// 保持 ComfyUI 原生排队流程；只跟踪本次返回的 prompt_id，不能把“队列空了”当作成功。
let capturing = false;
export async function interruptOwnedPrompt(api, owner) {
    const promptId = owner?._adH3PromptId;
    if (!promptId) return;
    const response = await api.fetchApi("/aiden_h3/interrupt_prompt", {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({prompt_id:promptId})
    });
    if (!response.ok) throw new Error("停止当前任务失败，请重试");
    if (owner._adH3PromptId === promptId) delete owner._adH3PromptId;
}
export async function executeQueueOnce(app, api, segmentId, {isStopped=()=>false, timeoutMs=3600000, pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)), serialized=null, owner=null, waitForCompletion=true}={}) {
    if (capturing) throw new Error("另一项任务正在提交，请稍后再试");
    if(isStopped()) return {stopped:true};
    let promptId;
    const original=api.queuePrompt;
    capturing=true;
    api.queuePrompt=async function(...args) {
        const result=await original.apply(this,args);
        if(args[1]?.output?.[String(segmentId)]) {
            promptId=result.prompt_id;
            if (owner) owner._adH3PromptId=promptId;
        }
        return result;
    };
    try {
        // 分段循环开始时已序列化本轮队列副本；切到其它工作流标签后继续使用它，
        // 不能再调用 app.queuePrompt（那会改用当前激活画布）。
        if (serialized) await api.queuePrompt(0, serialized);
        else await app.queuePrompt(0,1);
    }
    finally { api.queuePrompt=original;capturing=false; }
    if(!promptId) throw new Error("工作流未成功进入队列，请检查节点连线和输入参数");
    if(isStopped()) { if(owner) await interruptOwnedPrompt(api,owner); return {stopped:true,promptId}; }
    if(!waitForCompletion) return {promptId};
    const deadline=Date.now()+timeoutMs;
    while(Date.now()<deadline) {
        if(isStopped()) { if(owner) await interruptOwnedPrompt(api,owner); return {stopped:true,promptId}; }
        const response=await api.fetchApi("/history/"+encodeURIComponent(promptId));
        if(!response.ok) throw new Error("读取本次生成状态失败："+response.status);
        const history=await response.json(), job=history[promptId];
        if(job?.status && (job.status.completed || job.status.status_str==="error")) {
            if(job.status.status_str!=="success") {
                const error=job.status.messages?.find(item=>item[0]==="execution_error")?.[1];
                throw new Error(error?.exception_message||"本轮生成失败，当前段未完成");
            }
            if(owner?._adH3PromptId===promptId) delete owner._adH3PromptId;
            return {promptId,job};
        }
        await pause(400);
    }
    throw new Error("等待生成超时，当前段未完成；请核对活动任务");
}
