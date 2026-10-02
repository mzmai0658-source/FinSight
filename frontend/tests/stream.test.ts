import { afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { streamChat, registeredAssetId } from "../src/services/api";
import { useSessionStore } from "../src/stores/session";
import * as api from "../src/services/api";

function response(blocks: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({ start(controller) { blocks.forEach((block) => controller.enqueue(encoder.encode(block))); controller.close(); } }), { headers: { "content-type": "text/event-stream" } });
}
const result = { answer: { content: "完成", references: [], image: [] }, verification: { status: "pass", checks: [], unmatched_numbers: [] }, evidence: [], validation: {} };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });
describe("SSE terminal behavior", () => {
  it("parses CRLF split across network chunks and handles done once", async () => {
    const done = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([`event: done\r`, `\ndata: ${JSON.stringify({ result })}\r\n\r\n`, `event: done\ndata: ${JSON.stringify({ result })}\n\n`])));
    await streamChat({ question: "q" }, { onDone: done });
    expect(done).toHaveBeenCalledTimes(1);
  });
  it("rejects an error-only or truncated stream instead of silently succeeding", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(['event: error\ndata: {"message":"upstream failed","terminal":true}\n\n'])));
    await expect(streamChat({ question: "q" }, {})).rejects.toThrow("upstream failed");
  });
  it("keeps an interrupted task active until its durable status is known", async () => {
    setActivePinia(createPinia());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(['event: session\ndata: {"session_uid":"session-1"}\n\n', 'event: error\ndata: {"message":"连接失败","terminal":true}\n\n'])));
    const store = useSessionStore();
    const poll = vi.spyOn(store, "waitForTask").mockResolvedValue();
    await store.sendQuestion("收入是多少");
    expect(store.isStreaming).toBe(true);
    expect(store.streaming.holding).toBe(true);
    expect(store.messages).toHaveLength(0);
    expect(poll).toHaveBeenCalledOnce();
    expect(store.streaming.clientRequestId).toBeTruthy();
  });
  it("sends an owned task cancellation and waits for worker acknowledgement", async () => {
    setActivePinia(createPinia());
    const store = useSessionStore();
    store.streaming = { ...store.streaming, active: true, question: "q", taskId: "t", clientRequestId: "c" };
    const task = { version:3 as const, task_id:"t",client_request_id:"c",session_uid:"s",status:"running" as const,deadline:Date.now()+270000,saved:false };
    vi.spyOn(api,"getTask").mockResolvedValue(task);
    const cancel = vi.spyOn(api,"cancelTask").mockResolvedValue({...task,status:"cancelling"});
    vi.spyOn(store,"waitForTask").mockResolvedValue();
    await store.stopStreaming();
    expect(cancel).toHaveBeenCalledWith("t");
    expect(store.isStreaming).toBe(true);
    expect(store.streaming.cancelRequested).toBe(true);
    expect(store.messages).toHaveLength(0);
  });
  it("recovers the saved task by ID even when its question repeats an older turn", async () => {
    setActivePinia(createPinia());
    const store = useSessionStore();
    store.streaming = { ...store.streaming, active:true,holding:true,question:"q",taskId:"new-task" };
    const get = vi.spyOn(api,"getTask").mockResolvedValue({version:3,task_id:"new-task",client_request_id:"c",session_uid:"s",status:"completed",deadline:Date.now(),saved:true});
    vi.spyOn(api,"getSessionDetail").mockResolvedValue({sessionUid:"s",title:"q",messageCount:2,createdAt:"now",updatedAt:"now",messages:[{id:1,role:"user",content:"q",createdAt:"now"},{id:2,role:"assistant",content:"新结果",createdAt:"now"}]});
    vi.spyOn(store,"loadSessions").mockResolvedValue();
    await store.waitForTask();
    expect(get).toHaveBeenCalledWith("new-task");
    expect(store.messages[1].content).toBe("新结果");
    expect(store.isStreaming).toBe(false);
  });
  it("marks a done response that failed persistence as unsaved", async () => {
    setActivePinia(createPinia());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([`event: done\ndata: ${JSON.stringify({ result, persistence_status: "failed", error: "未能保存" })}\n\n`])));
    const store = useSessionStore(); vi.spyOn(store, "loadSessions").mockResolvedValue();
    await store.sendQuestion("q");
    expect(store.messages[1].metadata?.error).toBe("未能保存");
    expect(store.messages[1].metadata?.persistence_status).toBe("failed");
  });
  it("only accepts registered asset IDs or controlled API paths", () => {
    expect(registeredAssetId("/api/assets/0123456789abcdef#page=8")).toBe("0123456789abcdef");
    for (const path of ["../../.env", "file:///C:/secret", "https://example.org/file.pdf", "/api/assets/../secret"]) expect(registeredAssetId(path)).toBe("");
  });
  it("allows viewing another session without cancelling the background task", async () => {
    setActivePinia(createPinia());
    const store=useSessionStore();
    store.activeUid="old";
    store.streaming={...store.streaming,active:true,question:"旧问题",clientRequestId:"old-client",taskId:"old-task"};
    const cancel=vi.spyOn(api,"cancelTask");
    vi.spyOn(api,"getSessionDetail").mockResolvedValue({sessionUid:"new",title:"新会话",messageCount:0,createdAt:"now",updatedAt:"now",messages:[]});
    vi.spyOn(api,"getActiveTasks").mockResolvedValue([]);
    await store.openSession("new");
    expect(store.activeUid).toBe("new");
    expect(store.streaming.active).toBe(false);
    expect(cancel).not.toHaveBeenCalled();
    const turns=JSON.parse(localStorage.getItem("finsight-pending-turn:anonymous")!);
    expect(turns["old-client"].taskId).toBe("old-task");
  });
  it("ignores every late SSE update from a task whose session view was detached", async () => {
    setActivePinia(createPinia());
    const store=useSessionStore();
    let handlers: Parameters<typeof api.streamChat>[1] | undefined;
    let finish: (()=>void) | undefined;
    vi.spyOn(api,"streamChat").mockImplementation(async (_request,value) => {
      handlers=value;
      await new Promise<void>(resolve => { finish=resolve; });
    });
    vi.spyOn(store,"loadSessions").mockResolvedValue();
    const pending=store.sendQuestion("同仁堂营业收入");
    store.detachTaskView(); store.activeUid="other";
    handlers!.onSession?.({session_uid:"late-session",task_id:"late-task"});
    handlers!.onPlan?.({label:"迟到进度"});
    handlers!.onAnswerDelta?.({text:"迟到答案"});
    handlers!.onDone?.({result});
    finish!(); await pending;
    expect(store.activeUid).toBe("other");
    expect(store.messages).toEqual([]);
    expect(store.streaming.content).toBe("");
  });
  it("retains separate pending identities for multiple background sessions", () => {
    setActivePinia(createPinia());
    const store=useSessionStore();
    for (const [session,client] of [["first","c1"],["second","c2"]]) {
      store.activeUid=session;
      store.streaming={...store.streaming,active:true,question:"问题",clientRequestId:client};
      store.detachTaskView();
    }
    const turns=JSON.parse(localStorage.getItem("finsight-pending-turn:anonymous")!);
    expect(Object.keys(turns).sort()).toEqual(["c1","c2"]);
  });
  it("ends recovery when the server confirms no registration and preserves the exact retry ID", async () => {
    setActivePinia(createPinia());
    const store=useSessionStore();store.activeUid="session";
    store.streaming={...store.streaming,active:true,question:"请求未送达",clientRequestId:"unregistered-client"};
    store.rememberTask();
    const recover=vi.spyOn(api,"recoverTask").mockResolvedValue(null);
    await store.waitForTask();
    expect(recover).toHaveBeenCalledOnce();
    expect(store.isStreaming).toBe(false);
    expect(store.messages[1].retryClientRequestId).toBe("unregistered-client");
    expect(store.messages[1].content).toContain("尚未登记");
    expect(JSON.parse(localStorage.getItem("finsight-pending-turn:anonymous")!)["unregistered-client"]).toBeTruthy();
    const stream=vi.spyOn(api,"streamChat").mockResolvedValue();
    vi.spyOn(store,"waitForTask").mockResolvedValue();vi.spyOn(store,"loadSessions").mockResolvedValue();
    await store.retryMessage(1);
    expect(stream.mock.calls[0][0].clientRequestId).toBe("unregistered-client");
    expect(stream.mock.calls[0][0].sessionUid).toBe("session");
  });
  it("does not discard an unregistered request just because a session has no active task", async () => {
    setActivePinia(createPinia());
    const store=useSessionStore();
    localStorage.setItem("finsight-pending-turn:anonymous",JSON.stringify({c:{sessionUid:"session",question:"q",clientRequestId:"c"}}));
    vi.spyOn(api,"getSessionDetail").mockResolvedValue({sessionUid:"session",title:"q",messageCount:0,createdAt:"now",updatedAt:"now",messages:[]});
    vi.spyOn(api,"getActiveTasks").mockResolvedValue([]);vi.spyOn(api,"recoverTask").mockResolvedValue(null);
    vi.spyOn(store,"waitForTask").mockResolvedValue();
    await store.openSession("session");
    expect(store.streaming.clientRequestId).toBe("c");
    expect(JSON.parse(localStorage.getItem("finsight-pending-turn:anonymous")!).c).toBeTruthy();
  });
});
