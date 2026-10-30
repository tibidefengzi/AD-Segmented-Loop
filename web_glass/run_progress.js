// Canvas-only status: no widget, port, node size or workflow data changes.
const viewers = new Map();
let installed = false, currentPrompt = "";
const clamp = value => Math.max(0, Math.min(1, value));

export function mountNodeProgress(node) {
    if (node._adH3ProgressMounted) return;
    node._adH3ProgressMounted = true;
    const draw = node.onDrawForeground;
    node.onDrawForeground = function(ctx) {
        const result = draw?.apply(this, arguments);
        const p = this._adH3Progress;
        if (!ctx || !p || this.flags?.collapsed) return result;
        const width = Math.max(0, this.size[0] - 8);
        ctx.save();
        ctx.fillStyle = "#20362b"; ctx.fillRect(4, 0, width, 4);
        const color = p.phase === "error" ? "#ef6666" : p.phase === "paused" ? "#84948b"
            : p.phase === "review" ? "#dfbf62" : "#35d878";
        ctx.fillStyle = color;
        if (p.fraction === null) {
            for (let x = 4; x < width + 4; x += 16) ctx.fillRect(x, 0, Math.min(8, width + 4 - x), 4);
        } else ctx.fillRect(4, 0, width * clamp(p.fraction), 4);
        ctx.font = "11px 'Microsoft YaHei', sans-serif";
        ctx.textAlign = "right"; ctx.fillStyle = color;
        ctx.fillText(p.text || "", this.size[0] - 9, -7, Math.max(0, this.size[0] - 170));
        ctx.restore();
        return result;
    };
    const removed = node.onRemoved;
    node.onRemoved = function() { viewers.delete(this); return removed?.apply(this, arguments); };
}

function setProgress(node, progress) {
    if (!node) return;
    mountNodeProgress(node);
    node._adH3Progress = {...progress, fraction: Number.isFinite(progress.fraction) ? clamp(progress.fraction) : null};
    node.setDirtyCanvas?.(true, false);
}

export function setMaterialProgress(node, progress) {
    node._adH3LocalProgress = {busy: !!progress.busy};
    setProgress(node, progress);
}

export function updateSessionProgress(viewer, segment, queue, state) {
    const total = state.segments?.length || 0;
    if (!total) return;
    const index = Number(state.cursor) || 0;
    const done = state.segments.filter(row => row.valid && row.accepted).length;
    const busy = ["queued", "running", "merging"].includes(state.status);
    const names = {queued: "排队中", running: "处理中", merging: "合并中", review: "待审核",
        paused: "已停止", error: "失败", completed: "已完成", ready: "待运行"};
    const prefix = `第 ${index + 1}/${total} 段`;
    const promptId = state.execution?.prompt_id || "";
    const previous = viewers.get(viewer);
    const sampling = busy && state.status !== "merging" && promptId && previous?.promptId === promptId ? previous.sampling : null;
    const fraction = sampling ?? (state.status === "completed" ? 1 : busy ? null : state.segments[index]?.valid ? 1 : 0);
    setProgress(segment, {phase: state.status, fraction, text: sampling !== null
        ? `${prefix} · 执行 ${Math.round(sampling * 100)}%` : `${prefix} · ${names[state.status] || state.status}`});
    const summary = {phase: state.status, fraction: done / total,
        text: `完成 ${done}/${total} · ${names[state.status] || state.status}`};
    setProgress(viewer, summary);
    if (!queue?._adH3LocalProgress?.busy) setProgress(queue, summary);
    viewers.set(viewer, {segment, queue, promptId, busy, state, sampling});
}

export function installProgressEvents(api) {
    if (installed) return;
    installed = true;
    api.addEventListener("execution_start", event => { currentPrompt = event.detail?.prompt_id || ""; });
    api.addEventListener("progress", event => {
        const data = event.detail || {}, promptId = data.prompt_id || currentPrompt;
        if (!promptId || !(Number(data.max) > 0) || !Number.isFinite(Number(data.value))) return;
        for (const [viewer, group] of viewers) {
            if (!group.busy || group.promptId !== promptId) continue;
            group.sampling = clamp(Number(data.value) / Number(data.max));
            updateSessionProgress(viewer, group.segment, group.queue, group.state);
        }
    });
    for (const name of ["execution_error", "execution_interrupted", "execution_success"]) api.addEventListener(name, event => {
        const promptId = event.detail?.prompt_id || "";
        if (currentPrompt === promptId) currentPrompt = "";
        for (const [viewer, group] of viewers) {
            if (!promptId || group.promptId !== promptId) continue;
            group.busy = false; group.sampling = null;
            // Final completion/review comes from persisted session state, never socket success alone.
            if (name === "execution_success") continue;
            const phase = name === "execution_error" ? "error" : "paused";
            updateSessionProgress(viewer, group.segment, group.queue, {...group.state, status: phase});
        }
    });
}
