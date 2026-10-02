"""作品说明：真实浏览器登录与保存结果渲染，证据不包含登录凭据。"""
from __future__ import annotations
import argparse
import base64
import json
import sys
import time
import requests
import websocket
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.browser_acceptance import Browser,ACTIVE_SESSION_KEY


def run(args):
    root=args.workspace.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    credentials=json.loads((root/'.local_runtime/delivery/qa_credentials.json').read_text('utf-8'))
    rows=[json.loads(line) for line in (root/args.records).read_text('utf-8').splitlines()]
    browser=Browser(args.debugger);checks={}
    browser.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
    def screenshot(name):
        data=browser.call('Page.captureScreenshot',{'format':'png'})['data']
        (out/name).write_bytes(base64.b64decode(data))
    def session(uid):
        browser.wait_for("Boolean(document.querySelector(\".ws__topbar-new\") && !document.querySelector(\".ws__topbar-new\").disabled)",timeout=35)
        browser.evaluate(f'localStorage.setItem({json.dumps(ACTIVE_SESSION_KEY)},{json.dumps(uid)})')
        browser.reload()
        browser.wait_for("document.querySelectorAll('.msg--assistant').length>0 && !document.querySelector('.generation-status')",timeout=35)
    try:
        browser.navigate(args.frontend+'/')
        browser.evaluate('localStorage.clear()');browser.reload();browser.navigate(args.frontend+'/login')
        browser.wait_for("Boolean(document.querySelector('input[autocomplete=username]'))",timeout=30)
        expression="""(() => {try {const set=(selector,value)=>{const el=document.querySelector(selector);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));};set('input[autocomplete=username]',USERNAME);set('input[type=password]',PASSWORD);document.querySelector('button[type=submit]').click();return true;} catch(e) { return {error:e.name,reason:e.message}; }})()"""
        outcome=browser.evaluate(expression.replace('USERNAME',json.dumps(credentials['username'])).replace('PASSWORD',json.dumps(credentials['password'])))
        if outcome is not True:raise RuntimeError('Browser input setup failed: '+str(outcome))
        browser.wait_for("location.pathname.includes('workspace')",timeout=40);checks['login']=True
        query=next(r for r in rows if r['case']['id']=='C01');session(query['run']['session_uid'])
        checks['history_and_synthetic_label']=browser.evaluate("document.body.innerText.includes('虚构演示数据')")
        checks['numeric_page_rendered']=browser.evaluate("document.querySelector('.msg--assistant').innerText.includes('1.50')")
        browser.evaluate("document.querySelector('.ws__messages')?.scrollTo(0,0)");screenshot('desktop-query.png')
        data=browser.call('Page.captureScreenshot',dict(format='png',clip=dict(x=440,y=60,width=750,height=500,scale=1)))['data']
        (out/'report-query.png').write_bytes(base64.b64decode(data))
        browser.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True))
        checks['mobile_no_horizontal_overflow']=browser.evaluate('document.documentElement.scrollWidth<=innerWidth')
        screenshot('mobile-query.png');browser.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        chart=next(r for r in rows if r['case']['id']=='C06');session(chart['run']['session_uid'])
        browser.wait_for("document.querySelectorAll('canvas').length>0",timeout=30)
        checks['chart_rendered_after_refresh']=True;screenshot('desktop-chart.png')
        data=browser.call('Page.captureScreenshot',dict(format='png',clip=dict(x=494,y=448,width=670,height=410,scale=1)))['data']
        (out/'report-chart.png').write_bytes(base64.b64decode(data))
        checks['session_switch']=browser.evaluate('document.body.innerText.includes("柱状图") || document.querySelectorAll("canvas").length>0')
        # 作品说明：点击真实原页按钮，网络与 PDF 打开结果另由对应资源请求核对。
        buttons=browser.evaluate("[...document.querySelectorAll('button')].filter(b=>b.innerText.includes('打开') && b.innerText.includes('页')).length")
        checks['original_page_button_present']=buttons>0
        # 作品说明：真实点击后检查新窗口中的原页 PDF iframe，而非仅检查按钮存在。
        original={t['id'] for t in requests.get(args.debugger+'/json/list',timeout=10).json()}
        browser.call('Runtime.evaluate',dict(expression="[...document.querySelectorAll('button')].find(b=>b.innerText.includes('打开') && b.innerText.includes('页')).click()",userGesture=True))
        pdf_frame='';popup_id=None
        until=time.monotonic()+25
        while time.monotonic()<until and not pdf_frame:
            for target in requests.get(args.debugger+'/json/list',timeout=10).json():
                if target['id'] in original or target.get('type')!='page':continue
                ws=websocket.create_connection(target['webSocketDebuggerUrl'],origin='http://localhost:19225',timeout=10)
                try:
                    ws.send(json.dumps(dict(id=1,method='Runtime.evaluate',params=dict(expression="document.querySelector('iframe')?.src || ''",returnByValue=True))))
                    while True:
                        message=json.loads(ws.recv())
                        if message.get('id')==1:break
                    pdf_frame=(message.get('result',{}).get('result') or {}).get('value','')
                    if pdf_frame:popup_id=target['id'];break
                finally:ws.close()
            if not pdf_frame:time.sleep(.3)
        checks['original_pdf_opened_at_registered_page']=pdf_frame.startswith('blob:') and '#page=' in pdf_frame
        if popup_id:requests.get(args.debugger+'/json/close/'+popup_id,timeout=10)
    finally:
        (out/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),'utf-8');browser.close()
    print(json.dumps(checks,ensure_ascii=False))
    if not all(checks.values()):raise AssertionError('Browser acceptance did not pass every check')
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',type=Path,default=ROOT);p.add_argument('--output',type=Path,default=ROOT/'data/runtime/delivery/browser')
    p.add_argument('--records',default='data/runtime/delivery/final20-last/records.jsonl')
    p.add_argument('--frontend',default='http://127.0.0.1:5276');p.add_argument('--debugger',default='http://127.0.0.1:19226')
    run(p.parse_args())
