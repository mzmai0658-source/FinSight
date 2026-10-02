"""作品说明：通过真实 Java/SSE 接口发送认证请求，结果文件不包含凭据。"""
from __future__ import annotations
import json,secrets,time,uuid
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]

class NativeClient:
    def __init__(self,base='http://127.0.0.1:18080',credential=None):
        self.base=base;self.http=requests.Session()
        self.credential=Path(credential or ROOT/'.local_runtime/v3/qa_credentials.json')
        self.credential.parent.mkdir(parents=True,exist_ok=True)
        if self.credential.exists():
            stored=json.loads(self.credential.read_text(encoding='utf-8'))
            self.username,self.password=stored['username'],stored['password']
        else:
            self.username='qa_v3_'+uuid.uuid4().hex[:10];self.password=secrets.token_urlsafe(20)
            self.call('POST','/api/auth/register',json=dict(username=self.username,password=self.password,nickname='v3真实验收'),auth=False)
            self.credential.write_text(json.dumps(dict(username=self.username,password=self.password)),encoding='utf-8')
        self.login()

    def login(self):
        deadline=time.monotonic()+65
        while True:
            try:
                pair=self.call('POST','/api/auth/login',json=dict(username=self.username,password=self.password),auth=False)
                break
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code!=429 or time.monotonic()>=deadline:raise
                body=exc.response.json()
                if body.get('message')!='登录尝试过于频繁，请稍后再试':raise
                time.sleep(2)
        self.http.headers['Authorization']='Bearer '+pair['accessToken'];self.refresh=pair['refreshToken']

    def call(self,method,path,auth=True,**kwargs):
        response=self.http.request(method,self.base+path,timeout=kwargs.pop('timeout',12),**kwargs)
        if response.status_code==401 and auth:
            pair=self.call('POST','/api/auth/refresh',auth=False,json=dict(refreshToken=self.refresh))
            self.http.headers['Authorization']='Bearer '+pair['accessToken'];self.refresh=pair['refreshToken']
            response=self.http.request(method,self.base+path,timeout=12,**kwargs)
        response.raise_for_status()
        value=response.json()
        if value.get('code') not in {0,'0',200,'200'}:raise RuntimeError('API rejected request: '+str(value.get('message')))
        return value.get('data')

    def turn(self,question,uid=None,client_id=None,disconnect=False,cancel_after=None):
        started=time.monotonic();events=[];task_id=None
        body=dict(question=question,clientRequestId=client_id or uuid.uuid4().hex)
        if uid:body['sessionUid']=uid
        response=self.http.post(self.base+'/api/chat/stream',json=body,stream=True,timeout=(12,285))
        if response.status_code==401:
            self.login();response=self.http.post(self.base+'/api/chat/stream',json=body,stream=True,timeout=(12,285))
        admission_deadline=time.monotonic()+65
        while response.status_code==429 and time.monotonic()<admission_deadline:
            rejected=response.json()
            # 作品说明：未登记请求的有界重试保留原请求标识；配额耗尽与排队失败如实记录。
            if rejected.get('code')!=42900 or rejected.get('message')!='提问太快了，请稍等几秒再试':break
            response.close();time.sleep(2)
            response=self.http.post(self.base+'/api/chat/stream',json=body,stream=True,timeout=(12,285))
        response.raise_for_status();response.encoding='utf-8';kind='';payload=[]
        for line in response.iter_lines(decode_unicode=True,chunk_size=1):
            if line.startswith('event:'):kind=line[6:].strip()
            elif line.startswith('data:'):payload.append(line[5:].strip())
            elif not line and payload:
                value=json.loads('\n'.join(payload));payload=[];events.append(dict(type=kind,data=value))
                if kind=='session':
                    task_id=value['task_id'];uid=value['session_uid']
                    if cancel_after is not None:
                        time.sleep(cancel_after);self.call('POST','/api/chat/tasks/'+task_id+'/cancel');break
                    if disconnect:break
                if kind=='done':break
        response.close()
        if not task_id:raise RuntimeError('No task identity received')
        deadline=time.time()+280
        while time.time()<deadline:
            task=self.call('GET','/api/chat/tasks/'+task_id)
            if task['status'] in {'completed','cancelled','failed'}:break
            time.sleep(1)
        return dict(question=question,session_uid=uid,client_request_id=body['clientRequestId'],task_id=task_id,
            seconds=round(time.monotonic()-started,3),events=events,task=task)
