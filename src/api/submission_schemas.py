"""审核操作只接收Java验证过的管理员身份，不接收客户端的发布结果。"""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator


class IdentityConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    stock_code:str=Field(pattern=r'^\d{6}$')
    company:str=Field(min_length=2,max_length=80)
    year:int=Field(ge=1990,le=2100)
    period:Literal['FY','HY','Q1','Q3']
    aliases:list[str]=Field(default_factory=list,max_length=30)


class ReviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    decision:Literal['approve','reject']
    operator_id:int=Field(ge=1)
    identity:IdentityConfirmation|None=None
    reason:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def review_fields(self):
        if self.decision=='approve' and self.identity is None:raise ValueError('Confirm company identity')
        if self.decision=='reject' and not self.reason.strip():raise ValueError('Provide rejection reason')
        return self


class PublishRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    candidate_ids:list[str]=Field(min_length=1,max_length=200)
    operator_id:int=Field(ge=1)
    @model_validator(mode='after')
    def distinct(self):
        from src.etl.submission_store import valid_id
        if len(set(self.candidate_ids))!=len(self.candidate_ids):raise ValueError('Duplicate candidate')
        for id in self.candidate_ids:valid_id(id)
        return self
