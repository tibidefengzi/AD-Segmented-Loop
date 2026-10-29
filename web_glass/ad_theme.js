// AD-H3 统一深色主题：颜色、字号、控件尺寸只在这里定义一次。
// 素材清单库、视频预览、审核区共用同一套变量；后续其它插件可直接 import 本文件复用。
const STYLE_ID = "ad-h3-shared-theme";
const CSS = `
:root{
  --ad-bg:#2b2b2b;
  --ad-surface:#252525;
  --ad-surface-deep:#1e1e1e;
  --ad-surface-dark:#1b1b1b;
  --ad-screen:#0d0d0d;
  --ad-border:#454545;
  --ad-border-soft:#3a3a3a;
  --ad-text:#e6e9ef;
  --ad-text-dim:#aab3c2;
  --ad-text-mute:#8b95a3;
  --ad-accent:#3769c7;
  --ad-accent-hover:#2f5cb2;
  --ad-accent-border:#4d7ed8;
  --ad-accent-soft:#92b7ff;
  --ad-danger:#b45b61;
  --ad-danger-text:#ffb4b9;
  --ad-warn:#e8c26a;
  --ad-radius:6px;
  --ad-radius-sm:5px;
  --ad-control-h:32px;
  --ad-font:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;
  --ad-font-body:14px;
  --ad-font-timeline:15px;
  --ad-shadow:0 1px 3px rgba(0,0,0,.45);
}
.ad-h3-token-only{--ad-theme-ready:1}
`;

/** 注入共享主题样式；DOM 中已存在时直接跳过，重复导入不会叠加。 */
export function installAdTheme() {
    if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.append(style);
}

installAdTheme();
