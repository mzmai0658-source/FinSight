"""作品说明：真实财报保存结果的浏览器验收和报告截图，不产生额外问答成绩。"""
import argparse,base64,hashlib,json,sys,time
import requests,websocket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.browser_acceptance import Browser,ACTIVE_SESSION_KEY

def run(folder,frontend,debugger,output):
    rows=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    credentials=json.loads((folder/'private_credentials.json').read_text(encoding='utf-8'))
    from eval.v3_native_client import NativeClient
    client=NativeClient('http://127.0.0.1:19100',folder/'private_credentials.json')
    browser=Browser(debugger);checks={};output.mkdir(parents=True,exist_ok=True)
    def screenshot(name):
        raw=browser.call('Page.captureScreenshot',{'format':'png'})['data'];(output/name).write_bytes(base64.b64decode(raw))
    def figure(selector,name):
        box=browser.evaluate("(()=>{const r=document.querySelector("+json.dumps(selector)+").getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()")
        raw=browser.call('Page.captureScreenshot',{'format':'png','clip':box})['data'];(output/name).write_bytes(base64.b64decode(raw))
    def session(uid):
        browser.wait_for("Boolean(document.querySelector('.ws__topbar-new') && !document.querySelector('.ws__topbar-new').disabled)",timeout=35)
        sessions=client.call('GET','/api/chat/sessions');position=next(i for i,s in enumerate(sessions) if s['sessionUid']==uid)
        count=sessions[position]['messageCount']
        browser.wait_for("document.querySelectorAll('.ws__session-title').length>"+str(position),timeout=35)
        browser.evaluate("document.querySelectorAll('.ws__session-title')["+str(position)+"].click()")
        condition='localStorage.getItem('+json.dumps(ACTIVE_SESSION_KEY)+')==='+json.dumps(uid)+" && document.querySelectorAll('.msg').length==="+str(count)+" && !document.querySelector('.generation-status')"
        browser.wait_for(condition,timeout=35);browser.reload();browser.wait_for(condition,timeout=35)
    try:
        browser.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        browser.navigate(frontend+'/');browser.evaluate('localStorage.clear()');browser.reload();browser.navigate(frontend+'/login')
        browser.wait_for("Boolean(document.querySelector('input[autocomplete=username]'))",timeout=30)
        expression="""(()=>{const set=(s,v)=>{const e=document.querySelector(s);e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));};set('input[autocomplete=username]',USERNAME);set('input[type=password]',PASSWORD);document.querySelector('button[type=submit]').click();return true;})()"""
        browser.evaluate(expression.replace('USERNAME',json.dumps(credentials['username'])).replace('PASSWORD',json.dumps(credentials['password'])))
        browser.wait_for("location.pathname.includes('workspace')",timeout=40);checks['login']=True
        lookup=next(row for row in rows if row['case']['id']=='R001');session(lookup['run']['session_uid'])
        content=browser.evaluate("document.querySelector('.msg--assistant').innerText")
        checks['real_company_and_saved_table']=('万邦德' in content and '营业收入' in content and '亿元' in content)
        checks['no_synthetic_label']=not browser.evaluate("document.body.innerText.includes('虚构演示数据')")
        screenshot('real-desktop-query.png')
        figure('.msg--assistant','report-query.png')
        browser.call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True))
        checks['mobile_no_horizontal_overflow']=browser.evaluate('document.documentElement.scrollWidth<=innerWidth');screenshot('real-mobile-query.png')
        browser.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        session(lookup['run']['session_uid']);checks['refresh_restores_actual_session']=browser.evaluate("document.querySelectorAll('.msg--assistant').length>0")
        checks['original_page_button_present']=browser.evaluate("[...document.querySelectorAll('button')].some(b=>b.innerText.includes('打开') && b.innerText.includes('页'))")
        original={t['id'] for t in requests.get(debugger+'/json/list',timeout=10).json()}
        browser.call('Runtime.evaluate',dict(expression="[...document.querySelectorAll('button')].find(b=>b.innerText.includes('打开') && b.innerText.includes('页')).click()",userGesture=True))
        frame='';popup=None;until=time.monotonic()+25
        while time.monotonic()<until and not frame:
            for target in requests.get(debugger+'/json/list',timeout=10).json():
                if target['id'] in original or target.get('type')!='page':continue
                connection=websocket.create_connection(target['webSocketDebuggerUrl'],origin='http://localhost:19225',timeout=10)
                try:
                    connection.send(json.dumps(dict(id=1,method='Runtime.evaluate',params=dict(expression="document.querySelector('iframe')?.src || ''",returnByValue=True))))
                    while True:
                        message=json.loads(connection.recv())
                        if message.get('id')==1:break
                    frame=(message.get('result',{}).get('result') or {}).get('value','')
                    if frame:popup=target['id'];break
                finally:connection.close()
            if not frame:time.sleep(.3)
        facts=lookup['run']['task']['result']['facts'];source=facts[0]['source']
        checks['original_pdf_opened_at_registered_page']=frame.startswith('blob:') and '#page='+str(source['page']) in frame
        if popup:requests.get(debugger+'/json/close/'+popup,timeout=10)
        fact=facts[0];response=client.http.get(client.base+'/api/materials/financial/'+fact['stock_code']+'/'+str(fact['year'])+'/'+fact['period']+'/file',timeout=60)
        checks['served_pdf_sha256_matches_real_original']=response.status_code==200 and hashlib.sha256(response.content).hexdigest()==source['source_sha256']
        chart=next((row for row in rows if row['case']['category']=='图表' and row['score']['automated_pass'] and (row.get('run',{}).get('task',{}).get('result') or {}).get('chart_data_list')),None)
        if chart:
            session(chart['run']['session_uid']);browser.wait_for("document.querySelectorAll('canvas').length>0",timeout=30)
            checks['real_chart_after_session_switch']=True;screenshot('real-desktop-chart.png')
            figure('.chart-card','report-chart.png')
        (output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(checks,ensure_ascii=False))
        assert all(checks.values())
    except Exception:
        view=browser.evaluate('JSON.stringify({url:location.href,body:document.body.innerText})')
        (output/'browser-failure.json').write_text(view,encoding='utf-8')
        print(view[:1600]);raise
    finally:browser.close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',default='data/runtime/qa200/run0');p.add_argument('--frontend',default='http://127.0.0.1:5382');p.add_argument('--debugger',default='http://127.0.0.1:19226');p.add_argument('--output',default='docs/evidence/real200/browser');a=p.parse_args()
    run(ROOT/a.run,a.frontend,a.debugger,ROOT/a.output)
