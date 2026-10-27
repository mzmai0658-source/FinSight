"""数据抽取使用云端兼容协议，与问答共用配置和模型准入。"""
from __future__ import annotations
import json
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from loguru import logger

from src.agent.providers import resolve_chat_provider
from src.agent.model_admission import admitted
from src.agent.cloud_protocol import build_request, status_reason, tls_context, audit_start, audit_end


class TrustedAdapter(HTTPAdapter):
    """同时信任系统与certifi证书，保留证书链和主机名校验。"""
    def init_poolmanager(self,*args,**kwargs):
        kwargs['ssl_context']=tls_context()
        return super().init_poolmanager(*args,**kwargs)

    def proxy_manager_for(self,*args,**kwargs):
        kwargs['ssl_context']=tls_context()
        return super().proxy_manager_for(*args,**kwargs)


class LLMClient:
    def __init__(self,api_url=None,api_key=None,model=None):
        self.config=resolve_chat_provider(api_url=api_url,api_key=api_key,model=model)
        self.api_url=self.config.completions_url
        self.api_key=self.config.api_key
        self.model=self.config.model
        self.provider=self.config.provider
        self.timeout=self.config.timeout_seconds
        self.max_retries=self.config.max_retries
        self.retry_backoff_seconds=self.config.retry_backoff_seconds
        self.max_tokens=self.config.max_tokens
        self.headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key}
        self.last_diagnostics={}
        self._local=threading.local()

    @property
    def is_configured(self):return self.config.is_configured

    def describe(self):return {**self.config.describe(),'thinking':'disabled'}

    @staticmethod
    def _build_session():
        session=requests.Session()
        session.mount('https://',TrustedAdapter(pool_connections=4,pool_maxsize=4,max_retries=0))
        return session

    def _get_session(self):
        session=getattr(self._local,'session',None)
        if session is None:self._local.session=session=self._build_session()
        return session

    def _reset_session(self):
        session=getattr(self._local,'session',None)
        if session is not None:session.close()
        self._local.session=None

    def _fail(self,reason):
        self.last_diagnostics={'reason':reason}
        logger.warning('云端模型调用未完成：{}',reason)

    @admitted
    def _request_completion(self,payload):
        if not self.is_configured:
            self._fail('cloud_api_key_required');return None
        for attempt in range(self.max_retries+1):
            retry=False
            token=audit_start(self.model,'extraction')
            data=None
            try:
                response=self._get_session().post(self.api_url,json=payload,headers=self.headers,timeout=self.timeout)
                if response.status_code!=200:
                    self._fail(status_reason(response.status_code))
                    if response.status_code not in {408,429,500,502,503,504}:return None
                    retry=True
                else:
                    data=response.json()
                    if not isinstance(data,dict) or data.get('model')!=self.model:
                        self._fail('cloud_response_model_mismatch');return None
                    self.last_diagnostics={'model':data['model'],'usage':data.get('usage') or {},'attempts':attempt+1}
                    return data
            except requests.Timeout:
                self._fail('cloud_timeout');retry=True
            except requests.RequestException:
                self._fail('cloud_connection_failed');retry=True
            except (ValueError,TypeError):
                self._fail('cloud_invalid_response');return None
            finally:
                audit_end(token,model=data.get('model') if isinstance(data,dict) else None,
                    usage=data.get('usage') if isinstance(data,dict) else None,
                    error=None if isinstance(data,dict) and data.get('model')==self.model else self.last_diagnostics.get('reason'))
            if not retry or attempt>=self.max_retries:break
            self._reset_session();time.sleep(self.retry_backoff_seconds*(attempt+1))
        return None

    @staticmethod
    def _extract_message(result):
        choices=(result or {}).get('choices') or []
        if choices and isinstance(choices[0],dict):return choices[0].get('message')
        return None

    def chat(self,messages,temperature=0,*,json_mode=False):
        message=self._extract_message(self._request_completion(build_request(self.model,messages,self.max_tokens,json_mode=json_mode)))
        return message.get('content') if isinstance(message,dict) else None

    def chat_json(self,messages,temperature=0,max_parse_retries=1):
        payload=build_request(self.model,messages,self.max_tokens,json_mode=True)
        for _ in range(min(1,max(0,max_parse_retries))+1):
            message=self._extract_message(self._request_completion(payload))
            content=(message or {}).get('content') or ''
            if not content:return None
            try:
                def unique(pairs):
                    value={}
                    for key,item in pairs:
                        if key in value:raise ValueError('duplicate_json_field')
                        value[key]=item
                    return value
                result=json.loads(content,object_pairs_hook=unique)
                if isinstance(result,dict):return result
            except (ValueError,TypeError):pass
        self._fail('cloud_invalid_schema')
        return None

    def chat_with_tools(self,messages,tools,temperature=0,tool_choice='auto'):
        return self._extract_message(self._request_completion(build_request(self.model,messages,self.max_tokens,
            tools=tools,tool_choice=tool_choice)))

    @admitted
    def chat_stream(self,messages,temperature=0):
        if not self.is_configured:
            self._fail('cloud_api_key_required');return
        for attempt in range(self.max_retries+1):
            started=False;response=None
            try:
                response=self._get_session().post(self.api_url,
                    json=build_request(self.model,messages,self.max_tokens,stream=True),
                    headers=self.headers,timeout=self.timeout,stream=True)
                if response.status_code!=200:
                    self._fail(status_reason(response.status_code))
                    if response.status_code not in {408,429,500,502,503,504}:return
                    response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith('data:'):continue
                    raw=line[5:].strip()
                    if raw=='[DONE]':return
                    try:data=json.loads(raw)
                    except ValueError:
                        self._fail('cloud_invalid_stream');return
                    if data.get('model')!=self.model:
                        self._fail('cloud_response_model_mismatch');return
                    choices=data.get('choices') or []
                    if not choices:continue
                    content=(choices[0].get('delta') or {}).get('content')
                    if isinstance(content,str) and content:
                        started=True;yield content
                return
            except requests.Timeout:self._fail('cloud_timeout')
            except requests.RequestException:self._fail('cloud_connection_failed')
            finally:
                if response is not None:response.close()
            if started or attempt>=self.max_retries:return
            self._reset_session();time.sleep(self.retry_backoff_seconds*(attempt+1))
