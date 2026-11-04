// 保持 ComfyUI 原生排队流程；只跟踪本次返回的 prompt_id，不能把“队列空了”当作成功。
let capturing = false;
// 提交锁：只串行「提交 + 认领本次 prompt_id」这一小段，避免两个并发提交互认对方的 prompt_id；
// 等待 history 的过程不占锁，长时间生成不会阻塞其它提交。
let submitChain = Promise.resolve();
function runSubmitStage(task) {
    const run = submitChain.then(task, task);
    submitChain = run.then(() => {}, () => {});
    return run;
}
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
    if(isStopped()) return {stopped:true};
    let promptId;
    if (serialized) {
        // 直接使用这一次调用的返回值，不拦截共享API上的其它工作流提交。
        await runSubmitStage(async () => {
            const result=await api.queuePrompt(0,serialized);
            promptId=result?.prompt_id;
            if(owner&&promptId)owner._adH3PromptId=promptId;
        });
    } else {
        await runSubmitStage(async () => {
            if(capturing)throw new Error("另一项任务正在提交，请稍后再试");
            const original=api.queuePrompt,graph=app.graph,state=graph?.state;
            const task=graph?.extra?.ad_segmented_task?.id;
            let enabled=true;
            capturing=true;
            api.queuePrompt=async function(...args) {
                const submission=args[1],submittedTask=submission?.workflow?.extra?.ad_segmented_task?.id
                    ||submission?.output?.[String(segmentId)]?._meta?.ad_task_id;
                const matches=!!submission?.output?.[String(segmentId)]&&(task?submittedTask===task:app.graph===graph&&graph?.state===state);
                const result=await original.apply(this,args);
                if(enabled&&matches&&!promptId){promptId=result?.prompt_id;if(owner&&promptId)owner._adH3PromptId=promptId;}
                return result;
            };
            try{await app.queuePrompt(0,1);}
            finally{enabled=false;api.queuePrompt=original;capturing=false;}
        });
    }
    if(!promptId) throw new Error("工作流未能进入队列：请先确认没有任务正在运行、节点输入完整；详细原因见 ComfyUI 运行记录。");
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
    throw new Error("等待生成超时（默认 60 分钟）：当前段未完成。请先在 ComfyUI 队列查看活动任务，必要时点「停止」后重试。");
}
