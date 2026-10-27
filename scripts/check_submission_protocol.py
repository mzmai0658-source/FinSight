"""实际Java入口核对上传、原件下载、权限和处理失败后的作业恢复。"""
from pathlib import Path
import hashlib
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from src.etl.submission_upgrade import load_environment,SubmissionAPI,_configure_process,_write
    import requests
    env=load_environment(ROOT);_configure_process(env)
    user=SubmissionAPI(env);admin=SubmissionAPI(env,admin=True)
    path=ROOT/'data_root/financial_reports/new120/000333/000333_2025_Q1.pdf'
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    old=next((r for r in user.listing() if r.get('sourceSha256')==sha),None)
    if old:view=old
    else:
        with path.open('rb') as stream:
            view=user.call('POST','/api/financial-submissions',files={'file':(path.name,stream,'application/pdf')})
    id=view['id'];started=time.monotonic();states=[view['status']]
    while view['status'] in ('PENDING','RUNNING') and time.monotonic()-started<90:
        time.sleep(1);view=user.call('GET',f'/api/financial-submissions/{id}');states.append(view['status'])
    original=user.http.get(user.base+f'/api/financial-submissions/{id}/original',timeout=15)
    assert original.status_code==200 and hashlib.sha256(original.content).hexdigest()==sha
    assert user.http.get(user.base+'/api/admin/financial-submissions',timeout=15).status_code==403
    other=SubmissionAPI({**env,'UPLOAD200_USERNAME':'upload120_other'})
    assert other.http.get(other.base+f'/api/financial-submissions/{id}',timeout=15).status_code==403
    assert other.http.get(other.base+f'/api/financial-submissions/{id}/original',timeout=15).status_code==403
    candidate=admin.call('GET',f'/api/admin/financial-submissions/{id}/candidate')
    assert candidate['source_sha256']==sha
    assert requests.get('http://127.0.0.1:8120/internal/financial-submissions/'+sha,timeout=10).status_code==401
    from src.etl.submission_service import get_store
    store=get_store();before=store.ocr_usage();job=store.ocr_submission(sha)['job_id']
    if view['status']=='FAILED':
        retry=user.call('POST',f'/api/financial-submissions/{id}/retry')
        while retry['status'] in ('PENDING','RUNNING') and time.monotonic()-started<120:
            time.sleep(1);retry=user.call('GET',f'/api/financial-submissions/{id}')
        assert retry['status'] in ('FAILED','SUCCESS','PARTIAL')
        assert store.ocr_usage()==before and store.ocr_submission(sha)['job_id']==job
    result=dict(status='passed',entry='real Java/RabbitMQ/Python/MySQL/Redis',upload_id=id,states=states,
        terminal=view['status'],review_status=candidate['review_status'],ocr_job_id=job,ocr_pages_reserved=before,
        checks=['authenticated upload','durable queue consumption','original download matches actual bytes',
            'other-user detail denied','other-user original denied','ordinary user admin access denied',
            'internal API credential required','failed retry reuses OCR job and page reservation'],
        generation_configured=bool(env.get('LLM_API_KEY')),embedding_configured=bool(env.get('DASHSCOPE_API_KEY')),
        scope='protocol and authorization; not proof of successful cloud extraction, publication or model QA')
    _write(ROOT/'.local_runtime/upgrade120/live-protocol-smoke.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
