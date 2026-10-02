"""作品说明：验证实际 Vue 页面中的刷新、断网、会话切换与停止生成。"""
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient
from eval.browser_acceptance import Browser
from eval.v3_native_faults import terminal

def main():
    client=NativeClient(credential=ROOT/'.local_runtime/v3/lifecycle_credentials.json')
    credentials=json.loads(client.credential.read_text());b=Browser();records=[]
    output=ROOT/'data/runtime/v3/browser/lifecycle.json';output.parent.mkdir(parents=True,exist_ok=True)
    def record(name,passed,**evidence):
        records.append(dict(name=name,passed=bool(passed),evidence=evidence))
        output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(name=name,passed=bool(passed)),ensure_ascii=False),flush=True)
        assert passed,name
    def click(selector):
        b.evaluate('document.querySelector('+json.dumps(selector)+').scrollIntoView({block:"center",behavior:"instant"})');time.sleep(.2)
        point=b.evaluate('(()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()')
        b.call('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**point))
        b.call('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**point))
    def submit(question):
        b.wait_for('Boolean(document.querySelector("textarea") && !document.querySelector("textarea").disabled && !document.querySelector(".generation-status"))',timeout=40)
        b.evaluate('(()=>{const e=document.querySelector("textarea");e.value='+json.dumps(question)+';e.dispatchEvent(new Event("input",{bubbles:true}));return true})()')
        click('button[aria-label="发送问题"]')
        b.wait_for('Boolean(document.querySelector(".generation-status"))',timeout=20)
        return b.wait_for('(()=>{const k=Object.keys(localStorage).find(k=>k.startsWith("finsight-pending-turn:"));const a=k?Object.values(JSON.parse(localStorage.getItem(k))):[];const t=a.find(t=>t.question==='+json.dumps(question)+');return t?.taskId?{taskId:t.taskId,sessionUid:t.sessionUid,clientRequestId:t.clientRequestId}:false})()',timeout=25)
    try:
        b.call('Page.enable');b.call('Runtime.enable');b.call('Network.enable')
        b.navigate('http://127.0.0.1:5176/');b.evaluate('localStorage.removeItem("finsight-auth")');b.call('Page.reload')
        b.navigate('http://127.0.0.1:5176/login');b.wait_for('Boolean(document.querySelector("input[type=password]"))')
        b.evaluate('(()=>{const c='+json.dumps(credentials)+';for(const e of document.querySelectorAll("input")){e.value=e.type==="password"?c.password:c.username;e.dispatchEvent(new Event("input",{bubbles:true}));}document.querySelector("form").requestSubmit();return true})()')
        b.wait_for('Boolean(localStorage.getItem("finsight-auth"))');b.navigate('http://127.0.0.1:5176/workspace')
        # 作品说明：新建隔离会话，防止将旧任务或旧回答误认成本次结果；浏览器操作使用实际页面控件。
        b.wait_for('Boolean(document.querySelector(".ws__topbar-new") && !document.querySelector(".ws__topbar-new").disabled)')
        click('.ws__topbar-new')
        turn=submit('请查同仁堂2022、2023、2024年归母净利润，并逐年解释变化原因，分别给出对应原文依据。')
        before=client.call('GET','/api/chat/tasks/'+turn['taskId'])
        b.call('Page.reload');b.wait_for('Boolean(document.querySelector(".generation-status"))',timeout=35)
        pending=b.evaluate('(()=>{const k=Object.keys(localStorage).find(k=>k.startsWith("finsight-pending-turn:"));return Object.values(JSON.parse(localStorage.getItem(k))).map(t=>t.taskId)})()')
        record('refresh_active_task_identity',before['status'] in {'queued','running'} and turn['taskId'] in pending,task=turn)
        b.call('Network.emulateNetworkConditions',dict(offline=True,latency=0,downloadThroughput=-1,uploadThroughput=-1));time.sleep(1)
        b.call('Network.emulateNetworkConditions',dict(offline=False,latency=0,downloadThroughput=-1,uploadThroughput=-1))
        during=client.call('GET','/api/chat/tasks/'+turn['taskId'])
        record('temporary_disconnect_keeps_task',during['status'] in {'queued','running','completed'},task_id=turn['taskId'],status=during['status'])
        click('.ws__topbar-new')
        b.wait_for('localStorage.getItem("finsight-active-session")!=='+json.dumps(turn['sessionUid']))
        other_uid=b.evaluate('localStorage.getItem("finsight-active-session")')
        detached=client.call('GET','/api/chat/tasks/'+turn['taskId'])
        record('switch_session_keeps_task',other_uid!=turn['sessionUid'] and detached['status'] in {'running','queued','completed'},other_session=other_uid)
        complete=terminal(client,turn['taskId'])
        other=client.call('GET','/api/chat/sessions/'+other_uid)
        record('late_result_stays_in_origin_session',complete['saved'] and not other['messages'] and not b.evaluate('document.querySelectorAll(".msg--assistant").length'),task_id=turn['taskId'],status=complete['status'])
        b.evaluate('localStorage.setItem("finsight-active-session",'+json.dumps(turn['sessionUid'])+')');b.call('Page.reload')
        b.wait_for('document.querySelectorAll(".msg--assistant").length===1 && !document.querySelector(".generation-status")',timeout=35)
        record('origin_saved_result_recovers',True,task_id=turn['taskId'])
        stop=submit('请查万邦德2022、2023、2024年归母净利润，逐年解释变化原因并列出每年的原文。')
        click('button[aria-label="停止生成"]')
        cancelled=terminal(client,stop['taskId'])
        b.wait_for('!document.querySelector(".generation-status")',timeout=25)
        record('native_stop_button_cancels',cancelled['status']=='cancelled' and cancelled['saved'],task_id=stop['taskId'],status=cancelled['status'])
        detail=client.call('GET','/api/chat/sessions/'+stop['sessionUid'])
        last=detail['messages'][-1]
        record('cancelled_turn_has_no_financial_publish',not (last.get('metadata') or {}).get('facts') and not (last.get('metadata') or {}).get('chart_data_list'),task_id=stop['taskId'])
    finally:
        try:b.call('Network.emulateNetworkConditions',dict(offline=False,latency=0,downloadThroughput=-1,uploadThroughput=-1))
        finally:b.close()

if __name__=='__main__':main()
