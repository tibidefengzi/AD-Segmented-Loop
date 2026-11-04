// ComfyUI 优先使用 widget.width；旧工作流可能留下固定值，必须与节点宽度同步。
export const MIN_NODE_WIDTH = 760;   // 节点右下角自由缩放的宽度下限
const WIDTH_COMPACT = 680;           // 窄档：轨头收窄、缩略图变小
const WIDTH_WIDE = 1000;             // 宽档：播放器与缩略图放大

export function timelineOrigin(canvas) {
    const lane = canvas.querySelector(".h3-lane");
    const body = lane?.querySelector(".h3-body");
    const measured = lane && body ? lane.offsetLeft + body.offsetLeft : 0;
    if (measured > 0) return measured;
    // 节点在画布外/隐藏时 offsetLeft 为0；标签栏仍占74px，不能当作时间零点。
    return 74;
}

export function bindWidgetWidth(node, widget) {
    Object.defineProperty(widget, "width", {
        configurable: true,
        get: () => Number(node.size?.[0]) || 860,
        set: () => {}, // 忽略旧宽度恢复；宽度统一由节点外框决定。
    });
}

// 用节点外框宽度（=控件宽度）给三个档位打标记，CSS 只按 data-w 切换，不依赖浏览器窗口宽度。
export function widthBreakpoint(width) {
    const value = Number(width) || 0;
    if (!value) return "";
    return value < WIDTH_COMPACT ? "sm" : value >= WIDTH_WIDE ? "lg" : "md";
}

export function markWidthBreakpoint(root) {
    if (!root) return;
    const width = root.offsetWidth || root.clientWidth || 0;
    if (!width) return;
    const next = widthBreakpoint(width);
    if (next && root.dataset.w !== next) root.dataset.w = next;
}

// 自由缩放只保留一个宽度下限；超过下限的宽度、高度都由用户拖拽决定，不再被内容排版弹回。
export function clampNodeWidth(node, minimum = MIN_NODE_WIDTH) {
    const width = Number(node?.size?.[0]) || 0;
    if (!width || width >= minimum) return false;
    node.setSize([minimum, Math.max(120, Number(node.size?.[1]) || 0)]);
    return true;
}

// 两个预览控件按实际内容占位，不用外层固定高度反过来测内容。
// 预览节点自动高度的唯一入口：各分类切换/缩略图变化都只通知内容变高，由这里按 >2px 阈值统一收敛。
export function fitPreviewContent(node, root) {
    root.style.height = "auto";
    root.style.minHeight = "0";
    root.style.maxHeight = "none";
    let frame = 0;
    const schedule = () => {
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            if (!node.graph || !root.isConnected || !root.offsetWidth) return;
            if (document.fullscreenElement && root.contains(document.fullscreenElement)) return;
            // 旧工作流：保留用户保存的高度，不按内容强制改写（_adH3Configured 由 h3_loop 在 onConfigure 里置位）。
            if (node._adH3Configured) return;
            markWidthBreakpoint(root);
            clampNodeWidth(node);
            const size = node.computeSize?.();
            const height = Number(size?.[1]);
            if (Number.isFinite(height) && height > 0 && Math.abs(node.size[1]-height) > 2) {
                node.setSize([Math.max(MIN_NODE_WIDTH,node.size[0]),height]);
                node.setDirtyCanvas?.(true,true);
            }
        });
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    const previous = node.onRemoved;
    node.onRemoved = function() {
        observer.disconnect(); cancelAnimationFrame(frame);
        return previous?.apply(this,arguments);
    };
    schedule();
}
