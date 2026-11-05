// Nodes 2.0 / Vue 节点兼容守卫。
// AD-分段循环的自定义界面基于 LiteGraph canvas + DOM widget；ComfyUI 的 Nodes 2.0（Vue 节点）
// 渲染模式下这些界面不会显示，节点会退化成默认参数面板。这里检测并引导用户一键切回经典渲染。
export const AD_NODE_CLASSES = ["AidenH3Segment", "AidenH3AudioQueue", "AidenH3VideoPreview"];
export const AD_VUE_SETTING = "Comfy.VueNodes.Enabled";
const DISMISS_KEY = "ad_h3_nodes2_dismissed";

export function vueNodesEnabled(app, win = globalThis) {
  try {
    if (win?.LiteGraph?.vueNodesMode === true) return true;
  } catch (_error) {}
  try {
    const value = app?.ui?.settings?.getSettingValue?.(AD_VUE_SETTING);
    if (value === true) return true;
    if (value === false) return false;
  } catch (_error) {}
  try {
    const value = app?.extensionManager?.setting?.get?.(AD_VUE_SETTING);
    if (value === true) return true;
    if (value === false) return false;
  } catch (_error) {}
  try {
    const raw = win?.localStorage?.getItem?.(AD_VUE_SETTING);
    if (raw === "true") return true;
    if (raw === "false") return false;
  } catch (_error) {}
  return false;
}

export function pluginNodesPresent(graph) {
  try {
    return (graph?._nodes || []).some(node =>
      AD_NODE_CLASSES.includes(node?.comfyClass) || AD_NODE_CLASSES.includes(node?.type));
  } catch (_error) {
    return false;
  }
}

export function disableVueNodes(app, win = globalThis) {
  let ok = false;
  try {
    if (app?.ui?.settings?.setSettingValue) {
      app.ui.settings.setSettingValue(AD_VUE_SETTING, false);
      ok = true;
    }
  } catch (_error) {}
  try {
    if (app?.extensionManager?.setting?.set) {
      Promise.resolve(app.extensionManager.setting.set(AD_VUE_SETTING, false)).catch(() => {});
      ok = true;
    }
  } catch (_error) {}
  try {
    if (win?.LiteGraph) win.LiteGraph.vueNodesMode = false;
  } catch (_error) {}
  try {
    win?.localStorage?.setItem?.(AD_VUE_SETTING, "false");
  } catch (_error) {}
  return ok;
}

export function installVueNodesGuard({ app, win = globalThis } = {}) {
  if (!win || !app) return null;
  if (win.__adH3Nodes2Guard) return win.__adH3Nodes2Guard;
  const doc = win.document;
  if (!doc) return null;
  if (!doc.body) {
    // 扩展 setup 可能早于 body 挂载：稍后重试，避免守卫静默失效。
    if (!win.__adH3Nodes2GuardRetry) {
      let attempts = 0;
      win.__adH3Nodes2GuardRetry = win.setInterval(() => {
        attempts += 1;
        if (doc.body || attempts > 100) {
          win.clearInterval(win.__adH3Nodes2GuardRetry);
          win.__adH3Nodes2GuardRetry = null;
          installVueNodesGuard({ app, win });
        }
      }, 100);
    }
    return null;
  }

  const banner = doc.createElement("div");
  banner.id = "ad-h3-nodes2-guard";
  banner.setAttribute("role", "alertdialog");
  banner.style.cssText = [
    "position:fixed", "right:18px", "bottom:18px", "z-index:100000",
    "max-width:460px", "padding:14px 16px", "border-radius:12px",
    "background:#1b2430", "border:1px solid #f0a020",
    "box-shadow:0 10px 32px rgba(0,0,0,.55)",
    "color:#e9eef7", "font:13px/1.6 Inter,'Microsoft YaHei',sans-serif",
    "display:none"
  ].join(";");
  banner.innerHTML = `
    <div style="font-weight:600;font-size:14px;margin-bottom:6px;color:#ffd08a">AD-分段循环：检测到 Vue 节点渲染（Nodes 2.0）</div>
    <div style="margin-bottom:10px">该渲染模式不会显示本插件的自定义界面，节点会退化成默认参数面板。
    注意：设置页可能仍显示为关闭——App Builder（构型台）或旧页签会把渲染切到 Vue，以实际渲染模式为准。
    这不是插件数据丢失，切回经典节点显示即可恢复。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" data-ad-fix style="cursor:pointer;border:0;border-radius:8px;padding:7px 12px;background:#2f81f7;color:#fff;font-weight:600">切回经典节点显示并刷新</button>
      <button type="button" data-ad-later style="cursor:pointer;border:1px solid #445062;border-radius:8px;padding:7px 12px;background:transparent;color:#c8d3e2">暂不</button>
    </div>`;
  doc.body.appendChild(banner);

  const chip = doc.createElement("button");
  chip.type = "button";
  chip.textContent = "AD-分段循环：Nodes 2.0 已开启（点击修复界面）";
  chip.style.cssText = [
    "position:fixed", "right:18px", "bottom:18px", "z-index:100000",
    "cursor:pointer", "border:1px solid #f0a020", "border-radius:10px",
    "padding:8px 12px", "background:#2a2218", "color:#ffd08a",
    "font:12px/1.4 Inter,'Microsoft YaHei',sans-serif", "display:none"
  ].join(";");
  doc.body.appendChild(chip);

  const show = () => {
    let dismissed = false;
    try { dismissed = win.sessionStorage?.getItem(DISMISS_KEY) === "1"; } catch (_error) {}
    banner.style.display = dismissed ? "none" : "block";
    chip.style.display = dismissed ? "block" : "none";
  };
  banner.querySelector("[data-ad-fix]")?.addEventListener("click", () => {
    disableVueNodes(app, win);
    banner.innerHTML = `<div style="font-weight:600;color:#8ee6a8">已切回经典节点显示，正在刷新页面…</div>`;
    setTimeout(() => win.location?.reload?.(), 250);
  });
  banner.querySelector("[data-ad-later]")?.addEventListener("click", () => {
    try { win.sessionStorage?.setItem(DISMISS_KEY, "1"); } catch (_error) {}
    banner.style.display = "none";
    chip.style.display = "block";
  });
  chip.addEventListener("click", () => {
    try { win.sessionStorage?.removeItem(DISMISS_KEY); } catch (_error) {}
    banner.style.display = "block";
    chip.style.display = "none";
  });

  let autoFixed = false;
  let sawVue = false;
  const check = () => {
    try {
      const enabled = vueNodesEnabled(app, win);
      if (enabled) sawVue = true;
      // 一旦检测到 Vue 渲染，即使已自动把开关改回 false，页面仍处于 Vue 渲染，
      // 必须等用户刷新后才能恢复，因此提示要保留。
      if (!enabled && !sawVue) {
        banner.style.display = "none";
        chip.style.display = "none";
        return;
      }
      if (!pluginNodesPresent(app.graph)) return;
      // 设置页显示关闭但实际渲染仍是 Vue（App Builder/旧页签）时，直接按实际模式修复一次。
      if (enabled && !autoFixed) {
        autoFixed = true;
        try { disableVueNodes(app, win); } catch (_error) {}
        try { app.canvas?.setDirty?.(true, true); } catch (_error) {}
        try {
          const title = banner.querySelector("div");
          if (title) title.textContent = "AD-分段循环：已自动切回经典节点渲染，请点击下方按钮刷新恢复界面";
        } catch (_error) {}
      }
      if (banner.style.display !== "block" && chip.style.display !== "block") show();
    } catch (_error) {}
  };
  const timer = win.setInterval(check, 2000);
  win.setTimeout(check, 400);
  win.setTimeout(check, 1500);
  win.setTimeout(check, 4000);
  try { console.warn("[AD-分段循环] 检测到 Nodes 2.0（Vue 节点）。本插件需要经典节点显示；请点击右下角提示切回，或在设置中关闭 Comfy.VueNodes.Enabled。"); } catch (_error) {}

  const handle = { check, timer, banner, chip, dispose: () => win.clearInterval(timer) };
  win.__adH3Nodes2Guard = handle;
  return handle;
}
