"""同步抽取与异步问答共用云端参数、TLS与安全错误分类。"""
from __future__ import annotations

import ssl
import json
import os
import time
import uuid
from pathlib import Path
from threading import Lock
import certifi

JSON_INSTRUCTION='\n仅输出符合output_contract的JSON对象，不输出Markdown或额外说明。'
_AUDIT_LOCK=Lock()


def tls_context():
    context=ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def build_request(model,messages,max_tokens=3072,*,json_mode=False,stream=False,tools=None,tool_choice='auto'):
    roles={'human':'user','ai':'assistant'}
    prepared=[]
    for item in messages:
        if isinstance(item,dict):
            value=dict(item)
            value['role']=roles.get(value.get('role'),value.get('role'))
        else:
            role,content=item;value=dict(role=roles.get(role,role),content=content)
        prepared.append(value)
    if json_mode and not any(m.get('role') in {'system','user'} and 'json' in str(m.get('content','')).lower() for m in prepared):
        system=next((m for m in prepared if m['role']=='system'),None)
        if system is None:prepared.insert(0,dict(role='system',content=JSON_INSTRUCTION.strip()))
        else:system['content']=str(system.get('content',''))+JSON_INSTRUCTION
    body=dict(model=model,messages=prepared,temperature=0,max_tokens=min(3072,max_tokens),
        thinking={'type':'disabled'},stream=stream)
    if json_mode:body['response_format']={'type':'json_object'}
    if tools is not None:body.update(tools=tools,tool_choice=tool_choice)
    return body


def status_reason(status):
    if status in {401,402,403}:return 'cloud_authentication_or_balance_failed'
    if status==404:return 'cloud_model_or_endpoint_not_found'
    if status==429:return 'cloud_rate_limited'
    if status>=500:return 'cloud_service_unavailable'
    return 'cloud_service_request_failed'


def _audit(row):
    value=os.getenv('FINSIGHT_MODEL_AUDIT_PATH','').strip()
    if not value:return
    target=Path(value)
    with _AUDIT_LOCK:
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()


def audit_start(model,kind):
    if not os.getenv('FINSIGHT_MODEL_AUDIT_PATH','').strip():return None
    token=(uuid.uuid4().hex,time.monotonic())
    _audit(dict(event='start',id=token[0],at=time.time(),model=model,kind=kind))
    return token


def audit_end(token,*,model=None,usage=None,error=None):
    if token is None:return
    detail=dict(event='end',id=token[0],at=time.time(),seconds=round(time.monotonic()-token[1],4))
    if isinstance(model,str):detail['model']=model
    if isinstance(usage,dict):
        detail['usage']={key:value for key,value in usage.items() if key in {'prompt_tokens','completion_tokens','total_tokens'} and isinstance(value,int)}
    if error:detail['error']=error
    _audit(detail)
