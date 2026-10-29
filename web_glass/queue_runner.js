// 保持 ComfyUI 原生排队流程；只跟踪本次返回的 prompt_id，不能把“队列空了”当作成功。
let capturing = false;
export async function executeQueueOnce(app, api, segmentId, {isStopped=()=>false, timeoutMs=3600000, pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}) {
    if (capturing) throw new Error("另一项任务正在提交，请稍后再试");
    if(isStopped()) return {stopped:true};
    let promptId;
    const original=api.queuePrompt;
    capturing=true;
    api.queuePrompt=async function(...args) {
        const result=await original.apply(this,args);
        if(args[1]?.output?.[String(segmentId)]) promptId=result.prompt_id;
        return result;
    };
    try { await app.queuePrompt(0,1); }
    finally { api.queuePrompt=original;capturing=false; }
    if(!promptId) throw new Error("工作流未成功进入队列，请检查节点连线和输入参数");
    const deadline=Date.now()+timeoutMs;
    while(Date.now()<deadline) {
        if(isStopped()) return {stopped:true,promptId};
        const response=await api.fetchApi("/history/"+encodeURIComponent(promptId));
        if(!response.ok) throw new Error("读取本次生成状态失败："+response.status);
        const history=await response.json(), job=history[promptId];
        if(job?.status && (job.status.completed || job.status.status_str==="error")) {
            if(job.status.status_str!=="success") {
                const error=job.status.messages?.find(item=>item[0]==="execution_error")?.[1];
                throw new Error(error?.exception_message||"本轮生成失败，未批准或跳过当前段");
            }
            return {promptId,job};
        }
        await pause(400);
    }
    throw new Error("等待生成超时，当前段未批准；请核对活动任务");
}
