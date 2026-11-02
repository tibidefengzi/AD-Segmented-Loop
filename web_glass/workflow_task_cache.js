// 工作流任务标识缓存。
// 首次升级旧工作流时任务号只存在于内存：若用户在保存工作流前刷新/重开页面，
// 新页面会生成另一个任务并把旧会话判给其它任务（fork），旧进度将永久失联。
// 用 graph.id（保存工作流时持久化的 UUID）在浏览器本地缓存任务号，重新打开同一
// 未保存工作流时沿用原任务；工作流本身保存后仍以文件内的 extra 为准。
const PREFIX = "ad-segmented-task:";
const TASK_ID = /^[a-f0-9]{32}$/;

export function taskCacheKey(graphID) {
    return PREFIX + String(graphID || "");
}

export function loadCachedTask(graphID, path = "") {
    const id = String(graphID || "");
    if (!id) return null;
    try {
        const raw = localStorage.getItem(taskCacheKey(id));
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data || !TASK_ID.test(String(data.id || "")) || String(data.workflow_id || "") !== id) return null;
        // 同一 graph.id 的文件被复制到另一路径时不能共享任务，避免两份工作流串进度。
        if (String(data.path || "") !== String(path || "")) return null;
        return {id: String(data.id), workflow_id: id};
    } catch {
        return null;
    }
}

export function rememberTask(meta, path = "") {
    const graphID = String(meta?.workflow_id || "");
    if (!graphID || !TASK_ID.test(String(meta?.id || ""))) return false;
    try {
        localStorage.setItem(taskCacheKey(graphID), JSON.stringify({
            id: String(meta.id),
            workflow_id: graphID,
            path: String(path || ""),
        }));
        return true;
    } catch {
        return false;
    }
}

export function forgetTask(graphID) {
    const id = String(graphID || "");
    if (!id) return;
    try {
        localStorage.removeItem(taskCacheKey(id));
    } catch {
        // 隐私模式等不可写场景忽略：任务号仍可从已保存工作流恢复。
    }
}
