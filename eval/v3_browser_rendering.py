"""作品说明：通过真实登录页面只读检查 Vue 渲染、图表按需加载和原始 PDF。"""
import base64,json,sys,time
import requests,websocket
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.browser_acceptance import Browser

class ObservedBrowser(Browser):
    def __init__(self):
        super().__init__();self.pdf_network=[]

    def call(self,method,params=None):
        self.counter+=1
        self.ws.send(json.dumps({'id':self.counter,'method':method,'params':params or {}}))
        while True:
            event=json.loads(self.ws.recv())
            if event.get('method')=='Network.requestWillBeSent':
                request=event['params']['request']
                if '/file' in request.get('url',''):
                    from urllib.parse import urlsplit
                    url=urlsplit(request['url'])
                    self.pdf_network.append(dict(request_id=event['params']['requestId'],origin=url.scheme+'://'+url.netloc,request_path=url.path))
            if event.get('method')=='Network.loadingFailed':
                params=event['params']
                if any(item.get('request_id')==params['requestId'] for item in self.pdf_network):
                    self.pdf_network.append(dict(request_id=params['requestId'],error=params.get('errorText'),blocked_reason=params.get('blockedReason'),cors=params.get('corsErrorStatus')))
            if event.get('method')=='Network.responseReceived':
                response=event['params']['response']
                if '/file' in response.get('url','') or '/pdf' in response.get('url',''):
                    self.pdf_network.append(dict(path=response['url'].split('/api/',1)[-1],status=response['status'],mime=response.get('mimeType')))
            if event.get('id')==self.counter:
                if event.get('error'):raise RuntimeError('Browser command failed: '+method)
                return event.get('result') or {}


def main():
    folder=ROOT/'data/runtime/v3/browser';folder.mkdir(parents=True,exist_ok=True)
    credentials=json.loads((ROOT/'.local_runtime/v3/qa_credentials.json').read_text())
    rows=[json.loads(line) for line in (ROOT/'data/runtime/v3/regression/dev-13/records.jsonl').read_text().splitlines()]
    row=next(row for row in rows if (row['run']['task'].get('result') or {}).get('chart_data_list'))
    uid=row['run']['session_uid'];checks=[];b=ObservedBrowser()
    def record(name,passed,**evidence):
        checks.append(dict(name=name,passed=bool(passed),evidence=evidence))
        (folder/'rendering.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(name=name,passed=bool(passed)),ensure_ascii=False),flush=True)
        assert passed,name
    try:
        b.call('Page.enable');b.call('Runtime.enable');b.call('Network.enable')
        b.navigate('http://127.0.0.1:5176/')
        b.evaluate('localStorage.removeItem("finsight-auth")');b.call('Page.reload')
        b.navigate('http://127.0.0.1:5176/login')
        b.wait_for('Boolean(document.querySelector("input[type=password]"))')
        payload=json.dumps(credentials)
        b.evaluate('''(()=>{const c='''+payload+''';for(const el of document.querySelectorAll('input')){if(el.type==='password')el.value=c.password;else if(el.type==='text')el.value=c.username;el.dispatchEvent(new Event('input',{bubbles:true}));}document.querySelector('form').requestSubmit();return true})()''')
        b.wait_for('Boolean(localStorage.getItem("finsight-auth"))',timeout=30)
        record('native_login',True)
        b.evaluate('localStorage.setItem("finsight-active-session",'+json.dumps(uid)+')')
        b.navigate('http://127.0.0.1:5176/workspace')
        b.wait_for('Boolean(document.querySelector("canvas"))',timeout=45)
        record('lazy_echarts_native_render',b.evaluate('document.querySelectorAll("canvas").length')>0,session_uid=uid)
        for width,height,label in [(1440,1000,'desktop'),(390,844,'mobile')]:
            b.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=label=='mobile'))
            time.sleep(.3)
            dimensions=b.evaluate('({viewport:innerWidth,document:document.documentElement.scrollWidth})')
            record(label+'_no_horizontal_overflow',dimensions['document']<=dimensions['viewport'],dimensions=dimensions)
            (folder/(label+'.png')).write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))
        b.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        native=b.evaluate('''(async()=>{try{const a=JSON.parse(localStorage.getItem('finsight-auth'));const r=await fetch('/api/materials/financial/600085/2024/FY/file',{headers:{Authorization:'Bearer '+a.accessToken},signal:AbortSignal.timeout(12000)});const bytes=new Uint8Array(await r.arrayBuffer());return {status:r.status,type:r.headers.get('Content-Type'),bytes:bytes.length,signature:String.fromCharCode(...bytes.slice(0,5))}}catch(e){return {error:e.name}}})()''')
        record('native_browser_authenticated_file_fetch',native.get('signature')=='%PDF-',network=b.pdf_network,**native)
        b.wait_for('Boolean(document.querySelector(".msg__sources .registered-asset button"))')
        b.evaluate('(()=>{const open=window.open.bind(window);window.open=(...args)=>{window.__qaPdfPopup=open(...args);return window.__qaPdfPopup};return true})()')
        before_targets={t['targetId'] for t in b.call('Target.getTargets')['targetInfos']}
        b.evaluate("document.querySelector('.msg__sources .registered-asset button').scrollIntoView({block:'center',behavior:'instant'})")
        time.sleep(.3)
        point=b.evaluate('''(()=>{const button=document.querySelector('.msg__sources .registered-asset button');const rect=button.getBoundingClientRect();return {x:rect.x+rect.width/2,y:rect.y+rect.height/2}})()''')
        hit=b.evaluate('(()=>{const p='+json.dumps(point)+';const el=document.elementFromPoint(p.x,p.y);return {text:el?.textContent,tag:el?.tagName,isButton:el===document.querySelector(".msg__sources .registered-asset button")}})()')
        record('pdf_click_target_is_visible_button',hit['isButton'],hit=hit)
        b.call('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**point))
        b.call('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**point))
        # 作品说明：原件按钮在独立弹窗的 iframe 中打开 PDF，聊天页中的按钮本身没有改成链接。
        child_info=None
        for _ in range(30):
            child_info=next((t for t in b.call('Target.getTargets')['targetInfos'] if t['targetId'] not in before_targets and t['type']=='page'),None)
            if child_info:break
            time.sleep(.2)
        if child_info is None:
            controls=b.evaluate('[...document.querySelectorAll(".msg__sources .registered-asset button")].map(x=>({text:x.textContent,disabled:x.disabled}))')
            alerts=b.evaluate('[...document.querySelectorAll(".registered-asset [role=alert]")].map(x=>x.textContent)')
            record('authenticated_original_pdf_open',False,controls=controls,alerts=alerts,network=b.pdf_network)
        target=next(t for t in requests.get(b.debugger+'/json/list',timeout=10).json() if t['id']==child_info['targetId'])
        try:
            try:b.wait_for('Boolean(window.__qaPdfPopup && !window.__qaPdfPopup.closed && window.__qaPdfPopup.document.querySelector("iframe"))',timeout=65)
            except TimeoutError:
                shape=b.evaluate('(()=>{const w=window.__qaPdfPopup;return {closed:w?.closed,protocol:w?.location.protocol,title:w?.document.title,iframes:w?.document.querySelectorAll("iframe").length}})()')
                alerts=b.evaluate('[...document.querySelectorAll(".registered-asset [role=alert]")].map(x=>x.textContent)')
                controls=b.evaluate('[...document.querySelectorAll(".msg__sources .registered-asset button")].map(x=>({text:x.textContent,disabled:x.disabled}))')
                record('authenticated_original_pdf_open',False,popup_shape=shape,alerts=alerts,controls=controls,network=b.pdf_network)
                raise
            href=b.evaluate('window.__qaPdfPopup.document.querySelector("iframe").src')
            proof=b.evaluate('''(async()=>{const href='''+json.dumps(href)+''';const response=await fetch(href.split('#')[0]);const data=new Uint8Array(await response.arrayBuffer());return {type:response.headers.get('Content-Type'),signature:String.fromCharCode(...data.slice(0,5)),bytes:data.length,pageFragment:href.split('#')[1]}})()''')
        finally:requests.get(b.debugger+'/json/close/'+target['id'],timeout=5)
        record('authenticated_original_pdf_open',proof['signature']=='%PDF-' and proof['bytes']>1000,**proof)
        b.call('Page.reload');b.wait_for('Boolean(document.querySelector("canvas"))',timeout=45)
        record('refresh_saved_chart',True,session_uid=uid)
    finally:b.close()


if __name__=='__main__':main()
