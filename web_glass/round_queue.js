// 只处理提交副本；编辑器始终展示用户最新内容，不回写旧参数或旧连线。
export function installRoundQueue(api, app) {
    if (api._adRoundQueueInstalled) return;
    api._adRoundQueueInstalled = true;
    const original = api.queuePrompt;
    api.queuePrompt = async function(number, serialized, ...rest) {
        const bindings = (serialized?.workflow?.nodes || []).map(node=>node.properties?.ad_execution_round).filter(Boolean);
        const connected = Object.values(serialized?.output || {}).some(node=>node.class_type === "AidenH3Segment" && node.inputs?.queue);
        if (!connected && !bindings.length) return original.call(this, number, serialized, ...rest);
        const response = await api.fetchApi("/aiden_h3/round/prepare", {
            method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify({prompt:serialized.output, workflow:serialized.workflow, round_bindings:bindings, client_id:api.clientId})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "无法读取当前生成设置，请停止后检查");
        for (const round of result.rounds || []) {
            const node = app.graph?.getNodeById?.(Number(round.segment_node)) || app.graph?.getNodeById?.(round.segment_node);
            if (node && round.id) {
                node.properties ||= {};
                node.properties.ad_execution_round = round;
            }
        }
        return original.call(this, number, {...serialized, output:result.prompt, workflow:result.workflow || serialized.workflow}, ...rest);
    };
}
