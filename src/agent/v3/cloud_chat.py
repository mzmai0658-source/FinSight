"""云端JSON调用沿用整轮预算、取消传播和本地Schema核验。"""
from __future__ import annotations
import copy
from dataclasses import dataclass
from urllib.parse import urlparse
import httpx
from src.agent.cloud_protocol import build_request,status_reason,tls_context,audit_start,audit_end


class CloudProviderFailure(RuntimeError):
    def __init__(self, reason):
        self.reason=reason
        super().__init__(reason)


@dataclass(frozen=True)
class CloudResponse:
    content: str
    response_metadata: dict


def cloud_request(model, messages, max_tokens=3072):
    """JSON输出指令和关闭思考属于协议参数，不替换财务任务。"""
    return build_request(model,messages,max_tokens,json_mode=True)


class CloudChat:
    def __init__(self,config,*,transport=None):
        if not config.api_key:raise CloudProviderFailure('cloud_api_key_required')
        parsed=urlparse(config.base_url)
        if not parsed.hostname or parsed.username or parsed.password or (parsed.scheme!='https' and
                not (parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'})):
            raise CloudProviderFailure('cloud_endpoint_invalid')
        self.config=config
        self.transport=transport
        self.schema=None

    def bind(self,**kwargs):
        bound=copy.copy(self)
        bound.schema=kwargs.get('format')
        return bound

    async def _post(self,body):
        kind='title' if 'title' in (self.schema or {}).get('properties',{}) else 'structured'
        token=audit_start(self.config.model,kind)
        try:
            async with httpx.AsyncClient(timeout=min(60,self.config.timeout_seconds),verify=tls_context(),transport=self.transport) as client:
                response=await client.post(self.config.completions_url,json=body,
                    headers={'Authorization':'Bearer '+self.config.api_key})
                if response.status_code>=400:raise CloudProviderFailure(status_reason(response.status_code))
                data=response.json()
                audit_end(token,model=data.get('model') if isinstance(data,dict) else None,usage=data.get('usage') if isinstance(data,dict) else None)
                return data
        except BaseException as exc:
            audit_end(token,error=getattr(exc,'reason',type(exc).__name__))
            raise

    async def ainvoke(self,messages):
        body=cloud_request(self.config.model,messages,min(3072,max(1,self.config.max_tokens)))
        try:
            data=await self._post(body)
            if not isinstance(data,dict) or data.get('model')!=self.config.model:
                raise CloudProviderFailure('cloud_response_model_mismatch')
            choices=data.get('choices')
            if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
                raise CloudProviderFailure('cloud_invalid_response')
            content=choices[0].get('message',{}).get('content')
            if not isinstance(content,str):raise CloudProviderFailure('cloud_invalid_response')
            usage=data.get('usage') or {}
            return CloudResponse(content=content,response_metadata=dict(model=data['model'],
                prompt_eval_count=usage.get('prompt_tokens'),eval_count=usage.get('completion_tokens')))
        except CloudProviderFailure:
            raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError,AttributeError) as exc:
            raise CloudProviderFailure('cloud_connection_or_response_failed') from None
