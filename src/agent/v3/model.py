"""作品说明：Ollama异步调用共用准入控制和整轮预算，避免问答与后台请求争抢资源。"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from langchain_ollama import ChatOllama
from pydantic import BaseModel
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as WireValidationError

from src.agent.providers import resolve_chat_provider
from src.agent.model_admission import SCHEDULER, AdmissionFailure
from .model_wire import encode_schema,translate_data


class ModelFailure(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def sampling_schema(schema):
    """作品说明：采样合同移除展示性元数据，完整保留字段验证规则。"""
    if isinstance(schema,list): return [sampling_schema(item) for item in schema]
    if isinstance(schema,dict):
        return {key:sampling_schema(value) for key,value in schema.items() if key not in {'title','default','description'}}
    return schema


@dataclass
class Budget:
    deadline: float
    calls: int = 0
    timings: list[dict] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict, repr=False)
    finalization_reserve: float = 10.0

    def remaining(self) -> float:
        return self.deadline - time.time()

    def charge(self):
        if self.calls >= 6:
            raise ModelFailure('model_call_budget_exhausted')
        if self.remaining() - self.finalization_reserve < 2:
            raise ModelFailure('turn_deadline_exceeded')
        self.calls += 1


class AsyncModel:
    def __init__(self, factory=ChatOllama):
        config = resolve_chat_provider()
        if config.provider != 'ollama' or urlparse(config.base_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ModelFailure('local_ollama_required')
        self.model = factory(model=config.model, base_url=config.base_url.removesuffix('/v1'),
            temperature=0, reasoning=False, num_ctx=16384, num_predict=3072,
            client_kwargs={'timeout': 60}, async_client_kwargs={'timeout': 60})

    async def structured(self, schema: type[BaseModel], system: str, payload: dict, budget: Budget):
        try:
            return await self._structured_once(schema,system,payload,budget)
        except ModelFailure as failure:
            # 作品说明：整轮最多一次格式修正，计入同一调用预算；修正不更改用户输入，也不冒充用户澄清。
            if failure.reason!='model_invalid_schema' or budget.artifacts.get('proposal_repair_used'):raise
            budget.artifacts['proposal_repair_used']=True
            diagnostics=getattr(failure,'diagnostics',{}).get('format_validation',{})
            budget.artifacts.setdefault('format_repairs',[]).append(diagnostics)
            return await self._structured_once(schema,system+'\n上次输出违反类型约束。根据format_correction修正JSON结构；当前任务与原话保持不变。',
                {**payload,'format_correction':diagnostics},budget)

    async def _structured_once(self, schema: type[BaseModel], system: str, payload: dict, budget: Budget):
        budget.charge()
        try:
            await SCHEDULER.enter(budget.remaining()-budget.finalization_reserve)
        except AdmissionFailure as exc:
            raise ModelFailure(exc.reason) from exc
        started = time.monotonic()
        metadata = {}
        span_catalog={}
        expected_prefix=[]
        try:
            # 作品说明：使用可取消的异步连接，使停止任务能关闭模型请求并释放调用名额。
            async with asyncio.timeout(max(0.001,min(60, budget.remaining()-budget.finalization_reserve))):
                output_schema = sampling_schema(schema.model_json_schema())
                # 作品说明：生成合同与程序校验共用公司登记集合；未覆盖名称进入unknown_companies，不能编造可执行代码。
                if schema.__name__=='TurnPlan':
                    from .turn_transport import turn_schema
                    output_schema=turn_schema(output_schema,payload)
                if schema.__name__=='Explanations':
                    # 作品说明：叙述引用只能使用检索到的文档证据；财务事实编号不能替代叙述依据。
                    citation=output_schema['$defs']['Claim']['properties']['evidence_ids']
                    ids=[snippet['id'] for snippet in payload.get('snippets',[])]
                    if ids:citation['items']={'type':'string','enum':ids}
                    else:citation['maxItems']=0
                output_schema=encode_schema(output_schema)
                # 作品说明：采样约束与紧凑字段说明来自同一合同，并再次严格校验；用户原话放在合同和记忆之后，减少旧任务或默认值干扰。
                if schema.__name__=='TurnPlan':
                    from .turn_transport import turn_guide
                    contract=turn_guide(output_schema)
                else:contract=output_schema
                ordered={'output_contract':contract,**{key:value for key,value in payload.items() if key!='question'}}
                if 'question' in payload: ordered['question']=payload['question']
                response = await self.model.bind(format=output_schema).ainvoke([
                    ('system', system+'\n目标与对话关系的英文名称仅是内部代号；输出使用output_contract/schema中的中文枚举，其含义一一对应。'),
                    ('human', json.dumps(translate_data(ordered), ensure_ascii=False, default=str, separators=(',',':')))])
            metadata = {k:response.response_metadata.get(k) for k in ('model','prompt_eval_count','eval_count')}
            if not isinstance(response.content, str) or not response.content.strip():
                raise ModelFailure('model_empty_output')
            try:
                def unique_object(pairs):
                    result={}
                    for key,value in pairs:
                        if key in result:raise ValueError('Duplicate JSON field')
                        result[key]=value
                    return result
                raw=json.loads(response.content,object_pairs_hook=unique_object)
                if not isinstance(raw,dict):raise ValueError('The model response must be an object')
                Draft202012Validator(output_schema).validate(raw)
                raw=translate_data(raw,decode=True)
                if schema.__name__=='TurnPlan':
                    from .turn_transport import decode_turn
                    raw=decode_turn(raw,payload['question'])
                parsed=schema.model_validate(raw)
                if schema.__name__=='TurnPlan':
                    budget.artifacts.setdefault('structured_trace',[]).append(
                        dict(call=budget.calls,step=schema.__name__,proposal=parsed.model_dump(mode='json')))
                return parsed
            except (ValueError,WireValidationError) as exc:
                failure=ModelFailure('model_invalid_schema')
                failure.diagnostics={'format_validation':dict(step=schema.__name__,error_type=type(exc).__name__,
                    errors=exc.errors(include_input=False,include_context=False,include_url=False) if hasattr(exc,'errors') else [],
                    field_path=list(exc.absolute_path) if isinstance(exc,WireValidationError) else [])}
                raise failure from exc
        except TimeoutError as exc:
            raise ModelFailure('model_timeout') from exc
        except asyncio.CancelledError:
            raise
        except ModelFailure:
            raise
        except Exception as exc:
            # 作品说明：驱动异常可能携带私有地址或凭据，仅返回经过整理的错误类别。
            raise ModelFailure('model_connection_failed') from exc
        finally:
            budget.timings.append({'step': schema.__name__, 'seconds': round(time.monotonic() - started, 3), **metadata})
            SCHEDULER.leave()
