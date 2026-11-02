// 只读遍历；不修改工作流，不沿预览的媒体输入回到H3生成链。
export function previewSources(prompt, finalId) {
    const visited = new Set(), found = new Set();
    const visit = id => {
        id = String(id);
        if (visited.has(id)) return;
        visited.add(id);
        const node = prompt[id];
        if (!node) return;
        if (node.class_type === "AidenH3VideoPreview") { found.add(id); return; }
        for (const value of Object.values(node.inputs || {})) {
            if (Array.isArray(value) && value.length === 2 && Number.isInteger(value[1]) && prompt[String(value[0])]) visit(value[0]);
        }
    };
    visit(finalId);
    return [...found];
}

export function finalSubmission(plan, cursor, token) {
    const record = plan.records[cursor];
    if (!record) throw new Error("没有剩余加工项");
    const output = structuredClone(plan.prompt);
    output[plan.source_id].inputs.record = JSON.stringify(record);
    output[plan.final_id].inputs.source_record = JSON.stringify({...record, final_job_token: token,final_task_id:plan.task_id||record.final_task_id});
    // 避开round_queue：这里既无H3节点，也不携带原轮次绑定。
    return {output, workflow:{nodes:[],links:[],groups:[],extra:{},version:0.4}};
}

export function finalRecords(job, finalId, token) {
    if (job?.status?.status_str !== "success" || !job.status.completed) throw new Error("当前加工尚未成功完成");
    const records = job.outputs?.[String(finalId)]?.ad_final;
    if (!Array.isArray(records) || !records.length || records.some(record => record.source_record?.final_job_token !== token)) {
        throw new Error("最终保存结果与本次任务不匹配，未前移加工清单");
    }
    return records;
}
