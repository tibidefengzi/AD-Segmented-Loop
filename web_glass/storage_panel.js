// 存储信息不进入工作流状态或生成缓存身份。
let pendingUsage = null;
export function storageBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function mountStoragePanel(root, node, api, scheduleLayout, onClearAll) {
    const panel = document.createElement("div");
    panel.className = "h3-storage-panel";
    panel.setAttribute("aria-label", "素材占用与缓存清理");
    panel.innerHTML = `<div class="h3-storage-summary" role="status"><span data-storage-used>正在统计素材占用…</span><span data-storage-cache></span><span data-storage-auto></span></div><button type="button" data-storage-clear-sources title="清空素材准备里的全部图片/视频/音频（只从节点移除，不删除磁盘文件）">清空全部素材</button><button type="button" data-storage-clean>清理缓存</button><div data-storage-message hidden></div>`;
    root.querySelector(".h3-tl-inner").append(panel);
    const used = panel.querySelector("[data-storage-used]"), cache = panel.querySelector("[data-storage-cache]");
    const auto = panel.querySelector("[data-storage-auto]"), button = panel.querySelector("[data-storage-clean]");
    const clearSources = panel.querySelector("[data-storage-clear-sources]");
    const message = panel.querySelector("[data-storage-message]");
    let disposed = false, current = null, cleaning = false;
    const showMessage = text => { message.textContent = text; message.hidden = !text; scheduleLayout?.(40); };
    const fetchUsage = () => {
        if (!pendingUsage) pendingUsage = (async () => {
            const response = await api.fetchApi("/aiden_h3/storage");
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || "占用统计接口尚未就绪，请安全重启后使用");
            return data;
        })().finally(() => { pendingUsage = null; });
        return pendingUsage;
    };
    async function refresh() {
        try {
            const data = await fetchUsage();
            if (disposed) return data;
            current = data;
            used.textContent = `素材占用 ${storageBytes(data.used_bytes)} / ${storageBytes(data.limit_bytes)}`;
            cache.textContent = `可清理 ${storageBytes(data.cache_bytes)}`;
            auto.textContent = `启动自动清理：${["off", "0", "false", "none"].includes(String(data.auto_clean).toLowerCase()) ? "已关闭" : "已开启"}`;
            const warning = !!data.over_limit;
            root.classList.toggle("h3-storage-over-limit", warning);
            panel.classList.toggle("is-warning", warning);
            for (const entry of root.querySelectorAll('[data-load],[data-batch]')) entry.disabled = warning && data.enforce;
            panel.title = warning ? "已达到容量上限；新导入受限，已有素材与生成可继续使用" : "统计本插件导入素材、会话、衍生文件及帧缓存";
            button.disabled = cleaning || data.busy || !data.cache_bytes;
            button.title = data.busy ? "任务完成后可清理" : "只清理可重建缓存，保留原素材、结果与进度";
            if (clearSources) clearSources.disabled = !!data.busy;
            scheduleLayout?.(40);
            return data;
        } catch (error) {
            if (!disposed) { used.textContent = "素材占用统计暂不可用"; button.disabled = true; panel.title = error.message; }
            throw error;
        }
    }
    async function canImport(bytes = 0) {
        const data = await refresh();
        if (data.enforce && (data.over_limit || data.used_bytes + data.pending_bytes + bytes > data.limit_bytes)) {
            showMessage("导入将超过容量上限，请先清理缓存或整理素材。");
            return false;
        }
        return true;
    }
    if (clearSources) clearSources.onclick = () => {
        if (cleaning) return;
        if (typeof onClearAll === "function") onClearAll();
    };
    button.onclick = async () => {
        if (cleaning) return;
        cleaning = true; button.disabled = true;
        try {
            const options = confirmed => ({method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({confirmed})});
            let response = await api.fetchApi("/aiden_h3/storage/cleanup", options(false));
            let data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || "暂时无法清理");
            if (!data.files) { showMessage("暂无可清理缓存；原素材和生成结果均已保留。"); return; }
            if (!window.confirm(`清理 ${data.files} 个可重建缓存，预计释放 ${storageBytes(data.bytes)}？\n原素材、分段结果、成片和进度会保留。`)) return;
            response = await api.fetchApi("/aiden_h3/storage/cleanup", options(true));
            data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || "清理未完成");
            showMessage(`已清理 ${data.removed_files} 个缓存，释放 ${storageBytes(data.freed_bytes)}。`);
        } catch (error) { showMessage(error.message); }
        finally { cleaning = false; if (!disposed) void refresh().catch(() => {}); }
    };
    const timer = setInterval(() => { if (!document.hidden && root.isConnected) void refresh().catch(() => {}); }, 60000);
    void refresh().catch(() => {});
    return {refresh, canImport, dispose() { disposed = true; clearInterval(timer); }};
}
