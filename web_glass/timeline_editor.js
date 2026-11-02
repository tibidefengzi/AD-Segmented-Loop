import {createGroupMove} from "./selection_move.js?v=20261102-group-v64";
// CACHEBUST ref-link-preserve 20261025-053810
// cachebust 20261025-0440 segment-materials
// CACHEBUST 2026-10-25-cursor-timeline
// AD-H3 素材清单库：剪映式时间线编辑器（图片 / 视频 / 音频统一管理，片段切分驱动循环）。
import "./ad_theme.js?v=20261102-group-v64";
import { mountGlassTimeline, matchesSource, previewSource, clearSourcePreview } from "./glass_ui.js?v=20261102-group-v64";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { segmentBounds, segmentIntervals, cutTimelineAt, topVideoAt, framePlan, prependEmptyLane, playbackRange, imageOnlyClips, followsImageCount, clearImageSources, repairSourceInstances, retainedSourceBounds, trackDisabled, activeClips, selectionPlaybackRange } from "./timeline_state.js?v=20261102-group-v64";
import { mountStoragePanel } from "./storage_panel.js?v=20261102-group-v64";
import {generationClips, commitGeneration, changeGeneration, deleteGeneration} from "./generation_edit.js?v=20261102-group-v64";
import { bindWidgetWidth, timelineOrigin, markWidthBreakpoint, MIN_NODE_WIDTH } from "./dom_layout.js?v=20261102-group-v64";
import { PreviewCache } from "./timeline_performance.js?v=20261102-group-v64";
import { setMaterialProgress } from "./run_progress.js?v=20261102-group-v64";

const FPS = 24, FRAME = 1 / FPS;
const PORT_LIMITS = { image: 9, video: 3, audio: 3 };  // H3 参考输出口数量（图片 9，视频/音频各 3）
const MAX_LANES = 3;  // 视频三轨、音频三轨，合计六轨
let activeEditorRoot = null;  // 最近被点击的时间线编辑器；Delete/撤销等快捷键只作用于它
const uid = (prefix = "id") => `${prefix}-${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
const snapFrame = t => Math.round(t * FPS) / FPS;
const clipEnd = clip => clip.tl_start + (clip.src_out - clip.src_in);
const fmtClock = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
const mediaURL = source => {const view=source?.crop_target && source.preview?.file ? source.preview : source;return `/aiden_h3/media/${view.origin}/${String(view.file).replaceAll("\\", "/").split("/").map(encodeURIComponent).join("/")}`;};

const style = document.createElement("style");
style.textContent = `
.aiden-h3.h3-tl{display:block;pointer-events:auto;padding:0;width:100%;max-width:100%;min-width:0;height:auto;box-sizing:border-box;overflow:hidden;font-family:var(--ad-font);font-size:var(--ad-font-timeline);line-height:1.5;color:var(--ad-text)}
.h3-tl .h3-tl-inner{display:flex;flex-direction:column;gap:6px;padding:10px 12px 12px;width:100%;max-width:100%;height:auto;box-sizing:border-box;overflow:visible;min-height:min-content}
.h3-tl .h3-tlbar{margin-bottom:1px}
.h3-tl .h3-tools{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.h3-tl .h3-tlbar{display:flex;flex-wrap:nowrap!important;align-items:center;gap:6px;font-size:14px;overflow:hidden}
.h3-tl .h3-tlbar button,.h3-tl .h3-tlbar input,.h3-tl .h3-tlbar select{padding:2px 8px;white-space:nowrap}
.h3-tl .h3-tlbar label{font-size:14px;white-space:nowrap}
.h3-tl .h3-group{display:inline-flex;align-items:center;gap:4px;flex-wrap:nowrap!important;padding:2px 6px 2px 0}
.h3-tl .h3-group+.h3-group{padding-left:8px;border-left:1px solid var(--ad-border-soft)}
.h3-tl .h3-split-group{gap:6px}
.h3-tl .h3-split-group label{display:inline-flex;align-items:center;gap:5px}
.h3-tl .h3-split-group input{width:76px;min-width:76px;flex:0 0 auto;box-sizing:border-box;padding-left:6px;padding-right:2px}
.h3-tl .h3-step-btn{width:20px;height:24px;padding:0;line-height:1;border-radius:4px;border:1px solid #475569;background:#334155;color:#cbd5e1;font:600 14px/1 system-ui,sans-serif;cursor:pointer}
.h3-tl .h3-step-btn:hover{background:#3B7DC4;border-color:#4A80BF;color:#fff}
.h3-tl .h3-step-btn:active{background:#2f6aa8}
.h3-tl .h3-split-group [data-count]{width:44px}
.h3-tl .h3-split-group [data-every]{appearance:textfield;-moz-appearance:textfield}
.h3-tl .h3-split-group [data-every]::-webkit-outer-spin-button,.h3-tl .h3-split-group [data-every]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.h3-smart-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;width:100%;padding:4px 0 6px;color:var(--ad-text-dim)}
.h3-smart-row select,.h3-smart-row input{background:#1b2433;border:1px solid #3d5a80;color:#dbe6f5;border-radius:6px;padding:3px 6px;font-size:13px}
.h3-smart-row input[type=number]{width:58px}
 .h3-smart-row button{white-space:nowrap}
.h3-tl .h3-smart-row .h3-vocals-toggle{background:#1b2433;border-color:#3d5a80;color:#9fb0c7;font-weight:600;white-space:nowrap}
.h3-tl .h3-smart-row .h3-vocals-toggle:hover{border-color:#5b9bff;color:#dbe6f5}
.h3-tl .h3-smart-row .h3-vocals-toggle.on{background:#2f6fd0!important;border-color:#5b9bff!important;color:#fff!important;box-shadow:0 0 8px rgba(91,155,255,.55)}
.h3-smart-info{margin-left:auto;color:#FFD166;font-weight:600;white-space:nowrap}
.h3-smartcut{position:absolute;top:0;bottom:0;width:2px;background:#FFD166;z-index:7;cursor:col-resize}
.h3-smartcut::before{content:"";position:absolute;top:0;left:-4px;width:10px;height:8px;background:#FFD166;border-radius:2px}
.h3-tl .h3-recipe-meta{display:flex;flex-wrap:wrap;align-items:center;gap:10px;color:var(--ad-text-dim);font-size:13px;width:100%;padding:2px 0 5px;margin:0}
.h3-tl .h3-recipe-preview{color:var(--ad-text-dim);font-size:13px}
.h3-tl button,.h3-tl select,.h3-tl input{font:inherit;height:var(--ad-control-h);min-height:var(--ad-control-h);padding:0 8px;border-radius:var(--ad-radius-sm);border:1px solid var(--ad-border);background:var(--ad-surface-deep);color:var(--ad-text);line-height:1.2}
.h3-tl button{cursor:pointer}.h3-tl button:hover{background:#333}
.h3-tl button.primary{background:var(--ad-accent);border-color:var(--ad-accent-border);color:#fff}.h3-tl button.primary:hover{background:var(--ad-accent-hover);color:#fff}
.h3-tl button:disabled{opacity:.45;cursor:default}
.h3-tl button.on{background:#3B7DC4;border-color:#4A80BF;color:#fff}
.h3-tl .h3-unit{display:inline-flex}.h3-tl .h3-unit button,.h3-tl .h3-tlbar .h3-unit button{border-radius:0;background:#334155!important;border-color:#475569!important;color:#cbd5e1!important;font-weight:600}.h3-tl .h3-unit button.on,.h3-tl .h3-tlbar .h3-unit button.on{background:#3B7DC4!important;border-color:#4A80BF!important;color:#fff!important;font-weight:700}.h3-tl .h3-unit button:first-child{border-radius:5px 0 0 5px}.h3-tl .h3-unit button:last-child{border-radius:0 5px 5px 0}
.h3-tl .h3-srcs{display:flex;flex-wrap:wrap;gap:6px}
.h3-tl .h3-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid var(--ad-border);border-radius:14px;background:var(--ad-surface-deep);font-size:14px}
.h3-tl .h3-chip .kind{color:var(--ad-text-dim)}.h3-tl .h3-chip button{min-height:24px;padding:0 7px;border-radius:10px;font-size:13px}
.h3-tl .h3-view{position:relative;width:100%;height:auto;min-height:138px;max-height:520px;overflow-x:auto;overflow-y:auto;background:var(--ad-surface);border:1px solid var(--ad-border-soft);border-radius:6px;user-select:none;touch-action:none;flex:0 0 auto}
.h3-tl .h3-canvas{position:relative;min-width:100%;padding:0} /* 有音视频时再由 JS 加底边空位 */
.h3-tl .h3-blank-above{height:54px;flex:none} /* 显隐只由 JS 控制，有音视频时显示上端空轨 */
.h3-tl .h3-ruler{position:relative;height:30px;background:var(--ad-surface-dark);border-bottom:1px solid var(--ad-border-soft);cursor:pointer}
.h3-tl .h3-tick{position:absolute;bottom:0;width:1px;height:9px;background:var(--ad-text-mute)}.h3-tl .h3-tick.major{height:15px;background:var(--ad-text-dim)}
.h3-tl .h3-tick span{position:absolute;left:4px;bottom:8px;font-size:12px;color:var(--ad-text-dim);white-space:nowrap}
.h3-tl .h3-lane{display:flex;align-items:stretch;height:54px;border-bottom:1px solid var(--ad-border-soft);position:relative;z-index:1}
.h3-tl .h3-head{flex:0 0 74px;display:flex;align-items:center;gap:5px;padding:0 8px;font-size:14px;color:var(--ad-text-dim);background:var(--ad-surface-deep);border-right:1px solid var(--ad-border-soft);z-index:2}
.h3-tl .h3-body{position:relative;flex:1;min-width:160px}
.h3-tl .h3-clip{position:absolute;top:5px;bottom:5px;border-radius:5px;overflow:hidden;cursor:grab;border:1px solid transparent}
.h3-tl .h3-clip.v{background:#0e3f43}.h3-tl .h3-clip.a{background:#17335e}
.h3-tl .h3-clip .label{position:absolute;left:6px;top:3px;right:6px;font-size:12px;color:var(--ad-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;text-shadow:0 1px 2px #000;z-index:1}
.h3-tl .h3-clip canvas{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.h3-tl .h3-clip.v canvas{opacity:.85}.h3-tl .h3-clip.a canvas{opacity:.55}
.h3-tl .h3-clip .edge{position:absolute;top:0;bottom:0;width:7px;cursor:ew-resize}
.h3-tl .h3-clip .edge.l{left:0}.h3-tl .h3-clip .edge.r{right:0}
.h3-tl .h3-clip.v,.h3-tl .h3-clip.a{border:2px solid #2a5f78;box-shadow:none}
.h3-tl .h3-clip.selected{border-color:#49baff;box-shadow:0 0 0 1px #49baff inset,0 0 6px #49baff66}
.h3-tl .h3-playhead{position:absolute;top:0;bottom:0;width:1px;background:#fff;z-index:6;cursor:ew-resize}
.h3-tl .h3-playhead::before{content:"";position:absolute;top:0;left:-4px;width:9px;height:8px;background:#fff;border-radius:2px}
.h3-tl .h3-playhead::after{content:"";position:absolute;top:0;bottom:0;left:-5px;width:12px}
.h3-tl .h3-segshade{display:none!important}
.h3-tl .h3-marquee{position:absolute;z-index:8;pointer-events:none;border:1px solid #49baff;background:rgba(73,186,255,.18);border-radius:2px}
.h3-tl .h3-player[hidden]{display:none!important}
.h3-tl .h3-player{display:flex;flex-direction:column;width:100%;background:#000;border:1px solid var(--ad-border);border-radius:8px;overflow:hidden}
.h3-tl .h3-player-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:6px 10px;font-size:14px;color:var(--ad-text-dim);background:var(--ad-surface-dark)}
.h3-tl .h3-player-head [data-player-title]{min-width:0;flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.h3-tl .h3-player-head [data-player-meta]{flex:0 0 auto;white-space:nowrap}
.h3-tl .h3-player .screen{position:relative;width:100%;height:260px;max-height:260px;background:#000}
.h3-tl .h3-player.is-empty .screen{height:160px;max-height:160px}
/* !important：ComfyUI 与其它扩展可能注入全局 video 规则（如 max-height/object-fit），必须压住。
   contain=按视频原比例放大到贴住窗口边框，不裁切画面（与剪映一致），黑边留在窗口内。 */
.h3-tl .h3-player video{width:100%!important;height:100%!important;max-height:none!important;object-fit:contain!important;display:block}

.h3-tl .h3-player .ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center;color:#5b6572;font-size:14px;background:#0d0d0d;z-index:1;padding:16px;box-sizing:border-box}.h3-tl .h3-player .ph [data-player-ph-text]{pointer-events:none}.h3-tl .h3-player .ph button.primary{pointer-events:auto;min-height:34px;padding:6px 18px;color:#fff!important}
.h3-tl .h3-frame-plan{font-size:13px;border:1px solid var(--ad-border);border-radius:5px;padding:7px;height:auto;max-height:160px;overflow:auto;flex:0 0 auto;box-sizing:border-box}.h3-tl .h3-frame-toggle{cursor:pointer;background:transparent;border:1px solid var(--ad-border);color:var(--ad-text-dim);border-radius:6px;padding:2px 10px;margin:2px 0;font:13px var(--ad-font)}
.h3-tl .h3-frame-plan.collapsed [data-frame-plan-note],.h3-tl .h3-frame-plan.collapsed [data-frame-plan-body]{display:none}
.h3-tl .h3-frame-plan-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:4px}.h3-tl .h3-frame-plan-head b{font-weight:600}.h3-tl .h3-max-frames-label{font-size:13px;color:var(--ad-text);white-space:nowrap}.h3-tl .h3-max-frames-input{width:88px;min-height:var(--ad-control-h)!important;height:var(--ad-control-h);padding:2px 8px!important;font-size:14px!important}.h3-tl .h3-frame-plan table{width:100%;border-collapse:collapse}.h3-tl .h3-frame-plan td,.h3-tl .h3-frame-plan th{text-align:left;padding:3px 7px;border-top:1px solid var(--ad-border-soft)}.h3-tl .h3-frame-plan .error{color:var(--ad-danger-text)}.h3-tl .h3-generation-clip{position:absolute;top:6px;bottom:6px;background:#344253;border:2px solid #2a5f78;border-radius:4px;padding:3px 10px;overflow:hidden;white-space:nowrap;cursor:grab;font-size:13px;user-select:none}.h3-tl .h3-generation-clip .edge{position:absolute;top:0;bottom:0;width:8px;cursor:ew-resize;z-index:2}.h3-tl .h3-generation-clip .edge.l{left:0}.h3-tl .h3-generation-clip .edge.r{right:0}.h3-tl .h3-generation-clip.selected{border-color:#49baff;box-shadow:0 0 0 1px #49baff inset,0 0 6px #49baff66}.h3-tl .h3-generation-clip.dragging{cursor:grabbing;opacity:.92}
.h3-tl .h3-lane-empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--ad-text-mute);font-size:14px}
.h3-tl .h3-path-field{display:flex;align-items:center;gap:6px;flex:1 1 320px;min-width:240px;height:30px;background:#2E5E96;border:1px solid #4A80BF;border-radius:6px;padding:0 6px 0 10px}
.h3-tl .h3-path-label{color:#fff;font:600 13px system-ui,'Microsoft YaHei',sans-serif;white-space:nowrap;cursor:pointer}
.h3-tl .h3-path-field input{color:#fff}
.h3-tl .h3-path-field input::placeholder{color:#bfdbfe}
.h3-tl .h3-path-field input{flex:1;min-width:80px;background:transparent;border:none;outline:none;color:var(--ad-text);font:13px system-ui,'Microsoft YaHei',sans-serif}
.h3-tl .h3-path-btn{background:#f4c542;color:#2b2b2b;border:none;border-radius:6px;padding:6px 12px;font:600 13px system-ui,'Microsoft YaHei',sans-serif;cursor:pointer}
.h3-tl .h3-path-input{flex:1 1 240px;min-width:160px;background:var(--ad-surface-deep);color:var(--ad-text);border:1px solid var(--ad-border);border-radius:6px;padding:6px 8px;font:13px system-ui,'Microsoft YaHei',sans-serif}
.h3-tl .h3-mini-field{display:inline-flex;align-items:center;gap:5px;font-size:14px;white-space:nowrap}
.h3-tl .h3-load-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.h3-tl .h3-load-row>button{background:#3B7DC4;border-color:#4A80BF;color:#fff}.h3-tl .h3-load-row>button:hover{background:#326BA8;color:#fff}
.h3-tl .h3-tlbar button{background:#3B7DC4;border-color:#4A80BF;color:#fff}.h3-tl .h3-tlbar button:hover{background:#326BA8;color:#fff}.h3-tl .h3-tlbar button:disabled{opacity:.45}
.h3-tl .h3-pool-slot{border:1px dashed var(--ad-border);border-radius:8px;min-height:132px;background:transparent}
.h3-tl .h3-pool{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;align-content:flex-start;align-items:stretch;justify-content:flex-start;min-height:0;height:auto;max-height:none;flex:0 0 auto!important;border:1px dashed var(--ad-border);border-radius:8px;padding:10px;box-sizing:border-box;width:100%;background:#242424;position:relative;z-index:0;overflow:visible}
.h3-tl .h3-pool.h3-drop{border-color:#22d3ee;background:rgba(34,211,238,.08)}
.h3-tl .h3-glass-library.h3-drop{outline:2px dashed #22d3ee;outline-offset:-4px;background:rgba(34,211,238,.06)}
.h3-tl .h3-view.h3-drop{outline:2px dashed #22d3ee;outline-offset:-4px;background:rgba(34,211,238,.06)}
.h3-tl .h3-pool.is-empty{min-height:96px;height:auto;max-height:none;overflow:visible;padding:16px 8px;align-items:center;align-content:center;justify-content:center;flex-direction:column;gap:10px}.h3-tl .h3-pool-empty{color:var(--ad-text-mute);font-size:15px;line-height:1.5;margin:0;padding:8px 8px 0;text-align:center;width:100%}.h3-tl .h3-pool-load{min-height:34px!important;padding:6px 16px!important}
.h3-tl .h3-shot{position:relative;flex:1 1 168px;min-width:148px;max-width:230px;border:1px solid var(--ad-border);border-radius:6px;overflow:hidden;background:var(--ad-surface-deep)}
.h3-tl .h3-shot img{display:block;width:100%;height:108px;object-fit:contain;background:#111}
.h3-tl .h3-shot select{width:100%;border:none;border-top:1px solid var(--ad-border);border-radius:0;min-height:26px;font-size:13px}
.h3-tl .h3-shot button{position:absolute;top:2px;right:2px;min-height:22px;padding:0 6px;border-radius:10px;font-size:13px;background:#000a}
.h3-tl .h3-transport{display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;gap:4px!important;width:100%}.h3-tl .h3-transport .h3-trim-cluster{display:flex!important;flex-direction:row!important;align-items:center!important;justify-content:center!important;gap:12px!important;width:auto!important}.h3-tl .h3-transport .h3-clock{display:inline-block!important;min-width:78px;text-align:center;font:600 16px var(--ad-font);color:var(--ad-text)}.h3-tl .h3-transport [data-play]{min-width:104px;font-weight:700;padding:6px 18px}.h3-tl .h3-transport [data-mute]{background:#24456B!important;border-color:#4A80BF!important;color:#dbeafe!important;padding:6px 16px}.h3-tl .h3-transport .h3-selection-summary{display:block!important;flex:0 0 auto!important;width:100%;text-align:center!important;white-space:normal!important;overflow:visible!important}.h3-tl .h3-hint{text-align:center}
.h3-tl .h3-trim-cluster{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center}
.h3-tl .h3-clock{font-variant-numeric:tabular-nums;color:var(--ad-text);font-size:15px;min-width:5.5em;text-align:right}
.h3-tl .h3-hint{width:100%;text-align:center;color:var(--ad-text-dim);font-size:14px}
.h3-tl .h3-selection-summary{flex:1 1 0;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:right;color:var(--ad-text);font-size:14px}
.h3-tl .h3-hint.error{color:var(--ad-danger-text)}
/* 节点宽度档位：data-w 由 dom_layout.js 按控件实际宽度标记，不跟随浏览器窗口 */
.h3-tl[data-w="sm"] .h3-head{flex-basis:56px;padding:0 5px;font-size:13px;gap:3px}
.h3-tl[data-w="sm"] .h3-body{min-width:120px}
.h3-tl[data-w="sm"] .h3-shot{flex-basis:140px;min-width:132px;max-width:188px}
.h3-tl[data-w="sm"] .h3-shot img{height:92px}
.h3-tl[data-w="sm"] .h3-player .screen{height:200px;max-height:200px}
.h3-tl[data-w="sm"] .h3-player.is-empty .screen{height:140px;max-height:140px}
.h3-tl[data-w="lg"] .h3-head{flex-basis:84px}
.h3-tl[data-w="lg"] .h3-shot{flex-basis:198px;min-width:168px;max-width:252px}
.h3-tl[data-w="lg"] .h3-shot img{height:124px}
.h3-tl[data-w="lg"] .h3-player .screen{height:320px;max-height:320px}
.h3-tl .h3-lane[data-kind] .h3-head{padding:0 2px;gap:2px;font-size:13px;white-space:nowrap}
.h3-tl .h3-head [data-track-mute],.h3-tl .h3-head [data-track-visibility]{flex:0 0 16px!important;width:16px!important;min-width:16px!important;height:22px!important;min-height:22px!important;padding:0!important;border:0!important;background:transparent!important;font-size:12px!important;line-height:22px!important}
.h3-tl .h3-lane[data-kind] .h3-head{flex-basis:96px!important;box-sizing:border-box;flex-shrink:0}
.h3-tl .h3-track-disabled .h3-clip{opacity:.32;filter:grayscale(.85);pointer-events:none}
.h3-tl .h3-head [data-track-visibility][aria-pressed="true"]{color:#8b98ab!important}
.h3-tl .h3-head [data-track-mute][aria-pressed="true"]{background:#38506b!important;border-radius:4px}
/* 操作框高度只由素材轨数决定（见 syncViewHeight），节点拖拽不再拉伸它 */
`;
document.head.append(style);

/** 新节点起始态：无素材、3×243 帧 @24fps（总 729 帧 ≈ 30.375s）。 */
const DEFAULT_SEG_FRAMES = 243, DEFAULT_SEG_COUNT = 3, DEFAULT_MAX_FRAMES = 362;
function defaultSegmentRecipe() {
    return { frames: DEFAULT_SEG_FRAMES, count: DEFAULT_SEG_COUNT, unit: "f" };
}
function bareTimeline() {
    return { version: 3, segmentation: "clips", duration: null, sources: [],
             video_tracks: [[], [], []], audio_tracks: [[], [], []], images: [], split_points: [],
             max_frames: DEFAULT_MAX_FRAMES };
}
function emptyTimeline() {
    const frames = DEFAULT_SEG_FRAMES, count = DEFAULT_SEG_COUNT;
    const duration = snapFrame(frames * count / FPS);
    const split_points = [];
    for (let i = 1; i < count; i++) split_points.push(snapFrame(duration * i / count));
    return { ...bareTimeline(), duration, split_points, segment_recipe: defaultSegmentRecipe() };
}
/** 旧空清单 / 无素材且无时长段线 → 可套用起始态；有素材或已有纯生成时长则保留。 */
function isBlankTimeline(state) {
    if (!state) return true;
    if (Array.isArray(state.generation_clips)) return false;
    const hasSources = (state.sources || []).length > 0;
    const hasImages = (state.images || []).length > 0;
    const hasClips = [...(state.video_tracks || []), ...(state.audio_tracks || [])].some(lane => lane && lane.length);
    if (hasSources || hasImages || hasClips) return false;
    return state.duration == null && !(state.split_points || []).length;
}


function isBlockedToneSource(source) {
    const file = String(source?.file || "");
    const name = String(source?.name || "");
    const blob = `${file}\n${name}`;
    if (/aiden_continuum_.*_tone/i.test(blob)) return true;
    // *tone*.wav in file or name
    if (/tone/i.test(file) && /\.wav$/i.test(file)) return true;
    if (/tone/i.test(name) && /\.wav$/i.test(name)) return true;
    return false;
}

/** 加载时剔除内置嘟声测试音源及其片段（不删磁盘文件）。 */
function stripToneSources(state) {
    if (!state || !Array.isArray(state.sources)) return state || emptyTimeline();
    const bad = new Set(state.sources.filter(isBlockedToneSource).map(source => source.id));
    if (!bad.size) return state;
    state.sources = state.sources.filter(source => !bad.has(source.id));
    for (const key of ["video_tracks", "audio_tracks"]) {
        if (!Array.isArray(state[key])) continue;
        state[key] = state[key].map(lane => (lane || []).filter(clip => !bad.has(clip.source)));
    }
    return state;
}


function migrateLegacy(config) {
    const empty = bareTimeline();
    if (Array.isArray(config.entries) && !config.tracks) {  // v1：单条已切分清单
        const entries = config.entries.filter(item => item && Number(item.seconds_float) > 0);
        if (!entries.length) return empty;
        let cursor = 0;
        const sources = [], ids = new Map(), clips = [], splits = [];
        for (const entry of entries) {
            const key = `${entry.origin || "input"}:${entry.file}`;
            if (!ids.has(key)) {
                const source = { id: uid("m"), kind: "audio", origin: entry.origin || "input", file: entry.file,
                                 name: entry.name || entry.file, duration: null };
                sources.push(source); ids.set(key, source.id);
            }
            const length = Number(entry.seconds_float), srcIn = Number(entry.source_start || 0);
            clips.push({ id: uid("c"), source: ids.get(key), tl_start: snapFrame(cursor), src_in: srcIn, src_out: srcIn + length });
            cursor += length; splits.push(snapFrame(cursor));
        }
        return { ...empty, sources, audio_tracks: [clips, [], []], split_points: splits.slice(0, -1) };
    }

    const tracks = (config.tracks || []).filter(track => track && track.editor?.exec?.track_file);  // v2
    if (!tracks.length) return empty;
    if (tracks.length > 5) throw new Error(`旧清单有 ${tracks.length} 条音轨，超过当前 5 条上限，无法自动迁移；时间线暂显示为空，请不要保存覆盖原工作流，先备份并联系作者处理。`);
    const parsed = tracks.map(track => {
        const exec = track.editor.exec, duration = Number(exec.duration) || 0;
        const start = Number(track.trim_start || 0), end = Number(track.trim_end ?? duration);
        const points = [...new Set((exec.points || []).map(Number).filter(p => start < p && p < end))].sort((a, b) => a - b);
        const bounds = [start, ...points, end];
        let cuts = bounds.slice(1).map((b, i) => [bounds[i], b]);
        if (track.output_ranges != null) {
            const ranges = track.output_ranges.map(([a, b]) => [Math.max(start, Number(a)), Math.min(Number(b), end)]).filter(([a, b]) => a < b);
            const selectedBounds = [...new Set([...bounds, ...ranges.flat()])].sort((a, b) => a - b);
            cuts = selectedBounds.slice(1).map((b, i) => [selectedBounds[i], b])
                .filter(([a, b]) => ranges.some(([lo, hi]) => lo <= a + 1e-7 && hi >= b - 1e-7));
        }
        return { file: exec.track_file, duration, cuts, cutFlag: track.output_ranges != null || points.length > 0,
                 name: track.name || exec.track_file, trimStart: start, trimEnd: end };
    });
    let primary = tracks.findIndex((track, i) => track.id === config.main_track && parsed[i].cutFlag);
    if (primary < 0) primary = parsed.findIndex(item => item.cutFlag);
    if (primary < 0) primary = 0;
    const grid = [];
    let cursor = 0;
    for (const [a, b] of parsed[primary].cuts) { cursor += b - a; grid.push(snapFrame(cursor)); }
    const lanes = Array.from({length:Math.max(3,tracks.length)},()=>[]);
    let companion = 0;
    const sources = parsed.map((item, i) => ({ id: uid("m"), kind: "audio", origin: "input", file: item.file, name: item.name, duration: item.duration }));
    parsed.forEach((item, i) => {
        const lane = i === primary ? 0 : 1 + companion++;
        let position = 0;
        if (item.cutFlag) for (const [a, b] of item.cuts) {
            lanes[lane].push({ id: uid("c"), source: sources[i].id, tl_start: snapFrame(position), src_in: a, src_out: b });
            position += b - a;
        } else lanes[lane].push({ id: uid("c"), source: sources[i].id, tl_start: 0, src_in: item.trimStart, src_out: item.trimEnd });
    });
    return { ...empty, sources, audio_tracks: lanes, split_points: grid.slice(0, -1) };
}

function loadStateValue(raw) {
    let config;
    try { config = JSON.parse(raw || "{}"); } catch { config = {}; }
    let state;
    if (config.version === 3) { config = repairSourceInstances(config); state = config.segmentation === "clips" ? config : cutTimelineAt(config, config.split_points || []); }
    else if (config.version === 1 || config.version === 2 || config.tracks || config.entries) { const migrated = migrateLegacy(config); state = cutTimelineAt(migrated, migrated.split_points); }
    else state = emptyTimeline();
    state = stripToneSources(state);
    state.muted_tracks ||= {};
    state.disabled_tracks ||= {};
    const sourceMap = new Map((state.sources || []).map(source => [source.id, source]));
    for (const key of ["video_tracks", "audio_tracks"])
        state.muted_tracks[key] = (state[key] || []).map((lane, index) =>
            state.muted_tracks[key]?.[index] ?? lane.some(clip => !!sourceMap.get(clip.source)?.muted));
    // 去掉嘟声后若变成空白、或旧版空 JSON：套用 2×124 起始态，不覆盖已有时长/段线或素材
    if (isBlankTimeline(state)) state = emptyTimeline();
    if (![...state.video_tracks,...state.audio_tracks].some(lane=>lane.length) && state.duration>0)
        generationClips(state);
    return state;
}

export function updateMaterialOutputs(node, state) {
    // 批处理模式（素材路径）：右上角「分段清单（x段）」按路径内文件数量显示
    const batch = state?.path_source ? Math.max(0, Number(node?._adH3BatchCount) || 0) : 0;
    const count = state?.path_source ? batch : Math.max(0, segmentIntervals(state).length);
    const slots = [{ name: "分段清单", label: `分段清单（${count}段）`, localized_name: `分段清单（${count}段）`, type: "H3_AUDIO_QUEUE", h3_key: "queue" }];
    // Library only exposes 分段清单; ref ports move to linked AidenH3Segment.
    for (const [index, output] of node.outputs.entries()) {
        output.h3_key ||= index === 0 ? "queue" : slots.find(slot => slot.name === output.name && slot.type === output.type)?.h3_key;
    }
    const wanted = new Set(slots.map(slot => slot.h3_key));
    for (let index = node.outputs.length - 1; index >= 0; index--)
        if (!wanted.has(node.outputs[index].h3_key)) node.removeOutput(index);
    const existing = new Map(node.outputs.map(output => [output.h3_key, output]));
    const ordered = slots.map(slot => {
        let output = existing.get(slot.h3_key);
        if (!output) { node.addOutput(slot.name, slot.type); output = node.outputs.at(-1); }
        return Object.assign(output, slot);
    });
    ordered.forEach((output,index)=>{output.slot_index=index;});
    node.outputs = ordered;
    for (const [index, output] of ordered.entries()) for (const id of output.links || []) {
        const link = node.graph?.links?.get?.(id) || node.graph?.links?.[id];
        if (link) link.origin_slot = index;
    }
    node.setDirtyCanvas(true, true);
    // 载入期间的占位空状态不代表真实空清单，等待素材恢复完成再同步参考口。
    if (node._adH3MaterialsReady === false) return;
    // Push dynamic 参考图/音视频 ports onto linked 当前段.
    try {
        const links = node.outputs?.[0]?.links || [];
        for (const id of links) {
            const link = node.graph?.links?.get?.(id) || node.graph?.links?.[id];
            const target = link && node.graph?.getNodeById(link.target_id);
            if (target?.comfyClass === "AidenH3Segment" || target?.type === "AidenH3Segment")
                updateSegmentMaterialOutputs(target, state);
        }
    } catch {}
}

const SEGMENT_FIXED_KEYS = ["context", "index", "start_frame", "generation_frames", "fps", "width", "height", "positive_prompt", "negative_prompt", "round_image", "round_audio", "round_video", "source_video", "original_audio"];

// 图片按底部横条顺序；音视频按有片段的轨道数，未上轨素材不占参考口。
export function segmentRefCounts(state) {
    const sources = state?.sources || [];
    // 参考图按加载位置固定编号：禁止输出也占原槽位，仅该口输出空。
    // 不能减掉禁止图数量，否则后台按原位置取材时，末尾图片会被ref_counts截断。
    const images = sources.filter(source => source && source.kind === "image").length;
    const videos = (state?.video_tracks || []).reduce((max, lane, index) => lane?.length ? Math.max(max, index + 1) : max, 0);
    // 参考音频口按轨道位置固定：音频1→参考音频1…；中间空轨保留空位，不把后面的音频往前顶。
    const audios = (state?.audio_tracks || []).reduce((max, lane, index) => lane?.length ? Math.max(max, index + 1) : max, 0);
    return {
        image: Math.min(PORT_LIMITS.image, images),
        video: Math.min(PORT_LIMITS.video, videos),
        video_audio: Math.min(PORT_LIMITS.video, videos),
        audio: Math.min(PORT_LIMITS.audio, audios),
    };
}

export function updateSegmentMaterialOutputs(node, state) {
    if (!node || (node.comfyClass !== "AidenH3Segment" && node.type !== "AidenH3Segment")) return;
    // sources 未就绪时暂不动旧连线；明确的空数组则是空清单，必须移除全部参考口。
    if (!Array.isArray(state?.sources)) return;
    // 只有素材/参考图/路径真正变化时才重建端口；普通编辑（含路径输入逐字触发）直接跳过，避免节点跳闪。
    const signature = JSON.stringify({
        path: String(state?.path_source || ""),
        sources: (state?.sources || []).map(source => [source.id, source.kind, !!source.priority]),
        images: (state?.images || []).map(image => [image.source, image.segment]),
        tracks: [state?.video_tracks, state?.audio_tracks].map(lanes => (lanes || []).map(lane => !!lane?.length)),
        outputs: (node.outputs || []).map(output => [output.name, output.type]),
    });
    if (node._adH3MaterialSignature === signature) return;
    node._adH3MaterialSignature = signature;
    const labels = {
        context: "当前段上下文", index: "段号", start_frame: "分段首帧", generation_frames: "本段帧数", fps: "帧率",
        width: "画面宽度", height: "画面高度", positive_prompt: "正向提示词", negative_prompt: "反向提示词",
        round_image: "本段图像", round_audio: "本段音频", round_video: "本段视频",
        source_video: "源视频", original_audio: "源音频",
    };
    const fixed = SEGMENT_FIXED_KEYS.map(name => ({
        name, label: labels[name] || name, localized_name: labels[name] || name,
        type: ({
            context: "H3_LOOP_CONTEXT", index: "INT", start_frame: "IMAGE", generation_frames: "INT", fps: "FLOAT",
            width: "INT", height: "INT", positive_prompt: "STRING", negative_prompt: "STRING",
            round_image: "IMAGE", round_audio: "AUDIO", round_video: "VIDEO",
            source_video: "VIDEO", original_audio: "AUDIO",
        })[name],
        h3_key: `fixed:${name}`,
    }));
    const sources = state?.sources || [];
    const counts = segmentRefCounts(state);
    // 端口数量只按当前实际素材/轨道生成：删除音轨或片段后，多余的参考口（含旧连线）一起去掉，
    // 避免只有一条音轨/一条视频轨却还残留「参考音频2/3」「参考视频2/3」这种空端口。
    const desired = {
        image: counts.image,
        video: counts.video,
        video_audio: counts.video_audio,
        audio: counts.audio,
    };
    const refSlots = [];
    for (const [kind, prefix, type] of [
        ["image", "ref_image", "IMAGE"],
        ["video", "ref_video", "IMAGE"],
        ["video_audio", "ref_video_audio", "AUDIO"],
        ["audio", "ref_audio", "AUDIO"],
    ]) {
        const limit = PORT_LIMITS[kind] ?? PORT_LIMITS.video ?? 3;
        Array.from({ length: Math.min(limit, desired[kind] || 0) }).forEach((_, index) => {
            const label = `${({ ref_image: "参考图", ref_video: "参考视频", ref_video_audio: "参考视频音频", ref_audio: "参考音频" })[prefix]}${index + 1}`;
            // Stable key by slot name (not source.id) so refresh does not drop linked ports.
            const name = `${prefix}_${index}`;
            refSlots.push({ name, label, localized_name: label, type, h3_key: `name:${name}` });
        });
    }
    const isRefName = name => /^ref_(image|video|video_audio|audio)_\d+$/.test(name || "");
    // Snapshot outbound links by output name BEFORE any remove/reorder.
    const linkSnap = new Map();
    for (const [index, output] of (node.outputs || []).entries()) {
        const specs = [];
        for (const id of output.links || []) {
            const link = node.graph?.links?.get?.(id) || node.graph?.links?.[id];
            if (!link) continue;
            specs.push({ target_id: link.target_id, target_slot: link.target_slot, type: link.type });
        }
        if (specs.length) linkSnap.set(output.name, specs);
    }
    const slots = [...fixed, ...refSlots, {name: "mask_frames", label: "遮罩专用", localized_name: "遮罩专用", type: "IMAGE", h3_key: "name:mask_frames"}];
    const alias = { "宽度": "width", "高度": "height", width: "width", height: "height" };
    for (const output of (node.outputs || [])) {
        const raw = output.name;
        const canon = alias[raw] || raw;
        if (SEGMENT_FIXED_KEYS.includes(canon)) output.h3_key = `fixed:${canon}`;
        else if (isRefName(raw)) output.h3_key = `name:${raw}`;
        else output.h3_key ||= slots.find(slot => slot.name === output.name && slot.type === output.type)?.h3_key;
    }
    const wanted = new Set(slots.map(slot => slot.h3_key));
    for (let index = (node.outputs || []).length - 1; index >= 0; index--) {
        const key = node.outputs[index].h3_key;
        if (!wanted.has(key)) node.removeOutput(index);
    }
    const keep = new Set();
    for (let index = 0; index < (node.outputs || []).length; ) {
        const raw = node.outputs[index]?.name;
        const canon = alias[raw] || raw;
        if (SEGMENT_FIXED_KEYS.includes(canon)) {
            if (keep.has(canon)) { node.removeOutput(index); continue; }
            keep.add(canon);
            node.outputs[index].name = canon;
            node.outputs[index].h3_key = `fixed:${canon}`;
        }
        index++;
    }
    const existing = new Map((node.outputs || []).map(output => [output.h3_key, output]));
    // Also index by name for reuse when key scheme changed across versions.
    const byName = new Map((node.outputs || []).map(output => [output.name, output]));
    const ordered = slots.map(slot => {
        let output = existing.get(slot.h3_key) || byName.get(slot.name);
        if (!output) { node.addOutput(slot.name, slot.type); output = node.outputs.at(-1); }
        return Object.assign(output, slot);
    });
    ordered.forEach((output,index)=>{output.slot_index=index;});
    node.outputs = ordered;
    for (const [index, output] of ordered.entries()) for (const id of output.links || []) {
        const link = node.graph?.links?.get?.(id) || node.graph?.links?.[id];
        if (link) link.origin_slot = index;
    }
    // Re-apply any snapped links lost during rebuild (same slot name).
    for (const [index, output] of ordered.entries()) {
        const specs = linkSnap.get(output.name);
        if (!specs?.length) continue;
        for (const spec of specs) {
            const already = (output.links || []).some(id => {
                const link = node.graph?.links?.get?.(id) || node.graph?.links?.[id];
                return link && link.target_id === spec.target_id && link.target_slot === spec.target_slot;
            });
            if (already) continue;
            const target = node.graph?.getNodeById?.(spec.target_id);
            if (!target) continue;
            try { node.connect(index, target, spec.target_slot); } catch {}
        }
    }
    // 端口数必须与 ref_counts 同步：否则排队瞬间后端仍按旧数量切片，连线会拿到错位的数据。
    const countsWidget = (node.widgets || []).find(widget => widget.name === "ref_counts");
    if (countsWidget) {
        const value = JSON.stringify(desired);
        if (countsWidget.value !== value) {
            countsWidget.value = value;
            try { countsWidget.callback?.(value); } catch {}
        }
    }
    const namesNow = (node.outputs || []).map(output => `${output.name}|${output.type}|${output.label || ""}`).join(",");
    if (node._adH3LastOutputSignature !== namesNow) {
        node._adH3LastOutputSignature = namesNow;
        node.setDirtyCanvas(true, true);
    }
}

/** 旧隐藏控件可能携带初次状态；恢复端口和编辑器必须一致读取末尾的当前存档。 */
export function savedTimelineValue(info) {
    for (const value of [...(info?.widgets_values || [])].reverse()) {
        try {
            const state = typeof value === "string" ? JSON.parse(value) : value;
            if (state && (Array.isArray(state.sources) || Array.isArray(state.tracks) || Array.isArray(state.entries))) return value;
        } catch {}
    }
    return null;
}

export function linkedPromptSegment(queueNode) {
    const graph = queueNode?.graph, links = graph?.links;
    const values = typeof links?.values === "function" ? [...links.values()] : Object.values(links || {});
    for (const link of values) {
        if (String(link?.origin_id) !== String(queueNode?.id) || Number(link.origin_slot) !== 0) continue;
        const target = graph?.getNodeById?.(link.target_id)
            || (graph?._nodes || []).find(item => String(item.id) === String(link.target_id));
        if ((target?.comfyClass || target?.type) !== "AidenH3Segment") continue;
        const input = target.inputs?.find(item => item.name === "queue");
        if (input?.link != null && link.id != null && String(input.link) !== String(link.id)) continue;
        return target;
    }
    return null;
}

export function mountTimelineEditor(node) {
    if (node._h3TimelineMounted) return;
    node._h3TimelineMounted = true;
    const old = node.widgets?.findIndex(widget => widget.name === "queue_state");
    if (old >= 0) node.widgets.splice(old, 1);
    // 旧架构遗留的编辑器状态控件（audio_oasis_ui / h3_editor_state）：隐藏但保留占位，
    // 避免加载旧工作流时显示过期 JSON、或加载期按索引把队列状态误写进别的控件。
    for (const legacy of (node.widgets || []).filter(widget => widget.name === "audio_oasis_ui" || widget.name === "h3_editor_state")) {
        legacy.type = "hidden"; legacy.computeSize = () => [0, -4];
        legacy.serializeValue = () => "{}";
        if (legacy.element) legacy.element.style.display = "none";
    }

    const T = { state: emptyTimeline(), selected: new Set(), segment: -1, playhead: 0, pxs: 36, smartCuts: [],
                batchCount: 0, magnet: true, unit: "f", playing: false, nodes: [], buffers: new Map(), previewVideo: null };
    const undoStack = [], redoStack = [];
    node._adH3AssignTask=(session,task)=>{T.state.session_id=session;T.state.workflow_task_id=task;};
    const pushUndo = (groupMove=false) => { const state=JSON.stringify(T.state);undoStack.push(groupMove?{state,groupMove:true}:state); if (undoStack.length > 20) undoStack.shift(); redoStack.length = 0; syncHistory(); };
    // Layout v3：用户拖拽优先；绝不改 parent 样式（改了会和节点框错位）
    let applyLayoutHeight = () => {};
    let layoutTimer = 0;
    let interactionLock = 0;  // >0：拖拽中，推迟量高/清单重绘
    const beginInteraction = () => { interactionLock++; };
    const endInteraction = () => { interactionLock = Math.max(0, interactionLock - 1); };
    const scheduleLayout = (delay = 40) => {
        if (interactionLock) delay = Math.max(delay, 180);
        clearTimeout(layoutTimer);
        layoutTimer = setTimeout(() => {
            if (!node.graph || interactionLock) {
                if (interactionLock) scheduleLayout(120);
                return;
            }
            applyLayoutHeight();
        }, delay);
    };
    const restoreHistory = (from,to) => {const saved=from.pop(),groupMove=typeof saved==="object"&&saved.groupMove,state=JSON.stringify(T.state);to.push(groupMove?{state,groupMove:true}:state);T.state=JSON.parse(groupMove?saved.state:saved);afterEdit(!!groupMove,true,!!groupMove);};
    const doUndo = () => { if (!undoStack.length) return; restoreHistory(undoStack,redoStack);hint("已撤销上一步操作。"); };
    const doRedo = () => { if (!redoStack.length) return; restoreHistory(redoStack,undoStack);hint("已重做上一步操作。"); };
    const syncHistory = () => { root.querySelector("[data-undo]").disabled = !undoStack.length; root.querySelector("[data-redo]").disabled = !redoStack.length; };
    // 开关类按钮补 aria-pressed，读屏能读出磁吸与秒/帧当前状态。
    const syncToggleA11y = () => {
        const magnet = root.querySelector("[data-magnet]");
        if (magnet) magnet.setAttribute("aria-pressed", String(!!T.magnet));
        root.querySelectorAll("[data-unit]").forEach(item => item.setAttribute("aria-pressed", String(item.classList.contains("on"))));
    };

    const root = document.createElement("div");
    root.className = "aiden-h3 h3-tl";
    root.dataset.editorRevision = "20261025-cursor-timeline";
    root.innerHTML = `
<div class="h3-tl-inner"><div class="h3-tools h3-load-row">
  <button data-load="image">加载图片</button>
  <button data-load="audio">加载音频</button>
  <button data-load="video">加载视频</button>
  <button data-batch>批量导入</button>
  <div class="h3-path-field" data-path-field title="粘贴素材完整路径，回车或点导入按钮导入">
    <span class="h3-path-label" data-path-load>素材路径：</span>
    <button data-library="input" title="重新加载 ComfyUI/input/AD-Segmented-Loop-input 下的图片/音频/视频">重新加载</button>
    <button data-library="generated" title="生成加载 ComfyUI/output/AD-Segmented-Loop 下的图片/音频/视频">生成加载</button>
    <input data-path-input type="text" placeholder="粘贴完整路径后回车导入（如 E:\素材\a.mp4）" aria-label="素材文件路径">
  </div>
</div>
<div class="h3-player" data-player>
  <div class="h3-player-head"><span data-player-title>视频预览</span><span data-player-meta></span></div>
  <div class="screen"><div class="ph"><span data-player-ph-text>一帧光影，一段声音，皆可成为开端。</span></div><video playsinline preload="auto" data-player-video></video></div>
</div>
<div class="h3-tlbar">
  <span class="h3-group" role="group" aria-label="裁剪">
    <button data-edit="left" title="保留播放头右侧（裁掉左侧）" aria-label="裁掉播放头左侧">⧔</button>
    <button data-edit="split" title="在播放头处切断选中片段" aria-label="在播放头处切断">✂</button>
    <button data-edit="right" title="保留播放头左侧（裁掉右侧）" aria-label="裁掉播放头右侧">⧕</button>
  </span>
  <span class="h3-group" role="group" aria-label="撤销与磁吸">
    <button data-undo title="撤销">↶</button>
    <button data-redo title="重做">↷</button>
    <button data-magnet class="on" aria-pressed="true" title="游标磁吸（剪映）：白线靠近素材起止边/分段点会吸住；再点可关闭" aria-label="游标磁吸">🧲</button>
  </span>
  <span class="h3-group" role="group" aria-label="片段操作">
    <button data-separate title="分离选中视频的音轨">音视频分离</button>
    <button data-delete title="删除选中的片段（Delete 键）">删除</button>
    <button data-remove-source title="仅移除所选可见片段的素材使用；其它片段及磁盘文件保留">删除素材</button>
    <button data-merge title="只拼接同轨选中片段实际保留的内容，裁掉的内容不会恢复">合并</button>
  </span>
  <span class="h3-group h3-split-group" role="group" aria-label="分段参数">
    <label title="纯图片时只作末尾新增生成段的默认段长；无音视频时与分段数相乘得到总时长；有音视频且分段数≥1时，应用后会回写成实际每段长度">每段长度 <button type="button" class="h3-step-btn" data-every-step="-1" title="减少 17 帧（一次点击生效）">−</button><input data-every aria-label="每段长度" type="number" min="1" step="1" value="124" style="width:76px"><button type="button" class="h3-step-btn" data-every-step="1" title="增加 17 帧（一次点击生效）">＋</button></label>
    <span class="h3-unit"><button data-unit="s" aria-pressed="false">秒</button><button data-unit="f" class="on" aria-pressed="true">帧</button></span>
    <label title="≥1：按段数均分；填 0：按「每段长度」切开（仅音视频）；纯图片：直接决定生成段数，不再跟随图片数量">分段数 <input data-count aria-label="分段数" type="number" min="0" max="1000" step="1" value="2" style="width:44px"></label>
    <button data-apply-split class="primary" title="音视频仅处理所选可见片段：分段数≥1均分，填0按每段长度切开；纯图片按段数在末尾增删生成段">应用分段</button>
    <label class="h3-mini-field" title="单段生成帧数上限（须为 17k+5，非法值向下取整）；有连接时同步到 AD-当前段">单段上限 <input class="h3-max-frames-input" data-max-frames type="number" min="5" max="3600" step="17" value="362" style="width:76px" aria-label="单段生成帧数上限"></label>
  </span>
</div>
<div class="h3-recipe-meta"><span data-actual-count>实际 0 段</span><span data-recipe-preview class="h3-recipe-preview"></span></div>
<div class="h3-smart-row" data-smart-row>
  <button type="button" data-smart-cut class="primary" title="在白色游标位置切分选中的音视频片段（Ctrl 多选，可撤销）">切分视频</button>
  <button type="button" data-run-affected class="primary" title="选中分段后仅运行所选段；未选择时重跑受影响段，完成后回原进度">运行此段</button>
  <button type="button" data-new-group class="primary" title="新开一组并从第1段待命；素材、设置与旧成果保留，运行中请先停止">新建一组</button>
  <select data-smart-kind title="选择检测方式">
    <option value="beats-bar">踩节拍 I（小节）</option>
    <option value="beats-beat">踩节拍 II（每拍）</option>
    <option value="shots">智能镜头分割</option>
    <option value="breaths">智能气口分割</option>
  </select>
  <label>最小间隔 <input data-smart-gap type="number" min="0.2" step="0.05" value="0.5"></label>
  <label>最大段长 <input data-smart-max type="number" min="2" step="1" value="15"></label>
  <label>灵敏度 <input data-smart-sens type="number" min="0.1" max="0.95" step="0.05" value="0.75"></label>
  <button type="button" class="h3-vocals-toggle" data-smart-vocals aria-pressed="false" title="气口检测时用中置声道提取人声，减少伴奏干扰；点亮=人声增强开启">人声增强</button>
  <button data-smart-detect>检测</button>
  <button data-smart-apply class="primary">应用切分</button>
  <button data-smart-clear>清空候选</button>
  <button data-smart-add class="primary" title="在白色游标位置添加黄色候选切分线；应用切分后生效">添加切分线</button>
</div>
<div class="h3-view" data-view>
  <div class="h3-canvas" data-canvas>
    <div class="h3-ruler" data-ruler></div>
  </div>
</div>
<div class="h3-transport">
  <div class="h3-trim-cluster">
    <button data-cursor-step="-2" aria-label="后退2帧" title="白线游标后退2帧">◀</button>
    <span class="h3-clock" data-clock title="当前帧 / 总帧（24 FPS）">0 / 0</span>
    <button data-cursor-step="2" aria-label="前进2帧" title="白线游标前进2帧">▶</button>
    <button data-play class="primary" title="从白线游标位置开始播放到时间线结尾。空格=播放/暂停">▶ 播放</button>
    <button type="button" data-mute title="切换预览声音">取消静音</button>
  </div>
  <div class="h3-selection-summary" data-selection-summary hidden></div>
  <div class="h3-frame-summary" data-frame-summary><span data-timeline-frames></span><span data-segment-frames></span></div>
</div>
  <div class="h3-hint" data-hint>无音视频：总长 = 每段长度 × 分段数。纯图片：生成段数由「分段数」决定，已有段长保留、末尾增删，增删图片不再改变段数。有音视频：分段数≥1 均分；分段数填 0 则按每段长度切开。</div>

<div class="h3-frame-plan collapsed" data-frame-plan>
  <button type="button" class="h3-frame-toggle" data-frame-toggle aria-expanded="false">帧数明细 ▸</button>
  <div data-frame-plan-note></div>
  <div data-frame-plan-body></div>
</div>
<div class="h3-pool" data-pool></div>
</div>`;
    mountGlassTimeline(root);
    const storagePanel = mountStoragePanel(root, node, api, delay => scheduleLayout(delay), clearAllSources);
    const fileInput = document.createElement("input");
    fileInput.type = "file"; fileInput.multiple = true; fileInput.hidden = true;
    fileInput.accept = "image/*,video/*,audio/*";
    root.append(fileInput);

    const $ = selector => root.querySelector(selector);
    const view = $("[data-view]"), canvas = $("[data-canvas]"), ruler = $("[data-ruler]"),
          player = $("[data-player]"), playerVideo = player.querySelector("[data-player-video]"),
          playerTitle = player.querySelector("[data-player-title]"),
          playerMeta = player.querySelector("[data-player-meta]"),
          playerPh = player.querySelector(".ph"),
          pool = $("[data-pool]");

    view.addEventListener("pointerdown",()=>clearSourcePreview(root),{capture:true});
    root.querySelector("[data-play]").addEventListener("click",()=>clearSourcePreview(root),{capture:true});
    const blank = document.createElement("div");  // 标尺下空轨：剪映同款，方便往上拖/落轨
    blank.className = "h3-blank-above";
    blank.dataset.blank = "above";
    ruler.after(blank);

    // 拖放：图片拖到「参考图片」整块区域 → 加入参考图；音视频拖到时间轴区域 → 加入对应轨道。
    const fileKindOf = file => {
        const type = String(file?.type || "");
        if (type.startsWith("image/")) return "image";
        if (type.startsWith("video/")) return "video";
        if (type.startsWith("audio/")) return "audio";
        const name = String(file?.name || "").toLowerCase();
        if (/\.(png|jpe?g|webp|bmp|gif)$/.test(name)) return "image";
        if (/\.(mp4|mov|mkv|webm|avi|m4v)$/.test(name)) return "video";
        if (/\.(wav|mp3|flac|m4a|aac|ogg)$/.test(name)) return "audio";
        return "";
    };
    const hasFileDrag = event => {
        const types = event.dataTransfer?.types;
        return types ? [...types].includes("Files") : false;
    };
    const dropZones = [
        {element: root.querySelector(".h3-glass-library") || pool, kinds: ["image"], label: "参考图片"},
        {element: view, kinds: ["video", "audio"], label: "时间轴"},
    ];
    for (const zone of dropZones) {
        const element = zone.element;
        if (!element) continue;
        const enter = event => {
            if (!hasFileDrag(event)) return;
            event.preventDefault(); event.stopPropagation();
            element.classList.add("h3-drop");
        };
        element.addEventListener("dragenter", enter);
        element.addEventListener("dragover", event => {
            if (!hasFileDrag(event)) return;
            event.preventDefault(); event.stopPropagation();
            event.dataTransfer.dropEffect = "copy";
            element.classList.add("h3-drop");
        });
        element.addEventListener("dragleave", event => {
            if (element.contains(event.relatedTarget)) return;
            element.classList.remove("h3-drop");
        });
        element.addEventListener("drop", event => {
            if (!hasFileDrag(event)) return;
            event.preventDefault(); event.stopPropagation();
            element.classList.remove("h3-drop");
            const files = [...(event.dataTransfer?.files || [])].filter(file => zone.kinds.includes(fileKindOf(file)));
            if (!files.length) {
                hint(zone.kinds.includes("image") ? "参考图片区只接收图片；视频/音频请拖到时间轴区域。" : "时间轴区域接收视频/音频；图片请拖到参考图片区。", true);
                return;
            }
            hint(`已拖入 ${files.length} 个${zone.label}素材，正在导入…`);
            void uploadFiles(files);
        });
    }
    const hint = (text, error = false) => {
        if (typeof text === "string" && (text.startsWith("从白线") || text.startsWith("无音视频："))) {
            const el = $("[data-hint]");
            if (el) { el.textContent = ""; el.classList.remove("error"); }
            return;
        }
        const el = $("[data-hint]"); el.textContent = text; el.classList.toggle("error", error);
        el.hidden = !error && !!$("[data-selection-summary]").textContent;
    };
    const showPlayerPlaceholder = (text) => {
        if (!playerPh) return;
        playerPh.hidden = false;
        playerPh.style.display = "flex";
        const label = playerPh.querySelector("[data-player-ph-text]");
        if (label) label.textContent = text;
    };

    const duration = () => {
        if (![...T.state.video_tracks,...T.state.audio_tracks].some(lane=>lane.length) && Array.isArray(T.state.generation_clips))
            return Math.max(0,...T.state.generation_clips.map(clipEnd));
        if (T.state.duration != null) return Number(T.state.duration);
        const ends = [...T.state.video_tracks, ...T.state.audio_tracks].flat().map(clipEnd);
        return ends.length ? Math.max(...ends) : 0;
    };
    const intervals = () => segmentIntervals(T.state);
    const bounds = () => {  // 连续点列（无空隙）或成对展开；计段请用 intervals()
        const iv = intervals();
        if (!iv.length) return [];
        const hasClips = [...T.state.video_tracks, ...T.state.audio_tracks].some(lane => lane.length);
        if (T.state.segmentation === "clips" && hasClips) return iv.flat();
        return [iv[0][0], ...iv.map(pair => pair[1])];
    };
    const segmentAt = t => {
        const iv = intervals();
        for (let i = 0; i < iv.length; i++) if (t >= iv[i][0] && t < iv[i][1]) return i;
        return -1;
    };
    const segmentIndexForClip = clip => {
        if (!clip) return -1;
        const iv = intervals();
        const a = snapFrame(clip.tl_start), b = snapFrame(clipEnd(clip));
        let i = iv.findIndex(([s, e]) => Math.abs(s - a) < 1e-6 && Math.abs(e - b) < 1e-6);
        if (i >= 0) return i;
        return segmentAt((clip.tl_start + clipEnd(clip)) / 2);
    };
    const syncSegmentFromSelection = ({ announce = true } = {}) => {
        playedSelectionKey = "";  // 再次点选同一片段也表示重新从该片段开始试听。
        // 选中素材框 → 对应第几段；多选取时间上最早的一条（视频优先）
        const selected = [...T.state.video_tracks, ...T.state.audio_tracks].flat()
            .filter(clip => T.selected.has(clip.id));
        if (!selected.length) return;
        const videos = new Set((T.state.video_tracks || []).flat().map(c => c.id));
        const prefer = selected.filter(c => videos.has(c.id));
        const clip = (prefer.length ? prefer : selected)
            .sort((a, b) => a.tl_start - b.tl_start || clipEnd(a) - clipEnd(b))[0];
        const index = segmentIndexForClip(clip);
        if (index < 0) return;
        T.segment = index;
        if (announce) renderSelectionSummary();
    };

    function renderFrameSummary(cur=Math.max(0,Math.round(T.playhead*FPS)), total=Math.max(0,Math.round(duration()*FPS))) {
        const cached=T.frameInfo||{spans:[],selectedIndex:-1};
        const spans=cached.spans;
        const probe=cur===total&&total>0?cur-1:cur;
        const index=cached.selectedIndex>=0?cached.selectedIndex:spans.findIndex(span=>probe>=span.start&&probe<span.end);
        const span=spans[index];
        const timeline=$("[data-timeline-frames]"), segment=$("[data-segment-frames]");
        const values=[`时间线：${cur} / ${total} 帧`,
            span?`本段：${Math.max(0,Math.min(span.count,cur-span.start))} / ${span.count} 帧`:"本段：未选择或游标位于空隙"];
        [timeline,segment].forEach((element,i)=>{if(element&&element.textContent!==values[i])element.textContent=values[i];});
    }

    function renderSelectionSummary() {
        const element = $("[data-selection-summary]"), selected = [];
        const spans=intervals();
        const sources = sourcesOf();
        for (const [key,name] of [["video_tracks","视频"],["audio_tracks","音频"]])
            T.state[key].forEach((lane,i) => lane.forEach(clip => {
                if (T.selected.has(clip.id) && !trackDisabled(T.state,key,i)) selected.push({clip,track:`${name}${i+1}`,name:sources[clip.source]?.name || "未命名素材"});
            }));
        let text = "";
        if (selected.length === 1) {
            const {clip,track,name} = selected[0];
            const index = segmentIndexForClip(clip);
            text = `已选：${track}${index >= 0 ? ` ｜ 第${index+1}段` : ""}`;
            element.title = `${name} ｜ 时间线 ${fmtClock(clip.tl_start)}–${fmtClock(clipEnd(clip))} ｜ 时长 ${(clip.src_out-clip.src_in).toFixed(2)} 秒`;
        } else if (selected.length > 1) {
            const numbers = [...new Set(selected.map(item=>segmentIndexForClip(item.clip)).filter(index=>index>=0))].sort((a,b)=>a-b);
            const segments = numbers.length ? ` ｜ 第${numbers.map(index=>index+1).join("、")}段` : "";
            text = `已选中 ${selected.length} 个片段${segments}：${[...new Set(selected.map(item=>`${item.track} · ${item.name}`))].join("、")} ｜ 时间线 ${fmtClock(Math.min(...selected.map(item=>item.clip.tl_start)))}–${fmtClock(Math.max(...selected.map(item=>clipEnd(item.clip))))}`;
        } else if (!hasAvClips()) {
            const generated = generationClips(T.state).filter(clip=>T.selected.has(clip.id));
            const numbers = [...new Set(generated.map(segmentIndexForClip).filter(index=>index>=0))].sort((a,b)=>a-b);
            if (numbers.length > 1) text = `已选中 ${generated.length} 个生成片段 ｜ 第${numbers.map(index=>index+1).join("、")}段`;
            else {
                const index = numbers[0] ?? T.segment;
                if (spans[index]) {
                    const [a,b] = spans[index];
                    text = `已选中第${index+1}段 ｜ 时长 ${(b-a).toFixed(2)} 秒`;
                }
            }
        }
        const layoutChanged=element.textContent!==text;
        element.hidden = !text;if(layoutChanged)element.textContent = text;
        const selectedIndex=selected.length===1?segmentIndexForClip(selected[0].clip)
            : (!hasAvClips()&&T.segment>=0?T.segment:-1);
        T.frameInfo={spans:spans.map(([a,b])=>({start:Math.round(a*FPS),end:Math.round(b*FPS),count:Math.round((b-a)*FPS)})),selectedIndex};
        renderFrameSummary();
        const info = root.querySelector("[data-glass-source]");
        if (info) {
            const kindName = {image: "图片", video: "视频", audio: "音频"};
            if (selected.length === 1) {
                const source = sourcesOf()[selected[0].clip.source];
                const kind = kindName[source?.kind] || "素材";
                const duration = source?.duration ? ` · ${Number(source.duration).toFixed(2)}s` : "";
                info.textContent = `素材信息：${source?.name || "未命名素材"}（${kind}${duration}）`;
                info.title = source?.name || "未命名素材";
            } else if (selected.length > 1) {
                info.textContent = `素材信息：已选 ${selected.length} 个片段`;
                info.title = "多选";
            } else {
                info.textContent = "素材信息：未选择素材";
                info.title = "";
            }
        }
        const message = $("[data-hint]");
        message.hidden = !!text && !message.classList.contains("error");
        if(layoutChanged)scheduleLayout(40);
    }

    const sourcesOf = () => Object.fromEntries(T.state.sources.map(source => [source.id, source]));

    const timeToX = t => t * T.pxs;
    const xToTime = x => x / T.pxs;
    // 片段相对"轨道内容区"定位（左侧是轨道头），标尺/播放头/段线/预览必须使用同一零点。
    const headOffset = () => timelineOrigin(canvas);
    const viewScale = () => {
        const rect = root.getBoundingClientRect();
        return rect.width > 0 && root.offsetWidth > 0 ? rect.width / root.offsetWidth : 1;
    };
    // 标尺步长：保证每格至少约 42px，避免放大时刻度无限加密（DOM 过多会把缩放拖卡）。
    const RULER_STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const rulerStep = () => RULER_STEPS.find(step => step * T.pxs >= 42) || RULER_STEPS[RULER_STEPS.length - 1];
    // 段号漂移修复：段线增删/移动后，按时间重叠把图片的「第 N 段参考」自动迁移到新段号。
    let lastIntervals = null;
    const snapshotIntervals = () => segmentIntervals(T.state).map(([start, end]) => [start, end]);
    function remapImageSegments() {
        const next = snapshotIntervals();
        const old = lastIntervals;
        lastIntervals = next;
        if (!old || !old.length || !next.length) return;
        const same = old.length === next.length && old.every((pair, index) =>
            Math.abs(pair[0] - next[index][0]) < 1e-6 && Math.abs(pair[1] - next[index][1]) < 1e-6);
        if (same) return;
        const mapping = old.map(([a, b]) => {
            let best = -1, bestOverlap = 0;
            next.forEach(([c, d], index) => {
                const overlap = Math.min(b, d) - Math.max(a, c);
                if (overlap > bestOverlap) { bestOverlap = overlap; best = index; }
            });
            return bestOverlap > 1e-6 ? best : -1;
        });
        let changed = false, dropped = 0;
        for (const image of T.state.images || []) {
            const segment = Number(image.segment);
            if (!(segment >= 1)) continue;
            const mapped = mapping[segment - 1];
            if (mapped === undefined) continue;
            if (mapped < 0) { image.segment = -1; dropped += 1; changed = true; }
            else if (mapped + 1 !== segment) { image.segment = mapped + 1; changed = true; }
        }
        if (changed) hint(dropped
            ? `段线变化：${dropped} 张图片的原段已不存在，已改为「全局参考」`
            : "段线变化：已按时间重叠自动迁移图片的「第 N 段参考」");
    }
    // 磁吸锚点缓存：同一帧内多次 snapTo 复用；片段位置变了用 ++snapEpoch 失效
    let snapEpoch = 0, snapCacheEpoch = -1, snapCacheAnchors = null, snapCacheTotal = 0;
    const bumpSnapCache = () => { snapEpoch++; };
    const snapTo = (t, { withPlayhead = true } = {}) => {
        // 剪映同款磁吸：素材起止边、分段线、播放头游标、时间线 0/终点（默认开，🧲 可关）
        // 注意：必须先按像素距离找锚点，再回退到帧对齐；若先用 snapFrame 作 bestDist，磁吸几乎永不触发
        if (!T.magnet) return snapFrame(t);
        const total = Math.max(0, duration());
        if (snapCacheEpoch !== snapEpoch || snapCacheTotal !== total || !snapCacheAnchors) {
            const anchors = [0, total, ...(T.state.split_points || [])];
            for (const lane of [...(T.state.video_tracks || []), ...(T.state.audio_tracks || [])])
                for (const clip of lane || []) {
                    anchors.push(clip.tl_start, clipEnd(clip));
                }
            try {
                for (const [a] of (typeof intervals === "function" ? intervals() : [])) anchors.push(a);
            } catch {}
            snapCacheAnchors = anchors;
            snapCacheEpoch = snapEpoch;
            snapCacheTotal = total;
        }
        // 播放头游标实时变化，不放进缓存；拖动播放头本身时排除，避免自吸。
        const anchors = withPlayhead ? [...snapCacheAnchors, T.playhead] : snapCacheAnchors;
        const threshPx = 26;  // 吸附范围减半；未命中仍按24FPS逐帧定位
        let best = null, bestDistPx = Infinity;
        for (const anchor of anchors) {
            if (anchor == null || !Number.isFinite(anchor)) continue;
            const distPx = Math.abs(anchor - t) * T.pxs;
            if (distPx < bestDistPx && distPx <= threshPx) {
                best = anchor;
                bestDistPx = distPx;
            }
        }
        const picked = best != null ? best : snapFrame(t);
        return Math.max(0, Math.min(picked, total || picked));
    };

    function render() {
        const hasVideoClip = (T.state.video_tracks || []).some(lane => lane?.length);
        const hasVideoSource = T.state.sources.some(source => source.kind === "video");
        // 预览区始终展示；时间线上没有视频片段时清掉残留画面（避免「轨上空、上头还在播」）
        player.hidden = false;
        player.classList.toggle("is-empty", !hasVideoClip);
        if (!hasVideoClip && !root._adSourcePreviewId) {
            playerTitle.textContent = "视频预览";
            playerMeta.textContent = "";
            playerVideo.pause();
            playerVideo.removeAttribute("src");
            playerVideo.load?.();
            delete playerVideo.dataset.src;
            showPlayerPlaceholder(hasVideoSource
                ? "拖动播放头对准视频片段显示画面"
                : "一帧光影，一段声音，皆可成为开端。");
        }
        const total = duration();
        // 只画有片段的轨；编号按可见顺序 1、2、3，绝不出现「视频5」空壳
        // 必须先建好轨头再量 headOffset / 画标尺，否则无音视频时刻度零点会少算轨头宽度
        const plan = [["video_tracks", "视频"], ["audio_tracks", "音频"]];
        for (const element of canvas.querySelectorAll(".h3-lane")) element.remove();
        const hasAvLane = [...(T.state.video_tracks || []), ...(T.state.audio_tracks || [])].some(l => l?.length);
        for (const [key, label] of plan) {
            const lanes = Array.isArray(T.state[key]) ? T.state[key] : [];
            let visibleNo = 0;
            for (let i = 0; i < lanes.length && visibleNo < MAX_LANES; i++) {
                if (!(lanes[i]?.length)) continue;
                visibleNo += 1;
                const lane = document.createElement("div");
                lane.className = "h3-lane";
                lane.dataset.kind = key;
                lane.dataset.index = String(i);
                lane.innerHTML = `<div class="h3-head">${label}${i+1}</div><div class="h3-body"></div>`;
                canvas.append(lane);
            }
        }
        let genBody = null;
        if (!hasAvLane) {
            const lane = document.createElement("div"); lane.className = "h3-lane";
            lane.innerHTML = `<div class="h3-head">生成段</div><div class="h3-body"></div>`;
            genBody = lane.querySelector(".h3-body");
            canvas.append(lane);
        }
        const offset = headOffset();
        const width = Math.max(view.clientWidth - 80, offset + timeToX(total) + 160);
        canvas.style.width = `${width}px`;
        // 标尺（轨道建好后绘制，零点与轨道内容区对齐）
        ensureRulerCoverage();
        if (genBody) {
            const body = genBody, iv = intervals();
            if (!iv.length) body.innerHTML = '<div class="h3-lane-empty">填每段目标帧数和分段数，再点应用分段；之后可拖左右边改单段时长</div>';
            for (let i = 0; i < iv.length; i++) {
                const [segStart, segEnd] = iv[i];
                const clip = document.createElement("div");
                clip.className = "h3-generation-clip h3-clip g";
                clip.dataset.seg = String(i);
                clip.dataset.id = generationClips(T.state)[i].id;
                clip.classList.toggle("selected", T.selected.has(clip.dataset.id));
                clip.style.left = `${timeToX(segStart)}px`;
                clip.style.width = `${Math.max(6, timeToX(segEnd - segStart))}px`;
                clip.innerHTML = `<span class="label">第${i + 1}段 · ${Math.round((segEnd - segStart) * FPS)}帧</span><div class="edge l"></div><div class="edge r"></div>`;
                clip.ondblclick = () => {
                    T.playhead = Math.max(segStart + FRAME, Math.min(segEnd - FRAME, T.playhead));
                    if (T.playhead <= segStart + 0.5 * FRAME || T.playhead >= segEnd - 0.5 * FRAME)
                        T.playhead = snapFrame((segStart + segEnd) / 2);
                    editGeneration("split");
                };
                clip.onpointerdown = event => {
                    event.preventDefault();
                    event.stopPropagation();
                    const resume = T.playing;
                    if (resume) stopPlayback();
                    const edge = event.target.classList.contains("edge")
                        ? (event.target.classList.contains("l") ? "l" : "r") : null;
                    const selectedId = generationClips(T.state)[i].id;
                    if (event.ctrlKey || event.metaKey) {
                        if (T.selected.has(selectedId)) T.selected.delete(selectedId); else T.selected.add(selectedId);
                    } else if (edge || !T.selected.has(selectedId)) T.selected = new Set([selectedId]);
                    playedSelectionKey = "";
                    selectSegmentAt((segStart + segEnd) / 2);
                    // 单击/拖边只选中分段，不移动白色游标（游标保持原位）
                    renderClock();
                    renderOverlays();
                    syncButtons();
                    for (const el of canvas.querySelectorAll(".h3-generation-clip"))
                        el.classList.toggle("selected", T.selected.has(el.dataset.id));
                    if (event.ctrlKey || event.metaKey) {if(resume)void play({resume:true});return;}
                    if (!edge && T.selected.size>1) startGroupMove(event,generationClips(T.state)[i],{generation:true,resume});
                    else startGenSegResize(i, edge, event);
                };
                clip.onmousedown = event => { event.preventDefault(); event.stopPropagation(); };
                body.append(clip);
            }
        }
        const sources = sourcesOf();
        // plan 现为 [key, label]；必须按 state 轨索引画片段（勿再用 group.count，否则轨头在、片子全丢）
        for (const [key] of plan) {
            const lanes = T.state[key] || [];
            for (let i = 0; i < lanes.length; i++) {
                if (!(lanes[i]?.length)) continue;
                const laneEl = canvas.querySelector(`.h3-lane[data-kind="${key}"][data-index="${i}"]`);
                if (!laneEl) continue;
                const body = laneEl.querySelector(".h3-body");
                const isVideo = key === "video_tracks";
                for (const clip of lanes[i]) {
                    const source = sources[clip.source];
                    const element = document.createElement("div");
                    element.className = `h3-clip ${isVideo ? "v" : "a"}`;
                    element.dataset.id = clip.id;
                    element.style.left = `${timeToX(clip.tl_start)}px`;
                    element.style.width = `${Math.max(6, timeToX(clip.src_out - clip.src_in))}px`;
                    element.classList.toggle("selected", T.selected.has(clip.id));
                    element.innerHTML = `<div class="label">${source?.name || "?"}</div>` +
                        `<canvas width="1" height="1" ${isVideo ? `data-thumb="${clip.source}"` : ""}></canvas>` +
                        `<div class="edge l"></div><div class="edge r"></div>`;
                    element.ondblclick = () => splitClipAt(clip, T.playhead);
                    body.append(element);
                }
            }
        }
        renderOverlays();
        if (!interactionLock) renderPool();  // 拖拽中不重建图片池
        renderClock({ seekPreview: !interactionLock });
        syncButtons();
        renderFramePlan();
        if (lastIntervals === null) lastIntervals = snapshotIntervals();  // 首次绘制记录段线基线
        syncViewHeight();  // 每次重绘后重算高度（缩放/无素材都不留底空白）
        syncTrackMuteButtons();
        syncTrackVisibilityButtons();
        paintViewportMedia();
    }
    // 缩放只改几何位置，不重建轨道、图片池、片段事件或分段参数表。
    function renderTimelineZoom() {
        canvas.style.width = `${Math.max(view.clientWidth - 80, headOffset() + timeToX(duration()) + 160)}px`;
        const clips = new Map([...T.state.video_tracks, ...T.state.audio_tracks,
            hasAvClips() ? [] : generationClips(T.state)].flat().map(clip => [clip.id, clip]));
        for (const element of canvas.querySelectorAll(".h3-clip")) {
            const clip = clips.get(element.dataset.id);
            if (!clip) continue;
            element.style.left = `${timeToX(clip.tl_start)}px`;
            element.style.width = `${Math.max(6, timeToX(clip.src_out - clip.src_in))}px`;
        }
        renderOverlays();
    }
    const paintViewport = () => ({left: view.scrollLeft, width: view.clientWidth, offset: headOffset()});
    function clipPaintRegion(clip, viewport = paintViewport()) {
        const fullWidth = Math.max(8, Math.round(timeToX(clip.src_out - clip.src_in)));
        const left = viewport.offset + timeToX(clip.tl_start);
        const start = Math.max(0, Math.floor(viewport.left - left - 160));
        const end = Math.min(fullWidth, Math.ceil(viewport.left + viewport.width - left + 160));
        return end > start ? {start, width: end - start, fullWidth} : null;
    }
    function paintViewportMedia() {
        if (interactionLock || previewDisposed) return;
        const clips = new Map([...T.state.video_tracks, ...T.state.audio_tracks].flat().map(clip => [clip.id, clip]));
        const sources = sourcesOf();
        const viewport = paintViewport();  // 集中读布局，避免每画一张缩略图都强制重排。
        for (const element of canvas.querySelectorAll(".h3-clip canvas")) {
            const clip = clips.get(element.parentElement.dataset.id);
            if (!clip) continue;
            const region = clipPaintRegion(clip, viewport);
            if (!region) {
                // 释放画布像素；保留片段壳和交互，滚入窗口再补绘。
                if (element.width !== 1) { element.width = 1; element.height = 1; }
                element.style.width = "0px";
                delete element.dataset.paintKey;
                continue;
            }
            if (element.dataset.thumb) { paintThumbs(element, clip, region); void thumbsFor(sources[clip.source]); }
            else void drawWave(element, sources[clip.source], clip, region);
        }
    }
    function ensureRulerCoverage() {
        const viewEl = root.querySelector("[data-view]");
        if (!viewEl) return;
        const width = viewEl.clientWidth;
        if (!width) return;
        const step = rulerStep();
        const offset = headOffset();
        // 只绘制可见窗口及两侧余量；总时长再长也不截断后半段。
        const first = Math.max(0, Math.floor(xToTime(Math.max(0, viewEl.scrollLeft - offset)) / step) - 2);
        const last = Math.ceil(xToTime(Math.max(0, viewEl.scrollLeft + width - offset)) / step) + 2;
        const range = `${first}:${last}:${step}:${T.pxs}:${offset}`;
        if (ruler.dataset.range === range && ruler.childElementCount) return;
        const fragment = document.createDocumentFragment();
        for (let index = first; index <= last; index++) {
            const t = index * step;
            const tick = document.createElement("div");
            const majorUnit = step >= 5 ? 15 : 5;
            const major = Math.abs(t / majorUnit - Math.round(t / majorUnit)) < 1e-6;
            tick.className = `h3-tick${major ? " major" : ""}`;
            tick.dataset.time = String(t);
            tick.style.left = `${offset + timeToX(t)}px`;
            if (major || step >= 5) { const label = document.createElement("span"); label.textContent = fmtClock(t); tick.append(label); }
            fragment.append(tick);
        }
        ruler.replaceChildren(fragment);
        ruler.dataset.range = range;
    }
    function renderOverlays() {
        const total = duration(), offset = headOffset();
        for (const tick of ruler.querySelectorAll(".h3-tick"))
            tick.style.left = `${offset + timeToX(Number(tick.dataset.time))}px`;
        // 不再画灰蓝段罩：跑哪一段由选中的素材框决定
        for (const stale of canvas.querySelectorAll(":scope > .h3-segshade, :scope > .h3-segline, .h3-smartcut")) stale.remove();
        const candidateIds = new Set(activeClips(T.state,null,T.selected).map(clip=>clip.id));
        for (const frame of T.smartCuts || []) {
            if (!T.smartOwners?.[frame]?.some(id=>candidateIds.has(id))) continue;
            const marker = document.createElement("div");
            marker.className = "h3-smartcut";
            const t = frame / FPS;
            marker.style.left = `${offset + timeToX(t)}px`;
            marker.title = `${t.toFixed(2)} 秒（单击定位，双击删除，拖动调整）`;
            marker.dataset.frame = String(frame);
            marker.addEventListener("click", event => {
                event.stopPropagation();
                T.playhead = t;
                // 单击只移动白线，不能重建当前元素，否则第二次点击无法组成双击。
                const line=canvas.querySelector(":scope > .h3-playhead");
                if(line)line.style.left=(headOffset()+timeToX(t))+"px";
                renderClock({seekPreview:false});void updatePreviewAt(t);
            });
            marker.addEventListener("dblclick", event => {
                event.stopPropagation();
                moveCandidateFrame(frame,null);
                root._adH3SmartCuts = [...T.smartCuts];
                renderOverlays(); paintSmartInfo();
            });
            marker.addEventListener("pointerdown", event => {
                if (event.button !== 0) return;
                event.stopPropagation();
                const resume = T.playing;
                if (resume) stopPlayback();
                clearSourcePreview(root);
                beginInteraction();
                const startX = event.clientX;
                const startTime = t;
                let currentFrame = frame;
                const onMove = moveEvent => {
                    const next = Math.max(1 / FPS, Math.min(duration() - 1 / FPS, startTime + (moveEvent.clientX - startX) / (T.pxs * viewScale())));
                    const snapped = Math.round(next * FPS) / FPS;
                    const nextFrame = Math.round(snapped * FPS);
                    if(nextFrame===currentFrame)return;
                    moveCandidateFrame(currentFrame,nextFrame);
                    currentFrame = nextFrame; root._adH3SmartCuts = [...T.smartCuts];
                    T.playhead = snapped;
                    renderOverlays();
                    renderClock({seekPreview:true});
                };
                const onUp = () => {
                    window.removeEventListener("pointermove", onMove);
                    window.removeEventListener("pointerup", onUp);
                    window.removeEventListener("pointercancel", onUp);
                    endInteraction();
                    renderClock({seekPreview:true});
                    paintSmartInfo();
                    if (resume) void play({resume:true});
                };
                window.addEventListener("pointermove", onMove);
                window.addEventListener("pointerup", onUp);
                window.addEventListener("pointercancel", onUp);
            });
            marker.tabIndex=0;marker.setAttribute("role","slider");marker.setAttribute("aria-label","候选切点 "+frame+" 帧");marker.setAttribute("aria-valuenow",String(frame));
            marker.addEventListener("keydown",event=>{
                if(["Delete","Backspace"].includes(event.key)){event.preventDefault();moveCandidateFrame(frame,null);}
                else if(["ArrowLeft","ArrowRight"].includes(event.key)){event.preventDefault();const next=Math.max(1,Math.min(Math.round(duration()*FPS)-1,frame+(event.key==="ArrowLeft"?-1:1)));moveCandidateFrame(frame,next);}
                else if(event.key==="Enter"){event.preventDefault();T.playhead=t;clearSourcePreview(root);void updatePreviewAt(t);}
                else return;
                root._adH3SmartCuts=[...T.smartCuts];renderOverlays();paintSmartInfo();
            });
            canvas.append(marker);
        }
        let playhead = canvas.querySelector(":scope > .h3-playhead");
        if (!playhead) {
            playhead = document.createElement("div");
            playhead.className = "h3-playhead";
            canvas.append(playhead);
        }
        playhead.style.left = `${offset + timeToX(Math.max(0, Math.min(T.playhead, total)))}px`;
        ensureRulerCoverage();
    }
    // 轻量刷新：只改选中态，不重建 DOM（点选/框选用）
    function paintSelection() {
        for (const el of canvas.querySelectorAll(".h3-clip"))
            el.classList.toggle("selected", T.selected.has(el.dataset.id));
        for (const el of canvas.querySelectorAll(".h3-generation-clip"))
            el.classList.toggle("selected", T.selected.has(el.dataset.id));
    }
    // 拖拽中：只挪现有片段 left/width/所在轨，绝不拆建轨道/标尺/缩略图（卡顿主因）
    function patchClipsDuringDrag() {
        const byId = new Map();
        for (const el of canvas.querySelectorAll(".h3-clip")) byId.set(el.dataset.id, el);
        for (const key of ["video_tracks", "audio_tracks"]) {
            (T.state[key] || []).forEach((lane, i) => {
                let laneEl = canvas.querySelector(`.h3-lane[data-kind="${key}"][data-index="${i}"]`);
                if (!laneEl && lane?.length) {
                    laneEl = document.createElement("div");
                    laneEl.className = "h3-lane";
                    laneEl.dataset.kind = key;
                    laneEl.dataset.index = String(i);
                    laneEl.innerHTML = `<div class="h3-head">${key === "video_tracks" ? "视频" : "音频"}${i + 1}</div><div class="h3-body"></div>`;
                    if (key === "video_tracks") {
                        const firstAudio = canvas.querySelector(".h3-lane[data-kind=audio_tracks]");
                        if (firstAudio) firstAudio.before(laneEl); else canvas.append(laneEl);
                    } else canvas.append(laneEl);
                }
                if (!laneEl) return;
                laneEl.dataset.empty = lane?.length ? "0" : "1";
                if (!lane?.length) return;
                const body = laneEl.querySelector(".h3-body");
                for (const clip of lane) {
                    const el = byId.get(clip.id);
                    if (!el) continue;
                    if (el.parentElement !== body) body.append(el);
                    el.style.left = `${timeToX(clip.tl_start)}px`;
                    el.style.width = `${Math.max(6, timeToX(clip.src_out - clip.src_in))}px`;
                    el.classList.toggle("selected", T.selected.has(clip.id));
                    byId.delete(clip.id);
                }
            });
        }
        renderOverlays();
    }
    let previewSeekTimer = 0;

    function renderClock({ seekPreview = true } = {}) {
        const cur = Math.max(0, Math.round(T.playhead * FPS));
        const tot = Math.max(0, Math.round(duration() * FPS));
        const clock = $("[data-clock]");
        const text = `${cur} / ${tot}`;
        if (clock && clock.textContent !== text) clock.textContent = text;
        renderFrameSummary(cur,tot);
        if (!seekPreview) return;
        if (interactionLock) {
            // 连续拖动也持续更新，下一屏幕帧只取最新游标；不等鼠标松开。
            if (!previewSeekTimer) previewSeekTimer = requestAnimationFrame(() => {
                previewSeekTimer = 0;
                if (!previewDisposed) void updatePreviewAt(T.playhead);
            });
        } else {
            if (previewSeekTimer) cancelAnimationFrame(previewSeekTimer);
            previewSeekTimer = 0;
            void updatePreviewAt(T.playhead);
        }
    }

    function renderPool() {
        pool.replaceChildren();
        const visibleSources=T.state.sources.filter(source=>matchesSource(root,source));
        pool.classList.toggle("is-empty", !visibleSources.length);
        const batchSegs = T.state.path_source ? Math.max(0, Number(T.batchCount) || 0) : 0;
        const count = Math.max(1, T.state.path_source ? batchSegs : intervals().length);
        root.querySelector("[data-glass-count]").textContent = T.state.path_source ? batchSegs : intervals().length;
        if (root._adSourcePreviewId && !T.state.sources.some(item=>item.id===root._adSourcePreviewId)) clearSourcePreview(root);
        for (const source of visibleSources) {
            const card = document.createElement("div");
            card.className = source.kind === "image" ? "h3-shot" : "h3-source-card";
            card.dataset.sourceId = source.id;
            card.classList.toggle("is-preview", root._adSourcePreviewId===source.id);
            const show = () => {
                stopPlayback();
                previewSource(root,source,mediaURL(source));
                pool.querySelectorAll("[data-source-id]").forEach(item=>item.classList.toggle("is-preview",item.dataset.sourceId===source.id));
            };
            if (source.kind === "image") {
                const image=document.createElement("img");
                const preview=source.preview&&typeof source.preview==="object"&&source.preview.file?{...source,...source.preview}:source;
                const originalURL=mediaURL(source);let triedOriginal=preview===source;
                image.alt="参考图";image.decoding="async";image.tabIndex=0;image.setAttribute("role","button");image.setAttribute("aria-label","预览 "+source.name);
                image.onerror=()=>{if(!triedOriginal){triedOriginal=true;image.src=originalURL;return;}image.hidden=true;const unavailable=document.createElement("button");unavailable.type="button";unavailable.className="h3-thumb-unavailable";unavailable.textContent="图片暂不可预览";unavailable.onclick=show;image.after(unavailable);};
                image.src=mediaURL(preview);image.onclick=show;image.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();show();}};card.append(image);
                const name=document.createElement("span");name.className="h3-shot-name";name.textContent=source.name;name.title=source.name;card.append(name);
                const setting=T.state.images.find(item=>item.source===source.id);
                const select=document.createElement("select");select.setAttribute("aria-label",source.name+"的参考范围");
                select.add(new Option("禁止输出","-2"));select.add(new Option("替换图片…","__replace__"));select.add(new Option("全局参考","-1"));
                for(let i=1;i<=count;i++)select.add(new Option("第 "+i+" 段参考",String(i)));
                if(setting?.segment>count)select.add(new Option("第 "+setting.segment+" 段参考（当前不存在）",String(setting.segment)));
                select.value=String(setting?.segment===0?-1:setting?.segment??-1);
                select.onchange=()=>{
                    if(select.value==="__replace__"){
                        // 「替换图片」只是动作入口，不写入参考范围；选完文件后原地替换这张参考图。
                        select.value=String(setting?.segment===0?-1:setting?.segment??-1);
                        pickReplacementImage(source.id);
                        return;
                    }
                    pushUndo();if(setting)setting.segment=Number(select.value);else T.state.images.push({id:uid("img"),source:source.id,segment:Number(select.value)});afterEdit();
                };card.append(select);
            } else {
                const button=document.createElement("button");button.type="button";button.className="h3-source-preview";button.setAttribute("aria-label","预览 "+source.name);
                const thumb=document.createElement("span");thumb.className="h3-source-kind";thumb.textContent=source.kind==="video"?"视频素材":"音频素材";
                const name=document.createElement("span");name.className="h3-source-name";name.textContent=source.name;name.title=source.name;button.append(thumb,name);button.onclick=show;card.append(button);
            }
            const remove=document.createElement("button");remove.type="button";remove.className="h3-source-remove";remove.textContent="×";remove.title="移除素材";remove.setAttribute("aria-label","移除 "+source.name);remove.onclick=()=>removeSource(source.id);card.append(remove);pool.append(card);
        }
        const addImage=document.createElement("button");addImage.type="button";addImage.className="h3-image-add";addImage.dataset.load="image";addImage.textContent="＋ 加载图片";addImage.setAttribute("aria-label","添加参考图片");addImage.title="点击上传图片，也可将图片拖入此区域";pool.append(addImage);
        applyPoolExtra(Number(node._adH3PoolExtra) || 0);
        scheduleLayout(0);syncViewHeight();
        requestAnimationFrame(()=>{if(node.graph)applyLayoutHeight();});
    }
    root.addEventListener("ad-h3-library-filter",renderPool);
    // H3 合法帧数 = 17k+5：吸附到最接近的合法值（可增可减，不再只能往上取）
    const align17 = value => {
        const base = Math.max(5, Math.round(Number(value) || 0));
        const steps = Math.max(0, Math.round((base - 5) / 17));
        return 5 + 17 * steps;
    };
    // 帧单位时输入框本身也吸附到 17k+5（失焦/回车/点箭头后生效）；步长=17，上下箭头每次 ±17 始终合法
    const everyInput = $("[data-every]");
    if (everyInput) {
        everyInput.step = T.unit === "s" ? "0.001" : "17";
        everyInput.addEventListener("change", () => {
            if (T.unit !== "f") return;
            everyInput.step = "17";
            const raw = Number(everyInput.value);
            const next = Math.max(5, align17(raw));
            everyInput.value = String(next);
        });
        // 上下调整统一走前后 −/+ 按钮：滚轮不再改动数值。
        everyInput.addEventListener("wheel", event => { event.preventDefault(); }, { passive: false });
        for (const button of root.querySelectorAll("[data-every-step]")) {
            button.addEventListener("click", event => {
                event.preventDefault();
                event.stopPropagation();
                const step = Number(button.dataset.everyStep) || 0;
                if (!step || !everyInput) return;
                const raw = Number(everyInput.value);
                const current = align17(T.unit === "s" ? raw * FPS : raw);
                const next = Math.max(5, current + 17 * step);
                everyInput.value = T.unit === "s" ? String(Number((next / FPS).toFixed(6))) : String(next);
                everyInput.dispatchEvent(new Event("change", { bubbles: true }));
                try { syncRecipePreview(); } catch (_error) {}
                try { hint(`每段长度：${next} 帧（17k+5）`); } catch (_error2) {}
            });
        }
    }
    function readRecipeInputs() {
        const raw = Number($("[data-every]")?.value);
        const frames = align17(T.unit === "s" ? raw * FPS : raw);
        const count = Number($("[data-count]")?.value);
        const byCount = Number.isInteger(count) && count >= 1 && count <= 1000;
        return { raw, frames, count, byCount };
    }
    function syncRecipePreview() {
        const el = $("[data-recipe-preview]");
        if (!el) return;
        const { frames, count, byCount } = readRecipeInputs();
        const hasClips = [...T.state.video_tracks, ...T.state.audio_tracks].some(lane => lane.length);
        const selected = selectedEditableClips().length > 0;
        if (!(frames >= 1)) { el.textContent = ""; return; }
        // 纯图片：段数由「分段数」控制，已有段长保留，只在末尾增删（v0.4.23 用户新规则）。
        if (!hasClips && imageOnlyClips(T.state, frames)) {
            const existing = (Array.isArray(T.state.generation_clips) ? T.state.generation_clips : [])
                .filter(clip => clip && Number(clip.src_out) > Number(clip.src_in || 0)).length;
            if (!byCount) { el.textContent = "纯图片请填分段数（≥1）"; return; }
            const added = Math.max(0, count - existing), removed = Math.max(0, existing - count);
            el.textContent = `纯图片：生成段 ${existing} → ${count} 段`
                + (added ? `（末尾新增 ${added} 段 × ${frames} 帧）` : removed ? `（删除末尾 ${removed} 段）` : "（保留原段长）");
            return;
        }
        if (!hasClips) {
            if (!byCount) { el.textContent = "纯生成请填分段数（≥1）"; return; }
            const total = frames * count;
            el.textContent = `将建立 ${count}×${frames}帧 = ${total}帧（${(total / FPS).toFixed(2)}秒）`;
            return;
        }
        if (byCount) {
            const totalF = Math.max(1, Math.round(duration() * FPS));
            const each = Math.max(1, Math.round(totalF / count));
            el.textContent = selected
                ? `将把选中片段均分成 ${count} 段`
                : "请先选中可见片段，再应用分段";
            return;
        }
        if (count === 0) {
            el.textContent = selected
                ? `将按每 ${frames} 帧切开选中片段`
                : "请先选中可见片段，再应用分段";
            return;
        }
        el.textContent = "";
    }
    function syncButtons() {
        const pathBatch = !!String(T.state.path_source || "").trim();
        for (const control of root.querySelectorAll('[data-every],[data-count],[data-unit],[data-every-step],[data-apply-split],[data-max-frames]'))
            control.disabled = pathBatch;
        const canDeleteGen = typeof hasAvClips === "function" && !hasAvClips()
            && T.selected.size > 0;
        const editableCount = selectedEditableClips().length;
        $("[data-separate]").disabled = !activeClips(T.state,"video_tracks",T.selected).length;
        $("[data-delete]").disabled = !(editableCount || canDeleteGen);
        $("[data-remove-source]").disabled = !editableCount;
        $("[data-merge]").disabled = (hasAvClips()?editableCount:T.selected.size)<2;
        const batchSegs = T.state.path_source ? Math.max(0, Number(T.batchCount) || 0) : 0;
        const actualSegs = Math.max(0, pathBatch ? batchSegs : intervals().length);
        const { count: want, byCount } = readRecipeInputs();
        const custom = !!T.state.segment_recipe?.custom;
        let recipeNote = pathBatch ? "（路径批处理：逐个原文件，分段设置不适用）" : "";
        if (!pathBatch && custom && byCount && want !== actualSegs)
            recipeNote = "（已手动调段；再点应用会按左侧配方重算）";
        else if (!pathBatch && !custom && byCount && want >= 1 && actualSegs !== want)
            recipeNote = `（配方 ${want} 段，点应用分段生效）`;
        $("[data-actual-count]").textContent = `实际${actualSegs}段${recipeNote}`;
        syncRecipePreview();
        syncHistory();
        renderSelectionSummary();
    }
    // 视频/音频的片段全部删除时，素材连同其参考输出口一并移除（与图片池 ✕ 语义对齐）。
    const pruneSources = () => {
        const clipSources = new Set([...T.state.video_tracks, ...T.state.audio_tracks].flat().map(c => c.source));
        const before = T.state.sources.length;
        T.state.sources = T.state.sources.filter(s => s.kind === "image" || s.reference_only || clipSources.has(s.id));
        if (T.state.sources.length < before)
            hint("片段已全部删除的素材连同其参考输出口一并移除；重新加载可再次使用。");
    };

    function linkedSegment() {
        const links = node.outputs?.[0]?.links || [];
        return links.map(id => node.graph?.links?.get?.(id) || node.graph?.links?.[id])
            .map(link => link && node.graph?.getNodeById(link.target_id))
            .find(target => target?.comfyClass === "AidenH3Segment" || target?.type === "AidenH3Segment");
    }
    function segmentWidget(name, linked = linkedSegment()) {
        return linked?.widgets?.find(widget => widget.name === name);
    }
    function clampMaxFrames(value) {
        const n = Math.round(Number(value));
        if (!Number.isFinite(n)) return DEFAULT_MAX_FRAMES;
        const capped = Math.max(5, Math.min(3600, n));
        // H3 合法请求帧为 17k+5；非法值向下取整到最近合法值
        return 5 + 17 * Math.floor((capped - 5) / 17);
    }
    function resolveMaxFrames(linked = linkedSegment()) {
        const fromSeg = segmentWidget("max_frames", linked)?.value;
        if (fromSeg != null && fromSeg !== "") return clampMaxFrames(fromSeg);
        if (T.state.max_frames != null && T.state.max_frames !== "") return clampMaxFrames(T.state.max_frames);
        return DEFAULT_MAX_FRAMES;
    }
    function writeMaxFramesToSegment(value, linked = linkedSegment()) {
        const widget = segmentWidget("max_frames", linked);
        if (!widget) return;
        const next = clampMaxFrames(value);
        if (Number(widget.value) === next) return;
        widget.value = next;
        try { widget.callback?.(next, undefined, node, { widget }); } catch {}
        linked?.setDirtyCanvas?.(true, true);
    }
    function syncMaxFramesControl(value, { force = false } = {}) {
        const next = clampMaxFrames(value);
        const input = $("[data-max-frames]");
        if (input && (force || document.activeElement !== input)) input.value = String(next);
    }
    function applyMaxFrames(raw, { syncSegment = true } = {}) {
        const typed = Math.round(Number(raw));
        const next = clampMaxFrames(raw);
        T.state.max_frames = next;
        syncMaxFramesControl(next, { force: true });
        if (syncSegment) writeMaxFramesToSegment(next);
        renderFramePlan();
        try { updateMaterialOutputs(node, T.state); } catch {}
        node.setDirtyCanvas(true, true);
        if (Number.isFinite(typed) && typed !== next)
            hint(`单段上限已按 17k+5 向下取整：${typed} → ${next}`);
        return next;
    }
    let lastPlanSig = "";
    function renderFramePlan({ force = false } = {}){
        if (T.state.path_source) {
            $("[data-frame-plan-note]").textContent = "素材路径批处理保留完整原文件及其帧率、尺寸，不按分段配方或H3帧数规则处理。";
            $("[data-frame-plan-body]").innerHTML = "";
            lastPlanSig = ""; return;
        }
        if (interactionLock && !force) return;
        const linked = linkedSegment();
        const maxFrames = resolveMaxFrames(linked);
        // 已连接时以当前段为准回写清单状态；编辑输入框时不抢焦点
        if (linked && document.activeElement !== $("[data-max-frames]")) {
            if (Number(T.state.max_frames) !== maxFrames) T.state.max_frames = maxFrames;
            syncMaxFramesControl(maxFrames);
        } else if (!linked) {
            syncMaxFramesControl(maxFrames);
        }
        const settings = Object.fromEntries(["overlap_frames","guard_frames","reanchor_every"].map(name => [name, segmentWidget(name, linked)?.value]));
        settings.max_frames = maxFrames;
        const rows = framePlan(T.state, settings);
        const sig = `${maxFrames}|${linked?.id ?? ""}|${rows.map(r => `${r.target}:${r.request}:${r.valid?1:0}`).join(",")}|${T.state.duration}|${(T.state.split_points||[]).join(",")}`;
        if (!force && sig === lastPlanSig) return;
        lastPlanSig = sig;
        const note = '成片按目标帧数裁齐。'
            + (linked ? '按已连接的当前段参数计算（上限可在清单库改，同步到当前段）。'
                      : '未连接时按默认上限预估；上限用右侧数字框。');
        const body = rows.length
            ? '<table><thead><tr><th>段</th><th>成片目标</th><th>请求H3</th></tr></thead><tbody>'
              + rows.map((row,i)=>`<tr class="${row.valid?'':'error'}"><td>${i+1}</td><td>${row.target}帧 · ${(row.target/FPS).toFixed(3)}秒</td><td>${row.request}帧${row.valid?'':'（超出上限）'}</td></tr>`).join('')
              + '</tbody></table>'
            : '<div>无时间线素材时，直接按“每段目标帧数 × 分段数”建立生成区间。</div>';
        const noteEl = $("[data-frame-plan-note]");
        const bodyEl = $("[data-frame-plan-body]");
        if (noteEl && noteEl.textContent !== note) noteEl.textContent = note;
        if (bodyEl && bodyEl.innerHTML !== body) {
            bodyEl.innerHTML = body;
            if (!interactionLock) scheduleLayout(80);
        }
    }
    function syncViewHeight() {
        // 所有模式都保留上下各一条空轨操作区，和节点下方多余留白分开处理。
        const LANE = 54, MAX_VIEW = 520;
        const gutter = LANE;
        if (blank) blank.style.display = "block";
        canvas.style.paddingBottom = `${LANE}px`;
        const rulerH = ruler?.offsetHeight || 30;
        const lanes = [...canvas.querySelectorAll(".h3-lane")];
        const lanesH = lanes.reduce((sum, el) => sum + (el.offsetHeight || LANE), 0) || LANE;
        const needed = rulerH + gutter + lanesH + gutter;
        const h = Math.min(MAX_VIEW, Math.max(rulerH + lanesH, needed) + 2);
        view.style.height = `${h}px`;
        view.style.maxHeight = `${MAX_VIEW}px`;
        if (needed + 2 > h) view.scrollTop = Math.max(0, needed + 2 - h);
    }

    function compactTracks() {
        // 压紧有片段的轨并去掉中间空壳；再补齐到 MAX_LANES 条空轨，避免后端校验「轨道数不足」
        for (const key of ["video_tracks", "audio_tracks"]) {
            const flags = (T.state.muted_tracks || {})[key] || [];
            const active = (T.state[key] || []).map((lane, index) => ({lane, muted: !!flags[index], disabled: trackDisabled(T.state, key, index)}))
                .filter(item => item.lane?.length).slice(0, MAX_LANES);
            const packed = (Array.isArray(T.state[key]) ? T.state[key] : [])
                .filter(lane => Array.isArray(lane) && lane.length)
                .slice(0, MAX_LANES);
            while (packed.length < MAX_LANES) packed.push([]);
            T.state[key] = packed;
            T.state.muted_tracks ||= {};
            T.state.muted_tracks[key] = Array.from({length:MAX_LANES}, (_, index) => active[index]?.muted || false);
            T.state.disabled_tracks ||= {};
            T.state.disabled_tracks[key] = Array.from({length:MAX_LANES}, (_, index) => active[index]?.disabled || false);
        }
    }
    function currentSegmentFrames() {
        const raw = Number($("[data-every]")?.value);
        if (!Number.isFinite(raw) || raw < 1) return 0;
        return Math.max(1, Math.round(T.unit === "s" ? raw * FPS : raw));
    }
    // 纯图片：用户未点过「应用分段」、也未手动调过生成段之前，生成段跟随图片数量（首次载入按图片数自动建段）；
    // 一旦手动设置（manual_count/custom），增删图片不再改变段数，段数只由「分段数 + 应用分段」决定。
    function applyImageOnlySegments() {
        if (!followsImageCount(T.state.segment_recipe)) return false;
        const plan = imageOnlyClips(T.state, currentSegmentFrames());
        if (!plan) return false;
        T.state.generation_clips = plan.clips;
        T.state.duration = plan.duration;
        T.state.split_points = [];
        T.state.segmentation = "clips";
        T.state.segment_recipe = { ...(T.state.segment_recipe || {}), frames: plan.defaultFrames, count: plan.count, unit: T.unit, custom: false };
        const countInput = $("[data-count]");
        if (countInput) countInput.value = String(plan.count);
        return true;
    }
    // 素材准备里没有任何素材时：分段数只以「正向提示词」内容里行首 # 的段数为准（负向/前缀不参与）。
    function promptOnlySegments() {
        if ((T.state.sources || []).length) return null;
        if ([...T.state.video_tracks, ...T.state.audio_tracks].some(lane => lane && lane.length)) return null;
        const segmentNode = linkedPromptSegment(node);
        const text = String(segmentNode?.widgets?.find(item => item.name === "正向提示词")?.value ?? "");
        const parts = [];
        let current = [];
        for (const line of text.split(/\r?\n/)) {
            const stripped = line.replace(/^\s+/, "");
            if (stripped.startsWith("#")) {
                if (current.length) parts.push(current.join("\n").trim());
                current = [stripped.slice(1)];
            } else if (current.length) current.push(line);
        }
        if (current.length) parts.push(current.join("\n").trim());
        const count = parts.filter(Boolean).length;
        if (count) return count;
        return text.trim() ? 1 : 0;
    }
    function applyPromptOnlySegments({promptChanged = false} = {}) {
        const count = promptOnlySegments();
        if (!count) return false;
        const frames = currentSegmentFrames() || Math.max(5, Math.round(Number(T.state.segment_recipe?.frames) || DEFAULT_SEG_FRAMES));
        const recipe = T.state.segment_recipe || {};
        // A saved explicit delete-to-empty stays empty; editing the positive prompt is a new intent.
        if (!promptChanged && recipe.custom && Array.isArray(T.state.generation_clips) && !T.state.generation_clips.length) return false;
        const existing = (Array.isArray(T.state.generation_clips) ? T.state.generation_clips : [])
            .filter(clip => clip && Number(clip.src_out) > Number(clip.src_in || 0));
        if (Number(recipe.count) === count && Number(recipe.frames) === frames && existing.length === count) return false;
        const defaultLength = Math.max(1, Math.round(frames)) / FPS;
        const clips = [];
        let cursor = 0;
        for (let index = 0; index < count; index++) {
            const previous = existing[index];
            const length = previous
                ? Math.max(1 / FPS, Math.round((Number(previous.src_out) - Number(previous.src_in || 0)) * FPS) / FPS)
                : Math.round(defaultLength * FPS) / FPS;
            const start = previous ? Math.max(cursor, Number(previous.tl_start) || 0) : cursor;
            clips.push({id: previous?.id || `prompt-clip-${index + 1}`, tl_start: start, src_in: 0, src_out: length});
            cursor = start + length;
        }
        T.state.generation_clips = clips;
        T.state.duration = cursor;
        T.state.split_points = [];
        T.state.segmentation = "clips";
        T.state.segment_recipe = {...recipe, frames: Math.round(frames), count, unit: T.unit, custom: false};
        const countInput = $("[data-count]");
        if (countInput) countInput.value = String(count);
        return true;
    }
    node._adH3SyncPromptSegments = (options = {}) => {
        if (node._adH3MaterialsReady === false) return false;
        if (!applyPromptOnlySegments(options)) return false;
        render();
        try { updateMaterialOutputs(node, T.state); } catch {}
        node.setDirtyCanvas?.(true, true);
        return true;
    };
    function afterEdit(outputs = true, restoredBindings = false, preserveTracks = false) {
        applyImageOnlySegments();
        applyPromptOnlySegments();
        if (!hasAvClips() && !Array.isArray(T.state.generation_clips) && T.state.duration>0) generationClips(T.state);
        bumpSnapCache();
        pruneSources();
        // 空槽有实际轨道身份；组移后再裁剪/删除也不能把后轨偷偷挪上来。
        const hasTrackGap=[T.state.video_tracks,T.state.audio_tracks].some(lanes=>lanes.some((lane,i)=>!lane.length&&lanes.slice(i+1).some(next=>next.length)));
        if (!preserveTracks && !restoredBindings && !hasTrackGap) compactTracks();
        const available = new Set([...activeClips(T.state), ...(hasAvClips() ? [] : T.state.generation_clips || [])].map(clip=>clip.id));
        T.selected = new Set([...T.selected].filter(id => available.has(id)));
        if (outputs) try { updateMaterialOutputs(node, T.state); } catch (error) { hint(error.message, true); throw error; }
        // 历史快照已包含正确绑定，不能拿编辑后的段线再次迁移。
        if (restoredBindings) lastIntervals = snapshotIntervals();
        else remapImageSegments();
        render();
        scheduleLayout(40);
        node.setDirtyCanvas(true, true);
    }

    // ---------- 波形 ----------
    let audioCtx = null, previewDisposed = false;
    const audioCache = new PreviewCache(64 * 1024 * 1024);
    const audioPending = new Map();
    async function bufferFor(source) {
        const key = source.id, url = mediaURL(source);
        const cached = audioCache.get(key);
        if (cached?.url === url) return cached.buffer;
        const existing = audioPending.get(key);
        if (existing?.url === url) return existing.promise;
        existing?.controller.abort();
        const job = { url, controller: new AbortController() };
        job.promise = (async () => {
            const response = await fetch(url, {signal: job.controller.signal});
            if (!response.ok) throw new Error("素材读取失败");
            const data = await response.arrayBuffer();
            audioCtx ||= new (window.AudioContext || window.webkitAudioContext)();
            const buffer = await audioCtx.decodeAudioData(data);
            if (!previewDisposed && !job.controller.signal.aborted && T.state.sources.some(item => item.id === key)) {
                audioCache.set(key, {url, buffer}, buffer.length * buffer.numberOfChannels * 4, () => {});
                audioCache.sweep();
            }
            return buffer;
        })().finally(() => { if (audioPending.get(key) === job) audioPending.delete(key); });
        audioPending.set(key, job);
        return job.promise;
    }
    async function drawWave(canvasElement, source, clip, region = clipPaintRegion(clip)) {
        if (!canvasElement || !source) return;
        if (!region) return;
        const key = JSON.stringify([mediaURL(source), clip.src_in, clip.src_out, region]);
        if (canvasElement.dataset.paintKey === key) return;
        canvasElement.dataset.paintKey = key;
        try {
            const buffer = await bufferFor(source);
            if (previewDisposed || !canvasElement.isConnected || canvasElement.dataset.paintKey !== key) return;
            const width = region.width, height = 44;
            canvasElement.width = width; canvasElement.height = height;
            canvasElement.style.left = `${region.start}px`; canvasElement.style.right = "auto";
            canvasElement.style.width = `${width}px`;
            const context = canvasElement.getContext("2d");
            const channel = buffer.getChannelData(0);
            const rate = buffer.sampleRate;
            context.clearRect(0, 0, width, height);
            context.fillStyle = "#7fb2ff";
            for (let x = 0; x < width; x++) {
                const from = Math.floor((clip.src_in + (region.start + x) / region.fullWidth * (clip.src_out - clip.src_in)) * rate);
                const to = Math.floor((clip.src_in + (region.start + x + 1) / region.fullWidth * (clip.src_out - clip.src_in)) * rate);
                let peak = 0;
                for (let i = from; i < Math.min(to, channel.length); i += 16) peak = Math.max(peak, Math.abs(channel[i]));
                const bar = Math.max(1, peak * height);
                context.fillRect(x, (height - bar) / 2, 1, bar);
            }
        } catch { if (canvasElement.dataset.paintKey === key) delete canvasElement.dataset.paintKey; /* 波形只是装饰，失败不打断 */ }
    }

    // ---------- 视频缩略图（按源时间网格后台抽帧，渐进刷新所有片段） ----------
    const thumbJobs = new Map();
    const thumbCache = new PreviewCache(32 * 1024 * 1024);
    let thumbQueue = Promise.resolve();
    function disposeThumb(job) {
        job.cancelled = true; job.stopWait?.();
        if (job.video) { job.video.pause(); job.video.removeAttribute("src"); job.video.load(); }
        for (const bitmap of job.grid.values()) bitmap.close?.();
        job.grid.clear();
        if (thumbJobs.get(job.id) === job) thumbJobs.delete(job.id);
    }
    function waitThumb(job, event, seek) {
        return new Promise((resolve, reject) => {
            let timer;
            const finish = error => {
                clearTimeout(timer); job.video.removeEventListener(event, done);
                job.video.removeEventListener("error", failed); job.stopWait = null;
                error ? reject(error) : resolve();
            };
            const done = () => finish();
            const failed = () => finish(new Error("视频缩略图读取失败"));
            job.stopWait = () => finish(new Error("缩略图任务已取消"));
            job.video.addEventListener(event, done, {once:true});
            job.video.addEventListener("error", failed, {once:true});
            timer = setTimeout(failed, 4000);
            seek?.();
        });
    }
    async function thumbsFor(source) {
        if (!source || previewDisposed) return;
        const existing = thumbCache.get(source.id);
        if (existing?.url === mediaURL(source)) return existing;
        if (existing) thumbCache.delete(source.id);
        const job = { id: source.id, url: mediaURL(source), video: null, ready: false, grid: new Map(), cols: 0, rows: 0, thumbW: 0, thumbH: 0, duration: 0, bytes: 0 };
        thumbJobs.set(source.id, job);
        thumbCache.set(source.id, job, 0, disposeThumb);
        thumbQueue = thumbQueue.catch(() => {}).then(async () => {
        if (job.cancelled || previewDisposed) return;
        try {
            const video = document.createElement("video");
            video.muted = true; video.preload = "auto"; video.crossOrigin = "anonymous";
            job.video = video;
            await waitThumb(job, "loadedmetadata", () => { video.src = job.url; });
            job.duration = video.duration || source.duration || 1;
            job.thumbW = 96;
            job.thumbH = Math.max(1, Math.round(job.thumbW * video.videoHeight / Math.max(1, video.videoWidth)));
            const target = Math.min(60, Math.max(1, Math.ceil(job.duration / 2.5)));  // 每2.5秒一帧，上限60帧
            job.cols = target;
            for (let i = 0; i < job.cols; i++) {
                if (job.cancelled) break;
                const t = (i + 0.5) / job.cols * job.duration;
                await waitThumb(job, "seeked", () => { video.currentTime = Math.min(job.duration - 0.05, Math.max(0, t)); });
                try {
                    const bitmap = await createImageBitmap(video, {resizeWidth: job.thumbW, resizeHeight: job.thumbH, resizeQuality: "high"});
                    if (job.cancelled || previewDisposed) { bitmap.close?.(); break; }
                    job.grid.set(String(i), bitmap);
                    job.bytes += bitmap.width * bitmap.height * 4;
                    thumbCache.resize(job.id, job.bytes);
                    thumbCache.get(job.id); thumbCache.sweep(new Set([job.id]));
                    job.ready = job.grid.size > 1;
                    repaintThumbs(source.id);
                } catch {}
            }
            job.ready = true;
            repaintThumbs(source.id);
        } catch { if (thumbJobs.get(job.id) === job) thumbCache.delete(job.id); }
        finally { if (job.video) { job.video.pause(); job.video.removeAttribute("src"); job.video.load(); job.video = null; } }
        });
        return job;
    }
    function paintThumbs(canvasElement, clip, region = clipPaintRegion(clip)) {
        const job = thumbCache.get(clip.source);
        if (!canvasElement || !job || !job.ready) return;
        if (!region) return;
        const key = JSON.stringify([job.url, job.grid.size, clip.src_in, clip.src_out, region]);
        if (canvasElement.dataset.paintKey === key) return;
        canvasElement.dataset.paintKey = key;
        const width = region.width, height = 44;
        canvasElement.width = width; canvasElement.height = height;
        canvasElement.style.left = `${region.start}px`; canvasElement.style.right = "auto";
        canvasElement.style.width = `${width}px`;
        const context = canvasElement.getContext("2d");
        const span = clip.src_out - clip.src_in, srcDur = job.duration;
        for (let x = Math.floor(region.start / job.thumbW) * job.thumbW; x < region.start + width; x += job.thumbW) {
            const srcT = clip.src_in + (x + job.thumbW / 2) / region.fullWidth * span;
            const frame = Math.min(job.cols - 1, Math.max(0, Math.floor(srcT / srcDur * job.cols)));
            const bitmap = job.grid.get(String(frame));
            if (!bitmap) continue;
            context.drawImage(bitmap, x - region.start, 0, job.thumbW, Math.min(job.thumbH, height));
        }
    }
    function repaintThumbs(sourceId) {
        const lanes = [...canvas.querySelectorAll(".h3-lane")];
        const viewport = paintViewport();
        for (const element of canvas.querySelectorAll(".h3-clip.v canvas[data-thumb]")) {
            if (sourceId && element.dataset.thumb !== sourceId) continue;
            const clipElement = element.closest(".h3-clip");
            const laneIndex = lanes.indexOf(clipElement?.parentElement?.parentElement);
            const clip = (T.state.video_tracks[laneIndex] || []).find(item => item.id === clipElement?.dataset.id);
            if (clip) paintThumbs(element, clip, clipPaintRegion(clip, viewport));
        }
    }

    // ---------- 素材管理 ----------
    // 图片、音视频导入数量不限；参考输出仍遵循 H3 端口上限（图片 9、视频/音频各 3）。
    function addSource(source) {
        // 文件去重可返回已有ID；新增卡片/轨道实例仍须有自己的标识。
        source = {...source,id:source.id && !T.state.sources.some(item=>item.id===source.id) ? source.id : uid("s")};
        pushUndo();
        T.state.sources.push(source);
        if (source.kind !== "image" && !source.reference_only) {
            T.state.generation_clips = [];
            T.state.duration = null; T.state.split_points = [];
            // 音/视频各最多 MAX_LANES 条轨；轨满则接到最后一轨时间线末尾，不拒绝上传
            const key = source.kind === "video" ? "video_tracks" : "audio_tracks";
            if (!Array.isArray(T.state[key])) T.state[key] = [];
            let lanes = T.state[key];
            while (lanes.length > MAX_LANES) {
                // 防御：历史数据若超过 3 轨，多出来的并回最后一轨
                const extra = lanes.pop();
                if (extra?.length) lanes[MAX_LANES - 1].push(...extra);
            }
            let index = lanes.findIndex(track => !(track && track.length));
            let tlStart = 0;
            if (index < 0) {
                if (lanes.length < MAX_LANES) {
                    lanes.push([]);
                    index = lanes.length - 1;
                } else {
                    index = MAX_LANES - 1;
                    const lane = lanes[index] || [];
                    tlStart = lane.length ? Math.max(...lane.map(clipEnd)) : 0;
                }
            }
            lanes[index].push({
                id: uid("c"), source: source.id, tl_start: tlStart, src_in: 0,
                src_out: source.duration || 5,
            });
        }
        if (source.kind === "image") T.state.images.push({ id: uid("img"), source: source.id, segment: -1 });
        afterEdit();
        const portNote = T.state.sources.filter(s => s.kind === source.kind).length > PORT_LIMITS[source.kind]
            ? `（参考口仅输出前 ${PORT_LIMITS[source.kind]} 个）` : "";
        hint(`已导入 ${source.name}${source.duration ? `（${fmtClock(source.duration)}）` : ""}${portNote}`);
    }
    function removeSource(id) {
        pushUndo();
        thumbCache.delete(id);
        audioPending.get(id)?.controller.abort(); audioCache.delete(id);
        T.state.sources = T.state.sources.filter(source => source.id !== id);
        T.state.images = T.state.images.filter(image => image.source !== id);
        for (const key of ["video_tracks", "audio_tracks"])
            T.state[key] = T.state[key].map(lane => lane.filter(clip => clip.source !== id));
        afterEdit();
    }
    // 「清空」参考图片：只清图片素材与段别映射，视频/音频素材和轨道保持不动。
    function clearReferenceImages() {
        const images = (T.state.sources || []).filter(source => source.kind === "image");
        if (!images.length) { hint("参考图片已经是空的。"); return; }
        pushUndo();
        for (const source of images) thumbCache.delete(source.id);
        const removed = clearImageSources(T.state);
        afterEdit();
        hint(`已清空参考图片（${removed} 张），视频/音频轨未改动。`);
    }
    // 「清空全部素材」：移除素材准备节点里的全部图片/视频/音频（含时间线片段），不删除磁盘文件。
    function clearAllSources() {
        const sources = T.state.sources || [];
        const clipCount = [...(T.state.video_tracks || []), ...(T.state.audio_tracks || [])].flat().length;
        if (!sources.length && !clipCount) { hint("素材准备里没有素材。"); return; }
        if (!window.confirm(`清空素材准备里的全部素材（${sources.length} 个，含图片/视频/音频）？\n只从节点移除，不删除磁盘文件。`)) return;
        pushUndo();
        try { clearSourcePreview(root); } catch {}
        try { stopPlayback(); } catch {}
        for (const source of sources) {
            thumbCache.delete(source.id);
            audioPending.get(source.id)?.controller.abort();
            audioPending.delete(source.id);
            audioCache.delete(source.id);
            T.buffers.delete(source.id);
        }
        T.state.sources = [];
        T.state.images = [];
        T.state.video_tracks = [[], [], []];
        T.state.audio_tracks = [[], [], []];
        T.state.split_points = [];
        T.state.duration = null;
        delete T.state.generation_clips;
        T.state.muted_tracks = {};
        T.state.disabled_tracks = {};
        T.selected.clear();
        T.segment = -1;
        T.playhead = 0;
        afterEdit();
        hint(`已清空素材准备里的全部素材（${sources.length} 个）；磁盘文件保留。`);
    }
    // 「替换图片」：选一张本地图片，原地替换这张参考图（保留其参考范围/顺序，不新增图片）。
    function pickReplacementImage(oldSourceId) {
        const picker = document.createElement("input");
        picker.type = "file"; picker.accept = "image/*"; picker.style.display = "none";
        picker.addEventListener("change", () => {
            const file = picker.files && picker.files[0];
            picker.remove();
            if (file) void replaceImageWith(oldSourceId, file);
        }, {once:true});
        root.append(picker);
        picker.click();
    }
    async function replaceImageWith(oldSourceId, file) {
        const old = (T.state.sources || []).find(source => source.id === oldSourceId);
        if (!old) { hint("这张参考图已被移除，无法替换。", true); return; }
        try {
            if (!await storagePanel.canImport(Number(file.size) || 0)) { hint("导入将超过素材容量上限，请先清理缓存或整理素材。", true); return; }
        } catch (error) { hint(error.message, true); return; }
        try {
            const form = new FormData();
            form.append("file", file, file.name);
            const response = await api.fetchApi("/aiden_h3/upload", { method: "POST", body: form });
            const data = await response.json();
            if (data.error) { hint(data.error, true); return; }
            const source = data.source ? {...data.source,id:oldSourceId} : null;
            if (!source || source.kind !== "image") { hint("选择的不是图片文件，未替换。", true); return; }
            const index = T.state.sources.findIndex(item => item.id === oldSourceId);
            if (index < 0) { hint("这张参考图已被移除，无法替换。", true); return; }
            pushUndo();
            thumbCache.delete(oldSourceId);
            const entry = T.state.images.find(item => item.source === oldSourceId);
            T.state.sources.splice(index, 1, source);
            if (entry) entry.source = source.id;
            else T.state.images.push({ id: uid("img"), source: source.id, segment: -1 });
            afterEdit();
            const where = entry ? (entry.segment === -1 ? "全局参考" : entry.segment === -2 ? "禁止输出" : `第 ${entry.segment} 段参考`) : "全局参考";
            hint(`已替换参考图：${source.name}（${where}）`);
        } catch (error) { hint(`替换失败：${error.message}`, true); }
        finally { void storagePanel.refresh().catch(() => {}); }
    }
    async function uploadFiles(fileList, referenceOnly=false) {
        const files = Array.from(fileList || []);
        if (!files.length) return;
        // 批量预检只做一次（不再每个文件各扫一次占用），上传结束再刷新一次占用。
        try {
            const total = files.reduce((sum, file) => sum + (Number(file.size) || 0), 0);
            if (!await storagePanel.canImport(total)) { hint("导入将超过素材容量上限，请先清理缓存或整理素材。", true); return; }
        } catch (error) { hint(error.message, true); return; }
        let finished = 0, failed = false;
        setMaterialProgress(node, {busy: true, phase: "running", fraction: null, text: `导入中 0/${files.length}`});
        try {
            for (const file of files) {
                const form = new FormData();
                form.append("file", file, file.name);
                try {
                    const response = await api.fetchApi("/aiden_h3/upload", { method: "POST", body: form });
                    const data = await response.json();
                    if (data.error) { failed = true; hint(data.error, true); if (data.code === "storage_limit") break; continue; }
                    if (referenceOnly) data.source.reference_only = true;
                    addSource(data.source);
                    finished++;
                    setMaterialProgress(node, {busy: true, phase: "running", fraction: finished/files.length, text: `已导入 ${finished}/${files.length}`});
                } catch (error) { failed = true; hint(`导入失败：${error.message}`, true); }
            }
        } finally {
            setMaterialProgress(node, {busy: false, phase: failed ? "error" : "completed", fraction: finished/files.length,
                text: `${failed ? "部分导入失败" : "导入完成"} ${finished}/${files.length}`});
            void storagePanel.refresh().catch(() => {});
        }
    }
    async function loadLibrary(mode) {
        try {
            hint(mode === "generated" ? "正在加载生成结果目录…" : "正在加载 input 素材目录…");
            const response = await api.fetchApi("/aiden_h3/load_library_dir", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({mode}),
            });
            const data = await response.json();
            if (data.error) { hint(data.error, true); return; }
            const list = data.sources || [];
            if (!list.length) { hint("目录里没有可加载的素材", true); return; }
            for (const source of list) addSource(source);
            afterEdit();
            hint(`${mode === "generated" ? "生成结果" : "input 素材"}已加载 ${list.length} 个`);
        } catch (error) { hint(`加载失败：${error.message}`, true); }
    }

    // 「素材目录 / 生成目录」：打开系统文件选择框，选完直接载入（与“加载图片”一致）。
    async function pickLibraryFiles(mode) {
        const name = mode === "generated" ? "生成结果" : "素材库";
        setMaterialProgress(node, {busy: true, phase: "running", fraction: null, text: "选择并载入素材"});
        try {
            hint(`正在打开${name}选择框…`);
            const response = await api.fetchApi("/aiden_h3/library/pick", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }),
            });
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || "打开选择框失败");
            if (data.cancelled) { setMaterialProgress(node, {busy: false, phase: "paused", fraction: 0, text: "已取消载入"}); hint("已取消选择。"); return; }
            const added = data.sources || [];
            for (const source of added) addSource(source);
            afterEdit();
            void storagePanel.refresh().catch(() => {});
            hint(added.length ? `${name}已载入 ${added.length} 个` : "没有可载入的素材");
            setMaterialProgress(node, {busy: false, phase: "completed", fraction: added.length ? 1 : 0, text: `已载入 ${added.length} 个`});
        } catch (error) { setMaterialProgress(node, {busy: false, phase: "error", fraction: 0, text: "载入失败"}); hint(`载入失败：${error.message}`, true); }
    }

    // ---------- 素材库 / 生成结果 选择面板（勾选后载入，不再全量） ----------
    function ensureLibraryPanelStyle() {
        if (document.getElementById("ad-lib-style")) return;
        const style = document.createElement("style"); style.id = "ad-lib-style";
        style.textContent = `
.ad-lib-overlay{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;font-family:var(--ad-font,system-ui,sans-serif)}
.ad-lib-panel{width:min(780px,94vw);max-height:84vh;display:flex;flex-direction:column;background:#1f2937;color:#e5edf7;border:1px solid #475569;border-radius:10px;box-shadow:0 18px 48px rgba(0,0,0,.5);font-size:14px}
.ad-lib-head{display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid #334155}
.ad-lib-head input{flex:1;min-width:120px;background:#111827;color:#e5edf7;border:1px solid #475569;border-radius:5px;padding:3px 8px}
.ad-lib-list{overflow:auto;padding:6px 12px;display:flex;flex-direction:column;gap:2px;max-height:56vh}
.ad-lib-row{display:flex;gap:8px;align-items:center;padding:4px 2px;cursor:pointer;border-radius:4px}
.ad-lib-row:hover{background:#273449}
.ad-lib-kind{flex:0 0 40px;color:#93a4bd}
.ad-lib-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-lib-size{flex:0 0 90px;text-align:right;color:#93a4bd}
.ad-lib-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 12px;border-top:1px solid #334155}
.ad-lib-foot .primary{background:#3b82f6;color:#fff;border:1px solid #2563eb;border-radius:6px;padding:4px 14px;cursor:pointer}
.ad-lib-foot .primary[disabled]{opacity:.5;cursor:not-allowed}`;
        document.head.append(style);
    }
    let libraryPanel = null;
    function closeLibraryPanel() { if (libraryPanel) { libraryPanel.remove(); libraryPanel = null; } }
    async function openLibraryPanel(mode) {
        ensureLibraryPanelStyle(); closeLibraryPanel();
        const overlay = document.createElement("div"); overlay.className = "ad-lib-overlay";
        overlay.innerHTML = `<div class="ad-lib-panel" role="dialog" aria-modal="true" aria-label="素材选择">
            <div class="ad-lib-head"><b>${mode === "generated" ? "生成结果" : "素材库"}</b>
              <input data-lib-search placeholder="搜索文件名" />
              <button type="button" data-lib-all>全选</button>
              <button type="button" data-lib-open>打开文件夹</button>
              <button type="button" data-lib-close>关闭</button></div>
            <div class="ad-lib-list" data-lib-list>正在读取…</div>
            <div class="ad-lib-foot"><span data-lib-info></span><button type="button" class="primary" data-lib-load disabled>载入选中</button></div>
          </div>`;
        document.body.append(overlay); libraryPanel = overlay;
        const list = overlay.querySelector("[data-lib-list]");
        const loadBtn = overlay.querySelector("[data-lib-load]");
        const info = overlay.querySelector("[data-lib-info]");
        const search = overlay.querySelector("[data-lib-search]");
        const kindLabel = { image: "图片", audio: "音频", video: "视频" };
        const fmtSize = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB`
            : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
        let items = [];
        const selected = new Set();
        const shownItems = () => {
            const q = String(search.value || "").trim().toLowerCase();
            return q ? items.filter(item => String(item.name).toLowerCase().includes(q)) : items.slice();
        };
        const refreshInfo = () => {
            loadBtn.disabled = !selected.size;
            info.textContent = `已选 ${selected.size} / 共 ${items.length} 个`;
        };
        const render = () => {
            const shown = shownItems(); list.innerHTML = "";
            for (const item of shown) {
                const row = document.createElement("label"); row.className = "ad-lib-row";
                const box = document.createElement("input"); box.type = "checkbox"; box.checked = selected.has(item.file);
                box.onchange = () => { box.checked ? selected.add(item.file) : selected.delete(item.file); refreshInfo(); };
                const kind = document.createElement("span"); kind.className = "ad-lib-kind"; kind.textContent = kindLabel[item.kind] || item.kind;
                const name = document.createElement("span"); name.className = "ad-lib-name"; name.textContent = item.name; name.title = item.name;
                const size = document.createElement("span"); size.className = "ad-lib-size"; size.textContent = fmtSize(Number(item.size) || 0);
                row.append(box, kind, name, size); list.append(row);
            }
            if (!shown.length) list.textContent = "没有匹配的素材";
            refreshInfo();
        };
        const onKey = event => { if (event.key === "Escape") { document.removeEventListener("keydown", onKey); closeLibraryPanel(); } };
        document.addEventListener("keydown", onKey);
        overlay.addEventListener("click", async event => {
            if (event.target === overlay) { document.removeEventListener("keydown", onKey); closeLibraryPanel(); return; }
            const button = event.target.closest("button"); if (!button) return;
            if (button.hasAttribute("data-lib-close")) { document.removeEventListener("keydown", onKey); closeLibraryPanel(); return; }
            if (button.hasAttribute("data-lib-open")) {
                try { await api.fetchApi("/aiden_h3/open_folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) }); } catch {}
                return;
            }
            if (button.hasAttribute("data-lib-all")) {
                const shown = shownItems(); const all = shown.every(item => selected.has(item.file));
                for (const item of shown) all ? selected.delete(item.file) : selected.add(item.file);
                render(); return;
            }
            if (button.hasAttribute("data-lib-load")) {
                const chosen = items.filter(item => selected.has(item.file));
                if (!chosen.length) return;
                button.disabled = true; info.textContent = "正在载入…";
                try {
                    const response = await api.fetchApi("/aiden_h3/library/load", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ mode, items: chosen }),
                    });
                    const data = await response.json();
                    if (!response.ok || data.error) throw new Error(data.error || "载入失败");
                    const added = data.sources || [];
                    for (const source of added) addSource(source);
                    afterEdit();
                    void storagePanel.refresh().catch(() => {});
                    hint(`${mode === "generated" ? "生成结果" : "素材库"}已载入 ${added.length} 个`);
                    document.removeEventListener("keydown", onKey); closeLibraryPanel();
                } catch (error) { info.textContent = error.message; button.disabled = false; }
            }
        });
        search.oninput = render;
        try {
            const response = await api.fetchApi("/aiden_h3/library/list", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode }),
            });
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || "读取目录失败");
            items = data.items || []; render();
        } catch (error) { list.textContent = error.message; }
    }
    // 「素材路径」= 批处理素材清单：回车/失焦后自动读取路径内文件数量来更新右上角「分段清单（x段）」，
    // 同时绝不把这些文件导入素材准备节点（不新增素材、不新增端口、不改时间线）。
    function applyBatchPath() {
        const input = $("[data-path-input]");
        const raw = String(input?.value || "").trim().replace(/^"(.*)"$/, "$1");
        if (raw) T.state.path_source = raw;
        else delete T.state.path_source;
        try { afterEdit(); } catch {}
        if (!raw) {
            T.batchCount = 0;
            node._adH3BatchCount = 0;
            try { updateMaterialOutputs(node, T.state); } catch {}
            try { syncButtons(); } catch {}
        }
        return raw;
    }
    let batchScanTicket = 0;
    function showBatchCount(count, summary = "") {
        T.batchCount = count;
        node._adH3BatchCount = count;
        node._adH3BatchSummary = summary;
        try { updateMaterialOutputs(node, T.state); } catch {}
        try { syncButtons(); } catch {}
        const badge = root.querySelector("[data-glass-count]");
        if (badge) badge.textContent = String(T.state.path_source ? count : intervals().length);
    }
    async function scanBatchPath(raw, { announce = false } = {}) {
        const path = String(raw || "").trim().replace(/^"(.*)"$/, "$1");
        const ticket = ++batchScanTicket;
        showBatchCount(0);
        if (!path) return 0;
        try {
            const response = await api.fetchApi("/aiden_h3/scan_path", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
            });
            const data = await response.json();
            if (ticket !== batchScanTicket) return 0;
            if (!response.ok || data.error) throw new Error(data.error || `扫描失败（${response.status}）`);
            const list = (Array.isArray(data.sources) ? data.sources : (data.source ? [data.source] : []))
                .filter(source => ["image", "audio", "video"].includes(source?.kind));
            const kinds = {image: 0, audio: 0, video: 0};
            for (const source of list) kinds[source.kind] += 1;
            const summary = list.length ? `（图片 ${kinds.image}、音频 ${kinds.audio}、视频 ${kinds.video}）` : "";
            showBatchCount(list.length, summary);
            if (announce || !list.length) hint(list.length
                ? `分段清单已自动读取：${list.length} 段${summary}（按该路径内 ${list.length} 个文件计算；文件不导入素材准备节点）。`
                : "该路径内没有可用的图片/音频/视频文件。", !list.length);
            return list.length;
        } catch (error) {
            if (ticket !== batchScanTicket) return 0;
            showBatchCount(0);
            hint(`读取素材路径失败：${error.message}`, true);
            return 0;
        } finally {
            if (ticket === batchScanTicket) void storagePanel.refresh().catch(() => {});
        }
    }

    {
        const label = $("[data-path-load]");
        const input = $("[data-path-input]");
        root.querySelectorAll("[data-library]").forEach(button => button.addEventListener("click", () => loadLibrary(button.dataset.library)));
        if (label) label.addEventListener("click", () => input?.focus?.());
        if (input) {
            let pathInputTimer = 0;
            input.addEventListener("input", () => {
                const raw = String(input.value || "").trim().replace(/^"(.*)"$/, "$1");
                if (raw) T.state.path_source = raw; else delete T.state.path_source;
                clearTimeout(pathInputTimer);
                ++batchScanTicket; showBatchCount(0);
                pathInputTimer = setTimeout(() => { applyBatchPath(); void scanBatchPath(input.value, { announce: true }); }, 350);
            });
            input.addEventListener("change", () => { clearTimeout(pathInputTimer); applyBatchPath(); void scanBatchPath(input.value, { announce: true }); });
            input.addEventListener("blur", () => { clearTimeout(pathInputTimer); applyBatchPath(); void scanBatchPath(input.value, { announce: true }); });
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") { event.preventDefault(); clearTimeout(pathInputTimer); applyBatchPath(); void scanBatchPath(input.value, { announce: true }); }
            });
            const saved = T.state.path_source;
            if (saved) { input.value = saved; setTimeout(() => { void scanBatchPath(saved); }, 0); }
        }
    }

    async function separateSelected() {
        const clips = activeClips(T.state,"video_tracks",T.selected);
        if (!clips.length) { requireSelectedClips();return; }
        if (T.separating) return;
        const snapshot=JSON.stringify(T.state),planned=(T.state.audio_tracks||[]).map(lane=>[...lane]),placements=[];
        for(const clip of clips){
            const index=planned.findIndex((lane,i)=>!trackDisabled(T.state,"audio_tracks",i)&&lane.every(item=>clipEnd(item)<=clip.tl_start+.5/FPS||item.tl_start>=clipEnd(clip)-.5/FPS));
            if(index<0){hint("可见音频轨没有足够空位，请先腾出音频轨道。",true);return;}
            planned[index].push(clip);placements.push(index);
        }
        T.separating=true;
        try{
            const extracted=new Map();
            for(const clip of clips)if(!extracted.has(clip.source)){
                const source=sourcesOf()[clip.source];
                const response=await api.fetchApi("/aiden_h3/separate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({origin:source.origin,file:source.file,name:source.name})});
                const data=await response.json();if(!response.ok||data.error)throw Error(data.error||"分离失败");extracted.set(clip.source,{...data.source,id:uid("s")});
            }
            if(snapshot!==JSON.stringify(T.state)||clips.some(clip=>!selectedEditableClips().some(item=>item.id===clip.id))){hint("所选片段已变化，分离结果未添加到时间线。",true);return;}
            pushUndo();T.state.sources.push(...extracted.values());
            clips.forEach((clip,i)=>{
                T.state.audio_tracks[placements[i]].push({id:uid("c"),source:extracted.get(clip.source).id,tl_start:clip.tl_start,src_in:clip.src_in,src_out:clip.src_out});
                clip.audio_muted=true;
            });
            for(const lane of T.state.audio_tracks)lane.sort((a,b)=>a.tl_start-b.tl_start);
            afterEdit();hint("已分离所选片段的声音并对齐原时间位置；只静音这些视频片段。");
        }catch(error){hint(`分离失败：${error.message}`,true);}finally{T.separating=false;}
    }

    // ---------- 常驻视频预览 ----------
    let previewSeekRevision = 0;
    async function updatePreviewAt(t) {
        const revision = ++previewSeekRevision;
        if (previewDisposed || T.playing || root._adSourcePreviewId) return;
        const sources = sourcesOf();
        // 白线可以停在终点；预览取末帧，避免端点被当成视频范围之外而黑屏。
        const sampleTime = t >= duration() ? Math.max(0, duration() - 1/FPS) : t;
        const clip = topVideoAt(T.state, sampleTime);
        if (!clip) {
            if (root._adSourcePreviewId) return;
            showPlayerPlaceholder(T.state.sources.some(source => source.kind === "video")
                ? "拖动播放头对准视频片段显示画面" : "一帧光影，一段声音，皆可成为开端。");
            playerVideo.pause();
            return;
        }
        const source = sources[clip.source];
        clearSourcePreview(root);
        if (!source) { if (playerPh) playerPh.style.display = "flex"; return; }
        if (playerPh) playerPh.style.display = "none";
        playerTitle.textContent = source.name || "视频预览";
        const targetSpan=Number.isFinite(clip.retained_src_in)?clip.retained_src_out-clip.retained_src_in:source.duration;
        playerMeta.textContent = `${targetSpan ? `目标时长 ${fmtClock(targetSpan)} · ` : ""}时间线 24 fps`;
        playerVideo.muted = videoMuted(clip);
        if (playerVideo.dataset.src !== mediaURL(source)) {
            playerVideo.src = mediaURL(source);
            playerVideo.dataset.src = mediaURL(source);
        }
        const expectedSource = mediaURL(source);
        const seek = () => {
            if (previewDisposed || revision !== previewSeekRevision || playerVideo.dataset.src !== expectedSource || T.playing) return;
            playerVideo.currentTime = Math.max(0, Math.min(playerVideo.duration || 1, clip.src_in + (sampleTime - clip.tl_start)));
        };
        if (playerVideo.readyState >= 1) seek();
        else playerVideo.addEventListener("loadedmetadata", seek, { once: true });
    }

    // ---------- 无音视频素材时：生成段编辑（与音视频裁切/切断/删除对齐） ----------
    const selectedEditableClips = () => activeClips(T.state, null, T.selected);
    const requireSelectedClips = () => {
        const clips = selectedEditableClips();
        if (!clips.length) hint("请先选中可见轨道上的音视频片段（Ctrl 可多选）；隐藏片段不参与操作。", true);
        return clips;
    };
    const hasAvClips = () => [...T.state.video_tracks, ...T.state.audio_tracks].some(lane => lane.length);
    const activeGenIndex = () => {
        const iv = intervals();
        if (!iv.length) return -1;
        if (T.segment >= 0 && T.segment < iv.length) return T.segment;
        return segmentAt(T.playhead);
    };
    const writeGenCuts = (cuts, durationValue = null) => {
        T.state.split_points = [...new Set(cuts.map(snapFrame))]
            .filter(t => t > 0.5 * FRAME && t < (durationValue ?? duration()) - 0.5 * FRAME)
            .sort((a, b) => a - b);
        if (durationValue != null) T.state.duration = snapFrame(durationValue);
        T.state.segmentation = "clips";
        if (T.state.segment_recipe) T.state.segment_recipe.custom = true;
    };
    function editGeneration(action) {
        if (hasAvClips()) return false;
        const clips = generationClips(T.state), t = snapFrame(T.playhead);
        const selected = clips.filter(clip => T.selected.has(clip.id));
        const targets = selected.length ? selected : clips.filter(clip => t >= clip.tl_start && t < clipEnd(clip));
        if (action === "delete") {
            if (!selected.length) { hint("请先选择生成片段（Ctrl 可多选，Ctrl+A 全选）。", true); return true; }
            pushUndo(); deleteGeneration(T.state, new Set(selected.map(clip=>clip.id)));
            T.selected.clear(); T.segment = -1; T.playhead = Math.min(T.playhead,duration());
            afterEdit(); hint(T.state.generation_clips.length ? "已删除所选片段，空隙保留。" : "生成段已全部删除；可撤销或重新应用分段。"); return true;
        }
        const cut = targets.filter(clip=>t>clip.tl_start+FRAME/2 && t<clipEnd(clip)-FRAME/2);
        if (!cut.length) { hint("请把播放头放到要编辑的片段内部。",true); return true; }
        pushUndo();
        const ids=new Set(cut.map(clip=>clip.id)), nextSelected=new Set();
        const next=clips.flatMap(clip=>{
            if (!ids.has(clip.id)) return [clip];
            if (action==="split") {
                const right={id:uid("g"),tl_start:t,src_in:0,src_out:clipEnd(clip)-t};
                nextSelected.add(clip.id);nextSelected.add(right.id);
                return [{...clip,src_in:0,src_out:t-clip.tl_start},right];
            }
            nextSelected.add(clip.id);
            return action==="left" ? [{...clip,tl_start:t,src_in:0,src_out:clipEnd(clip)-t}]
                : [{...clip,src_in:0,src_out:t-clip.tl_start}];
        });
        commitGeneration(T.state,next);T.selected=nextSelected;T.segment=-1;afterEdit();
        hint(action==="split"?"已切断所选生成片段。":"已独立裁剪所选生成片段，其它段帧数保留。");
        return true;
    }

    // 生成片段与音视频一样独立移动/拖边；碰撞时只顺延后块，保留其长度。
    function startGenSegResize(index, edge, event) {
        const snapshot=generationClips(T.state).map(clip=>({...clip}));
        const clip=snapshot[index];if(!clip)return;
        const startX=event.clientX, startScroll=view.scrollLeft, scale=viewScale()||1, pxs=T.pxs;
        let moved=false, raf=0, last=event;
        beginInteraction();
        const apply=ev=>{
            if (!moved && Math.abs(ev.clientX-startX)<2) return;
            if (!moved) {pushUndo();moved=true;}
            const rect=view.getBoundingClientRect();
            if(ev.clientX>rect.right-24)view.scrollLeft+=16;
            const delta=(ev.clientX-startX)/scale/pxs+(view.scrollLeft-startScroll)/pxs;
            let start=clip.tl_start,end=clipEnd(clip);
            if(edge==="l")start=Math.max(0,Math.min(end-FRAME,snapTo(start+delta)));
            else if(edge==="r")end=Math.max(start+FRAME,snapTo(end+delta));
            else {start=Math.max(0,snapTo(start+delta));end=start+clip.src_out-clip.src_in;}
            T.state.generation_clips=snapshot.map(item=>({...item}));
            changeGeneration(T.state,clip.id,start,end);bumpSnapCache();
            const live=generationClips(T.state);
            for(const element of canvas.querySelectorAll(".h3-generation-clip")){
                const i=live.findIndex(item=>item.id===element.dataset.id);if(i<0)continue;
                const item=live[i];element.dataset.seg=String(i);
                element.style.left=`${timeToX(item.tl_start)}px`;
                element.style.width=`${Math.max(6,timeToX(item.src_out-item.src_in))}px`;
                element.querySelector(".label").textContent=`第${i+1}段 · ${Math.round((item.src_out-item.src_in)*FPS)}帧`;
            }
            canvas.style.width=`${Math.max(view.clientWidth-80,headOffset()+timeToX(duration())+160)}px`;
            hint(`第 ${index+1} 段 ${Math.round((end-start)*FPS)} 帧；后块仅顺延，帧数保留。`);
        };
        const move=ev=>{last=ev;if(!raf)raf=requestAnimationFrame(()=>{raf=0;apply(last);});};
        const up=()=>{
            window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",up);
            if(raf){cancelAnimationFrame(raf);raf=0;apply(last);}endInteraction();
            if(moved)afterEdit();
        };
        window.addEventListener("pointermove",move);window.addEventListener("pointerup",up);
    }

    // ---------- 播放试听 ----------
    let playbackRevision = 0, playedSelectionKey = "";
    const laneMuted = (key, index) => !!T.state.muted_tracks?.[key]?.[index];
    const videoMuted = clip => !!root._adMuted || !!clip.audio_muted || T.state.video_tracks.some((lane, index) =>
        lane.includes(clip) && (T.state.muted_tracks?.video_tracks?.[index] ?? !!sourcesOf()[clip.source]?.muted));
    function syncTrackMuteButtons() {
        for (const lane of canvas.querySelectorAll(".h3-lane[data-kind]")) {
            const key = lane.dataset.kind, index = Number(lane.dataset.index), head = lane.querySelector(".h3-head");
            if (!head || head.querySelector("[data-track-mute]")) continue;
            const button = document.createElement("button");
            button.type = "button"; button.dataset.trackMute = "";
            const muted = laneMuted(key, index);
            button.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4Z"/>${muted ? '<path d="m16 9 6 6m0-6-6 6"/>' : '<path d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>'}</svg>`;
            button.title = `${muted ? "取消静音" : "静音"}本轨道`;
            button.setAttribute("aria-label", head.textContent + button.title);
            button.setAttribute("aria-pressed", String(muted));
            button.onpointerdown = event => { event.stopPropagation(); };
            button.onclick = event => {
                event.stopPropagation(); pushUndo();
                const resume = T.playing;
                if (resume) stopPlayback();
                T.state.muted_tracks ||= {};
                T.state.muted_tracks[key] ||= [];
                T.state.muted_tracks[key][index] = !muted;
                afterEdit();
                if (resume) void play({resume:true});
            };
            head.append(button);
        }
    }
    // 点击预览画面：播放 / 停止（与播放按钮同一套逻辑）
    function syncTrackVisibilityButtons() {
        for (const lane of canvas.querySelectorAll(".h3-lane[data-kind]")) {
            const key = lane.dataset.kind, index = Number(lane.dataset.index), head = lane.querySelector(".h3-head");
            const disabled = trackDisabled(T.state, key, index);
            lane.classList.toggle("h3-track-disabled", disabled);
            if (!head || head.querySelector("[data-track-visibility]")) continue;
            const button = document.createElement("button");
            button.type = "button"; button.dataset.trackVisibility = "";
            button.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>${disabled ? '<path d="m3 3 18 18"/>' : ''}</svg>`;
            button.title = disabled ? "启用本轨道（恢复预览与分段输出）" : "禁用本轨道（隐藏预览且不参与分段输出）";
            button.setAttribute("aria-label", head.textContent + button.title);
            button.setAttribute("aria-pressed", String(disabled));
            button.onpointerdown = event => { event.stopPropagation(); };
            button.onclick = event => {
                event.stopPropagation(); pushUndo();
                const resume = T.playing;
                if (resume) stopPlayback();
                clearSourcePreview(root);
                T.state.disabled_tracks ||= {};
                T.state.disabled_tracks[key] ||= [];
                T.state.disabled_tracks[key][index] = !disabled;
                afterEdit();
                hint(disabled ? "已启用本轨道。" : "已禁用本轨道：不参与预览与后续分段输出；正在执行的任务保持启动时状态。");
                if (resume) void play({resume:true});
            };
            head.append(button);
        }
    }
    const applyMute = (muted) => {
        root._adMuted = muted;
        root.querySelectorAll("video,audio").forEach(element => { try { element.muted = muted; } catch (error) {} });
        playerVideo.muted = muted;
        for (const source of T.nodes) if (source._adGain) source._adGain.gain.value = muted ? 0 : 1;
        const clip = topVideoAt(T.state, T.playhead);
        if (clip) playerVideo.muted = videoMuted(clip);
    };
    applyMute(true);
    // 素材清单库所有媒体统一受静音开关控制：播放时若处于静音态，直接静音该媒体
    root.addEventListener("play", event => {
        if (root._adMuted && event.target && "muted" in event.target) event.target.muted = true;
    }, true);
    const muteButton = root.querySelector("[data-mute]");
    if (muteButton) {
        muteButton.textContent = "取消静音";
        muteButton.onclick = () => {
            applyMute(!root._adMuted);
            muteButton.textContent = root._adMuted ? "取消静音" : "静音";
        };
    }
    playerVideo?.addEventListener?.("click", () => {
        const control = $("[data-play]");
        if (control && String(control.textContent || "").includes("停止")) stopPlayback();
        else play();
    });
    playerVideo && (playerVideo.style.cursor = "pointer");
    function stopPlayback() {
        playbackRevision++;
        T.playing = false;
        for (const source of T.nodes) try { source.stop(); } catch {}
        T.nodes = [];
        playerVideo.pause();
        $("[data-play]").textContent = "▶ 播放";
        renderOverlays();
        void updatePreviewAt(T.playhead);
    }
    async function play({ resume = false } = {}) {
        if (T.playing) { stopPlayback(); return; }
        if (!duration()) { hint("时间线还没有时长：请放置素材或填写总时长。", true); return; }
        const selected = hasAvClips() ? selectedEditableClips()
            : generationClips(T.state).filter(clip=>T.selected.has(clip.id));
        const playback = resume ? {range:playbackRange(T.playhead,duration()),selectionKey:playedSelectionKey,fromSelection:false}
            : selectionPlaybackRange(T.playhead,duration(),selected,playedSelectionKey);
        const [from,to] = playback.range;
        if (!resume) playedSelectionKey = playback.selectionKey;
        if (!(to > from)) { hint("没有可播放的范围：请先放置素材或移动白线游标。", true); return; }
        const ticket = ++playbackRevision;
        T.playing = true;
        $("[data-play]").textContent = "⏸ 停止";
        T.playhead = from;
        renderOverlays();
        renderClock();
        const sources = sourcesOf();
        try { audioCtx ||= new (window.AudioContext || window.webkitAudioContext)(); await audioCtx.resume(); } catch {}
        if (ticket !== playbackRevision) return;
        for (const [laneIndex, lane] of T.state.audio_tracks.entries()) for (const clip of lane) {
            if (laneMuted("audio_tracks", laneIndex) || trackDisabled(T.state, "audio_tracks", laneIndex)) continue;
            const start = Math.max(from, clip.tl_start), end = Math.min(to, clipEnd(clip));
            if (end - start <= 0.5 * FRAME) continue;
            try {
                const buffer = await bufferFor(sources[clip.source]);
                if (ticket !== playbackRevision) return;
                const node = audioCtx.createBufferSource();
                node.buffer = buffer;
                const gain = audioCtx.createGain();
                gain.gain.value = root._adMuted ? 0 : 1;
                node.connect(gain); gain.connect(audioCtx.destination); node._adGain = gain;
                const offsetInSource = clip.src_in + (start - clip.tl_start);
                node.start(audioCtx.currentTime + 0.03 + start - from, offsetInSource, end - start);
                T.nodes.push(node);
            } catch {}
        }
        const stepVideo = (t, forceSeek = false) => {
            const clip = topVideoAt(T.state, t);
            if (!clip) {
                if (!playerVideo.paused) playerVideo.pause();
                if (playerPh) playerPh.style.display = "flex";
                return;
            }
            if (playerPh) playerPh.style.display = "none";
            const source = sources[clip.source];
            playerVideo.muted = videoMuted(clip);
            playerTitle.textContent = source.name || "视频预览";
            if (playerVideo.dataset.src !== mediaURL(source)) {
                playerVideo.src = mediaURL(source);
                playerVideo.dataset.src = mediaURL(source);
                playerVideo.currentTime = clip.src_in + (t - clip.tl_start);
            } else {
                const expect = clip.src_in + (t - clip.tl_start);
                if (forceSeek || Math.abs(playerVideo.currentTime - expect) > 0.35)
                    playerVideo.currentTime = Math.max(0, Math.min(expect, playerVideo.duration || expect));
            }
            if (playerVideo.paused) void playerVideo.play().catch(() => {});
        };
        stepVideo(from,true);
        const startedAt = performance.now() / 1000, base = from;
        const step = () => {
            if (!T.playing || ticket !== playbackRevision) return;
            const t = base + performance.now() / 1000 - startedAt;
            if (t >= to) { T.playhead = to; stopPlayback(); renderClock(); return; }
            T.playhead = t;
            const x = headOffset() + timeToX(t);
            const playhead = canvas.querySelector(".h3-playhead");
            if (playhead) playhead.style.left = `${x}px`;
            else renderOverlays();  // 偶发被重绘清掉时补回，并继续跟随时针
            const viewWidth = view.clientWidth;
            if (x > view.scrollLeft + viewWidth - 60 || x < view.scrollLeft) view.scrollLeft = x - viewWidth / 3;
            stepVideo(t);
            renderClock();
            requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
        hint(`从${playback.fromSelection ? "所选片段起点" : "白线"} ${fmtClock(from)} 播放，到时间线结尾停止。`);
    }

    // ---------- 指针交互 ----------
    const locateCanvas = event => {
        const rect = canvas.getBoundingClientRect();
        const scale = viewScale();
        return Math.max(0, xToTime((event.clientX - rect.left) / scale - headOffset()));
    };
    function dragPlayhead(event, keepSegment = false) {
        if (event.button !== 0) return;
        event.preventDefault();
        const resume = T.playing;
        if (resume) stopPlayback();
        beginInteraction();
        // 拖拽期间缓存布局，避免每次 mousemove 强制 reflow
        const scale0 = viewScale() || 1;
        const rect0 = canvas.getBoundingClientRect();
        const head0 = headOffset();
        const total0 = duration();
        let raf = 0, lastE = event;
        const apply = e => {
            const t = snapTo(Math.max(0, Math.min(total0, xToTime((e.clientX - rect0.left) / scale0 - head0))), { withPlayhead: false });
            T.playhead = t;
            const playhead = canvas.querySelector(":scope > .h3-playhead");
            if (playhead) playhead.style.left = `${head0 + timeToX(Math.max(0, Math.min(t, total0)))}px`;
            else renderOverlays();
            renderClock({ seekPreview: true });
        };
        const move = e => {
            lastE = e;
            if (raf) return;
            raf = requestAnimationFrame(() => { raf = 0; apply(lastE); });
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            window.removeEventListener("pointercancel", up);
            if (raf) { cancelAnimationFrame(raf); raf = 0; apply(lastE); }
            endInteraction();
            renderClock({ seekPreview: true });
            syncButtons();
            if (resume) void play({resume:true});
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        window.addEventListener("pointercancel", up);
        apply(event);
    }
    canvas.addEventListener("pointerdown", event => {
        if (event.target.closest("[data-track-mute],[data-track-visibility]")) return;
        const clipElement = event.target.closest(".h3-clip");
        if (event.target.closest(".h3-playhead") || event.target.closest(".h3-ruler")) { dragPlayhead(event); return; }
        if (clipElement) { dragClip(event, clipElement); return; }
        if (event.target.closest(".h3-generation-clip")) return;
        // 空白轨道：剪映式框选（按住拖动选中范围内素材）；单击仍定位播放头
        if (event.target.closest(".h3-body") || event.target.classList.contains("h3-lane")
            || event.target === canvas || event.target.classList.contains("h3-canvas")) {
            startMarquee(event);
            return;
        }
        if (event.target.closest(".h3-head")) { dragPlayhead(event); return; }
        dragPlayhead(event);
    });
    function startMarquee(event) {
        if (event.button !== 0) return;
        event.preventDefault();
        const resume = T.playing;
        if (resume) stopPlayback();
        beginInteraction();
        const startX = event.clientX, startY = event.clientY;
        const additive = !!event.ctrlKey;
        const kept = additive ? new Set(T.selected) : new Set();
        let box = null, moved = false;
        const paintSelection = () => {
            for (const el of canvas.querySelectorAll(".h3-clip,.h3-generation-clip"))
                el.classList.toggle("selected", T.selected.has(el.dataset.id));
            syncButtons();
        };
        const move = e => {
            const dx = e.clientX - startX, dy = e.clientY - startY;
            if (!moved && Math.hypot(dx, dy) < 4) return;
            moved = true;
            if (!box) {
                box = document.createElement("div");
                box.className = "h3-marquee";
                canvas.append(box);
            }
            const crect = canvas.getBoundingClientRect();
            const scale = viewScale() || 1;
            const left = (Math.min(startX, e.clientX) - crect.left) / scale;
            const top = (Math.min(startY, e.clientY) - crect.top) / scale;
            const right = (Math.max(startX, e.clientX) - crect.left) / scale;
            const bottom = (Math.max(startY, e.clientY) - crect.top) / scale;
            box.style.left = `${left}px`;
            box.style.top = `${top}px`;
            box.style.width = `${Math.max(1, right - left)}px`;
            box.style.height = `${Math.max(1, bottom - top)}px`;
            const mx1 = Math.min(startX, e.clientX), my1 = Math.min(startY, e.clientY);
            const mx2 = Math.max(startX, e.clientX), my2 = Math.max(startY, e.clientY);
            const hit = new Set(kept);
            for (const el of canvas.querySelectorAll(".h3-clip,.h3-generation-clip")) {
                const r = el.getBoundingClientRect();
                if (r.right >= mx1 && r.left <= mx2 && r.bottom >= my1 && r.top <= my2)
                    hit.add(el.dataset.id);
            }
            T.selected = hit;
            T.segment = -1;
            paintSelection();
        };
        const up = e => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            box?.remove();
            endInteraction();
            if (!moved) {
                // 单击空白：清空选中、定位播放头；跑段改由点选素材框决定（不整页重建）
                if (!additive) T.selected.clear();
                T.segment = -1;
                T.playhead = snapTo(Math.max(0, Math.min(duration(), locateCanvas(event))), { withPlayhead: false });
                paintSelection();
                renderOverlays();
                renderClock({ seekPreview: true });
                syncButtons();
                if (resume) void play({resume:true});
                return;
            }
            syncSegmentFromSelection();
            paintSelection();
            if (T.selected.size) {
                const seg = T.segment >= 0 ? ` → 当前跑第 ${T.segment + 1} 段` : "";
                hint(`已框选 ${T.selected.size} 个片段${seg}；拖动其中一块可整体移动，也可切分、合并或删除。`);
            } else
                hint("框选范围内没有素材片段。");
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }
    function selectSegmentAt(t) {
        const index = segmentAt(t);
        T.segment = index;  // 点击即选中并保持；不做二次点击取消，否则双击等于没选
        renderOverlays();
        syncButtons();
        if (T.segment >= 0) { const iv = intervals(); if (iv[T.segment]) hint(`已选中第 ${T.segment + 1} 段（${fmtClock(iv[T.segment][1] - iv[T.segment][0])}）；点裁切/切断/删除，或拖左右边改时长。`); }
    }
    function neighborWindow(laneClips, clip, refStart, refEnd) {
        // 剪映式：以参考区间找左右邻居，拖到端点即停，禁止压盖。
        let prevEnd = 0, nextStart = Infinity;
        for (const item of laneClips) {
            if (item === clip) continue;
            const s = item.tl_start, e = clipEnd(item);
            if (e <= refStart + 1e-6) prevEnd = Math.max(prevEnd, e);
            else if (s >= refEnd - 1e-6) nextStart = Math.min(nextStart, s);
            else if ((s + e) / 2 <= (refStart + refEnd) / 2) prevEnd = Math.max(prevEnd, e);
            else nextStart = Math.min(nextStart, s);
        }
        return { prevEnd, nextStart };
    }
    function resolvePlacement(laneClips, clip, desired, length, refStart, refEnd) {
        const rs = refStart ?? clip.tl_start;
        const re = refEnd ?? (rs + length);
        const { prevEnd, nextStart } = neighborWindow(laneClips, clip, rs, re);
        const minStart = Math.max(0, prevEnd);
        const maxStart = Math.max(minStart, nextStart - length);
        return Math.max(minStart, Math.min(Math.max(0, desired), maxStart));
    }
    function startGroupMove(event, anchor, {generation=false,resume=false,locked=false}={}) {
        if(!locked)beginInteraction();
        const group=createGroupMove(T.state,T.selected,{generation,limit:MAX_LANES});
        const anchorMember=group.members.find(member=>member.id===anchor.id);
        if(!anchorMember){endInteraction();return;}
        const live=new Map((generation?generationClips(T.state):selectedEditableClips()).map(clip=>[clip.id,clip]));
        const moving=new Set(group.members.map(member=>member.id)),anchors=[0,T.playhead,...(T.state.split_points||[])];
        const clips=generation?generationClips(T.state):[...T.state.video_tracks,...T.state.audio_tracks].flat();
        for(const clip of clips)if(!moving.has(clip.id))anchors.push(clip.tl_start,clipEnd(clip));
        const startX=event.clientX,startY=event.clientY,startScroll=view.scrollLeft,scale=viewScale()||1,pxs=T.pxs;
        let previous={delta:0,deltaFrames:0,laneShift:0},changed=false,attempted=false,undoPushed=false,raf=0,last=event;
        T.segment=-1;paintSelection();syncButtons();
        const apply=ev=>{
            if(Math.hypot(ev.clientX-startX,ev.clientY-startY)<4&&!changed)return;
            attempted=true;
            const rect=view.getBoundingClientRect();
            if(ev.clientX>rect.right-24)view.scrollLeft+=16;else if(ev.clientX<rect.left+24)view.scrollLeft=Math.max(0,view.scrollLeft-16);
            if(ev.clientY>rect.bottom-36)view.scrollTop+=28;else if(ev.clientY<rect.top+36)view.scrollTop=Math.max(0,view.scrollTop-28);
            let delta=(ev.clientX-startX)/scale/pxs+(view.scrollLeft-startScroll)/pxs,laneShift=0;
            if(T.magnet)delta=group.snap(delta,anchors,pxs);
            if(!generation){
                const lanes=[...canvas.querySelectorAll(".h3-lane")].filter(el=>el.dataset.kind===anchorMember.key)
                    .sort((a,b)=>Number(a.dataset.index)-Number(b.dataset.index));
                const hit=lanes.find(el=>{const r=el.getBoundingClientRect();return ev.clientY>=r.top&&ev.clientY<r.bottom;});
                let index=hit?Number(hit.dataset.index):anchorMember.lane;
                if(!hit&&lanes.length){
                    if(ev.clientY<lanes[0].getBoundingClientRect().top)index=Number(lanes[0].dataset.index)-1;
                    else if(ev.clientY>=lanes.at(-1).getBoundingClientRect().bottom)index=Number(lanes.at(-1).dataset.index)+1;
                }
                laneShift=index-anchorMember.lane;
            }
            const plan=group.plan(delta,laneShift,previous);
            if(!plan.valid){hint(plan.reason,true);return;}
            if(plan.deltaFrames===previous.deltaFrames&&plan.laneShift===previous.laneShift){if(plan.blocked)hint("碰到其它片段或时间线起点，整组已停住。");return;}
            if(!undoPushed){pushUndo(true);undoPushed=true;}
            if(generation){for(const item of plan.items)live.get(item.id).tl_start=item.start;commitGeneration(T.state,generationClips(T.state));}
            else{
                for(const key of ["video_tracks","audio_tracks"])T.state[key]=T.state[key].map(lane=>lane.filter(clip=>!moving.has(clip.id)));
                for(const item of plan.items){while(T.state[item.key].length<=item.lane)T.state[item.key].push([]);const clip=live.get(item.id);clip.tl_start=item.start;T.state[item.key][item.lane].push(clip);}
                for(const key of ["video_tracks","audio_tracks"])for(const lane of T.state[key])lane.sort((a,b)=>a.tl_start-b.tl_start);
            }
            previous=plan;changed=true;bumpSnapCache();
            if(generation){const current=generationClips(T.state);for(const el of canvas.querySelectorAll(".h3-generation-clip")){
                const i=current.findIndex(clip=>clip.id===el.dataset.id);if(i<0)continue;const clip=current[i];el.dataset.seg=String(i);el.style.left=`${timeToX(clip.tl_start)}px`;el.style.width=`${Math.max(6,timeToX(clip.src_out-clip.src_in))}px`;
                el.querySelector(".label").textContent=`第${i+1}段 · ${Math.round((clip.src_out-clip.src_in)*FPS)}帧`;}
            }else patchClipsDuringDrag();
            canvas.style.width=`${Math.max(view.clientWidth-80,headOffset()+timeToX(duration())+160)}px`;
            paintSelection();hint(`已整体移动 ${group.members.length} 个片段${plan.blocked?"，已停靠边界":""}；长度和相对间距保持。`);
        };
        const move=ev=>{last=ev;if(!raf)raf=requestAnimationFrame(()=>{raf=0;apply(last);});};
        const up=()=>{
            window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",up);window.removeEventListener("pointercancel",up);
            if(raf){cancelAnimationFrame(raf);raf=0;apply(last);}endInteraction();
            // 组移只变位置，保留参考设置与轨道槽位；一次撤销恢复整个快照。
            if(changed)afterEdit(true,true,true);else{
                if(!attempted){T.selected=new Set([anchor.id]);if(generation)T.segment=generationClips(T.state).findIndex(clip=>clip.id===anchor.id);else syncSegmentFromSelection();}
                paintSelection();syncButtons();if(resume)void play({resume:true});
            }
        };
        window.addEventListener("pointermove",move);window.addEventListener("pointerup",up);window.addEventListener("pointercancel",up);
    }
    function dragClip(event, element) {
        if (event.button !== 0) return;
        
        event.preventDefault();
        const resume = T.playing;
        if (resume) stopPlayback();
        beginInteraction();
        const id = element.dataset.id;
        const find = () => [...T.state.video_tracks, ...T.state.audio_tracks].flat().find(clip => clip.id === id);
        const clip = find();
        if (!clip) { endInteraction(); return; }
        const laneBody = element.parentElement;
        const lanes = [...canvas.querySelectorAll(".h3-body")];
        const edge = event.target.classList.contains("edge") ? (event.target.classList.contains("l") ? "l" : "r") : null;
        if (event.ctrlKey || event.metaKey) {
            T.selected.has(id) ? T.selected.delete(id) : T.selected.add(id);
        } else if (edge || !T.selected.has(id)) {
            T.selected = new Set([id]);
        }
        syncSegmentFromSelection();
        if (event.ctrlKey || event.metaKey) {
            endInteraction();
            paintSelection();
            syncButtons();
            if (resume) void play({resume:true});
            return;
        }
        const selected=!edge&&T.selected.size>1?selectedEditableClips():[];
        if (!edge && selected.length>1 && selected.some(item=>item.id===id)) {
            T.selected=new Set(selected.map(item=>item.id));
            startGroupMove(event,clip,{resume,locked:true});return;
        }
        const startX = event.clientX, startTL = clip.tl_start, startIn = clip.src_in, startOut = clip.src_out;
        const dragScale = viewScale();
        const startLen = startOut - startIn;
        const retained=Number.isFinite(clip.retained_src_in)&&Number.isFinite(clip.retained_src_out)
            ? [clip.retained_src_in,clip.retained_src_out] : retainedSourceBounds(T.state,clip.source)||[0,sourcesOf()[clip.source]?.duration??Infinity];
        const startEnd = startTL + startLen;
        const locate0 = item => {
            for (const key of ["video_tracks", "audio_tracks"]) {
                const li = T.state[key].findIndex(lane => lane.includes(item));
                if (li >= 0) return { key, li };
            }
            return null;
        };
        const home = locate0(clip);
        const homeLane = home ? T.state[home.key][home.li] : [];
        const frozen = neighborWindow(homeLane, clip, startTL, startEnd);
        paintSelection();  // 按下即高亮；不整页重建
        syncButtons();
        let moved = false, undoPushed = false, raf = 0, topReserved = false;
        const move = moveEvent => {
            if (!undoPushed) { pushUndo(); undoPushed = true; }
            moved = true;
            const delta = xToTime((moveEvent.clientX - startX) / dragScale);
            const sources = sourcesOf();
            const maxSrc = Math.min(sources[clip.source]?.duration ?? Infinity,retained[1]);
            if (!edge) {
                const length = startLen;
                let next = snapTo(startTL + delta);
                next = Math.max(0, next);
                // 拖近视图上下沿时自动滚，否则到不了「下面那一轨」落点区
                const vrect = view.getBoundingClientRect();
                const edgePx = 36;
                if (moveEvent.clientY > vrect.bottom - edgePx) view.scrollTop += 28;
                else if (moveEvent.clientY < vrect.top + edgePx) view.scrollTop = Math.max(0, view.scrollTop - 28);

                const locate = locate0;
                const here0 = locate(clip);
                const kindKey = here0?.key;
                let laneKey = null, laneIndex = -1;
                if (kindKey) {
                    const kindLanes = [...canvas.querySelectorAll(".h3-lane")]
                        .filter(el => el.dataset.kind === kindKey)
                        .sort((a, b) => Number(a.dataset.index) - Number(b.dataset.index));
                    // 指针落在哪条同类轨（含空轨音频3）就落到哪条
                    let hitSame = kindLanes.find(el => {
                        const r = el.getBoundingClientRect();
                        return moveEvent.clientY >= r.top && moveEvent.clientY < r.bottom;
                    });
                    const topSpace = blank.getBoundingClientRect();
                    if (moveEvent.clientY >= topSpace.top && moveEvent.clientY < topSpace.bottom) {
                        const emptyIndex = T.state[kindKey].findIndex(lane => !lane.length);
                        if (!topReserved) patchClipsDuringDrag();
                        if (!topReserved && prependEmptyLane(T.state[kindKey], MAX_LANES)) {
                            T.state.muted_tracks ||= {};
                            const flags = Array.from({length:MAX_LANES}, (_, i) => !!T.state.muted_tracks[kindKey]?.[i]);
                            flags.splice(emptyIndex, 1); flags.unshift(false);
                            T.state.muted_tracks[kindKey] = flags;
                            const disabled = Array.from({length:MAX_LANES}, (_, i) => trackDisabled(T.state, kindKey, i));
                            disabled.splice(emptyIndex, 1); disabled.unshift(false);
                            T.state.disabled_tracks ||= {};
                            T.state.disabled_tracks[kindKey] = disabled;
                            for (const element of [...kindLanes]) {
                                const oldIndex = Number(element.dataset.index);
                                if (oldIndex === emptyIndex) {
                                    element.remove(); kindLanes.splice(kindLanes.indexOf(element),1); continue;
                                }
                                element.dataset.index = String(oldIndex < emptyIndex || emptyIndex < 0 ? oldIndex+1 : oldIndex);
                                element.querySelector(".h3-head").textContent = `${kindKey === "video_tracks" ? "视频" : "音频"}${Number(element.dataset.index)+1}`;
                            }
                            const extra = document.createElement("div");
                            extra.className = "h3-lane"; extra.dataset.kind = kindKey;
                            extra.dataset.index = "0"; extra.dataset.empty = "1";
                            extra.innerHTML = `<div class="h3-head">${kindKey === "video_tracks" ? "视频" : "音频"}1</div><div class="h3-body"></div>`;
                            kindLanes[0].before(extra); kindLanes.unshift(extra); topReserved = true;
                        }
                        if (topReserved) hitSame = kindLanes.find(element => element.dataset.index === "0");
                    }
                    // 仍在最后一条有片段的轨下半截 / 其下方：强制落到下一条空轨
                    const occupied = kindLanes.filter(el => el.dataset.empty !== "1");
                    const lastOcc = occupied[occupied.length - 1] || kindLanes[kindLanes.length - 1];
                    if (lastOcc) {
                        const r = lastOcc.getBoundingClientRect();
                        // 落在最后有片轨的下 1/4 或整条下方 → 进下一空轨（音频3）
                        const gate = r.top + r.height * 0.75;
                        if (moveEvent.clientY >= gate) {
                            const nextIndex = Number(lastOcc.dataset.index) + 1;
                            if (nextIndex < MAX_LANES) {
                                let extra = kindLanes.find(el => Number(el.dataset.index) === nextIndex);
                                if (!extra) {
                                    extra = document.createElement("div");
                                    extra.className = "h3-lane";
                                    extra.dataset.kind = kindKey;
                                    extra.dataset.index = String(nextIndex);
                                    extra.dataset.empty = "1";
                                    extra.style.background = "rgba(34,211,238,.12)";
                                    extra.innerHTML = `<div class="h3-head">${kindKey === "video_tracks" ? "视频" : "音频"}${nextIndex + 1}</div><div class="h3-body"></div>`;
                                    if (kindKey === "video_tracks") {
                                        const firstAudio = canvas.querySelector(".h3-lane[data-kind=audio_tracks]");
                                        if (firstAudio) firstAudio.before(extra); else canvas.append(extra);
                                    } else canvas.append(extra);
                                    kindLanes.push(extra);
                                }
                                hitSame = extra;
                                try { extra.scrollIntoView({ block: "nearest" }); } catch {}
                            }
                        }
                    }
                    if (hitSame) {
                        laneKey = kindKey;
                        laneIndex = Number(hitSame.dataset.index);
                    }
                }
                if (laneIndex < 0 && here0) { laneKey = here0.key; laneIndex = here0.li; }
                if (laneKey) {
                    while (T.state[laneKey].length <= laneIndex) T.state[laneKey].push([]);
                    const sameHome = !topReserved && home && laneKey === home.key && laneIndex === home.li;
                    // 同轨：冻住按下时的左右邻居；换轨：按目标轨空隙夹紧，禁止覆盖
                    clip.tl_start = sameHome
                        ? Math.max(frozen.prevEnd, Math.min(next, Math.max(frozen.prevEnd, frozen.nextStart - length)))
                        : resolvePlacement(T.state[laneKey][laneIndex], clip, next, length, next, next + length);
                    if (T.state[laneKey][laneIndex].every(item => item !== clip)) {
                        const here = locate(clip);
                        if (here) T.state[here.key][here.li] = T.state[here.key][here.li].filter(item => item !== clip);
                        T.state[laneKey][laneIndex].push(clip);
                        T.state[laneKey][laneIndex].sort((a, b) => a.tl_start - b.tl_start);
                    }
                } else {
                    clip.tl_start = Math.max(frozen.prevEnd, Math.min(next, Math.max(frozen.prevEnd, frozen.nextStart - length)));
                }
            } else if (edge === "l") {
                // 左缘修剪：不能越过左邻右端；最短 1 帧；不能把 src_in 拖成负
                let next = snapTo(startTL + delta);
                const maxStart = startEnd - FRAME;
                next = Math.max(frozen.prevEnd, Math.min(next, maxStart));
                let shift = next - startTL;
                if (startIn + shift < retained[0]) { shift = retained[0]-startIn; next = startTL + shift; }
                if (next < frozen.prevEnd) { next = frozen.prevEnd; shift = next - startTL; }
                clip.tl_start = next;
                clip.src_in = startIn + shift;
            } else {
                // 右缘修剪：不能越过右邻左端；受片源时长限制
                const maxEndBySrc = startTL + Math.max(FRAME, maxSrc - startIn);
                const maxEnd = Math.min(frozen.nextStart, maxEndBySrc);
                let end = snapTo(startEnd + delta);
                end = Math.max(startTL + FRAME, Math.min(end, maxEnd));
                clip.src_out = startIn + (end - startTL);
            }
            bumpSnapCache();
            if (!raf) raf = requestAnimationFrame(() => { raf = 0; patchClipsDuringDrag(); });
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            if (raf) { cancelAnimationFrame(raf); raf = 0; patchClipsDuringDrag(); }
            endInteraction();
            if (moved) {
                if(edge&&(Math.abs(clip.src_in-startIn)>1e-7||Math.abs(clip.src_out-startOut)>1e-7)){clip.retained_src_in=clip.src_in;clip.retained_src_out=clip.src_out;}
                afterEdit();
            }
            else {
                // 单击片段只选中素材：白线游标保持原位，不跟随点击位置移动（改走标尺/白线才定位）。
                paintSelection(); renderOverlays(); renderClock(); syncButtons();
                if (resume) void play({resume:true});
            }
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }
    function splitSelectedClips(clips, points) {
        const cutsFor = clip => [...new Set((points instanceof Map ? points.get(clip.id) || [] : points).map(snapFrame))].sort((a,b)=>a-b);
        const ids = new Set(clips.map(clip => clip.id));
        const nextSelected = new Set();
        for (const key of ["video_tracks", "audio_tracks"]) {
            T.state[key] = T.state[key].map(lane => lane.flatMap(clip => {
                if (!ids.has(clip.id)) return [clip];
                const ends = [clip.tl_start,
                    ...cutsFor(clip).filter(t => t > clip.tl_start + 0.5 * FRAME && t < clipEnd(clip) - 0.5 * FRAME),
                    clipEnd(clip)];
                return ends.slice(1).map((end, index) => {
                    const piece = {
                        ...clip,
                        id: index ? uid("c") : clip.id,
                        tl_start: ends[index],
                        src_in: clip.src_in + ends[index] - clip.tl_start,
                        src_out: clip.src_in + end - clip.tl_start,
                    };
                    nextSelected.add(piece.id);
                    return piece;
                });
            }));
        }
        T.state.segmentation = "clips";
        T.state.split_points = [];
        T.selected = nextSelected;
    }
    function splitClipAt(clip, t) {
        if (!clip || t <= clip.tl_start + 0.5 * FRAME || t >= clipEnd(clip) - 0.5 * FRAME) { hint("播放头要落在片段内部才能切断。", true); return; }
        pushUndo();
        const right = { ...clip, id: uid("c"), tl_start: snapFrame(t), src_in: clip.src_in + (t - clip.tl_start) };
        clip.src_out = clip.src_in + (t - clip.tl_start);
        for (const key of ["video_tracks", "audio_tracks"]) T.state[key] = T.state[key].map(lane => {
            const index = lane.findIndex(item => item.id === clip.id);
            return index === -1 ? lane : [...lane.slice(0, index + 1), right, ...lane.slice(index + 1)];
        });
        T.state.segmentation = "clips";
        T.selected = new Set([clip.id, right.id]);  // 切开后两段都保持可选中的蓝色框
        afterEdit();
        hint("已在播放头处切断片段。");
    }
    function removeSelected() {
        const ids = new Set(requireSelectedClips().map(clip=>clip.id));
        if (!ids.size) return;
        pushUndo();
        for (const key of ["video_tracks", "audio_tracks"])
            T.state[key] = T.state[key].map(lane => lane.filter(clip => !ids.has(clip.id)));
        T.selected.clear();
        afterEdit();
        hint("已删除选中片段；素材文件保留。");
    }
    async function mergeSelected() {
        if (!hasAvClips()) {
            const clips=generationClips(T.state), selected=clips.filter(clip=>T.selected.has(clip.id));
            if(selected.length<2){hint("请先选择至少两个相邻生成片段。",true);return;}
            const indexes=selected.map(clip=>clips.indexOf(clip));
            if(indexes.at(-1)-indexes[0]+1!==selected.length){hint("请选择相邻生成片段。",true);return;}
            pushUndo();
            const merged={id:uid("g"),tl_start:selected[0].tl_start,src_in:0,src_out:selected.reduce((sum,clip)=>sum+clip.src_out-clip.src_in,0)};
            commitGeneration(T.state,[...clips.filter(clip=>!T.selected.has(clip.id)),merged]);
            T.selected=new Set([merged.id]);T.segment=-1;afterEdit();hint("已合并所选生成片段的保留帧数。");return;
        }
        if (T.merging || T.selected.size < 2) { hint("请先选中至少两个片段（Ctrl 多选）再合并。", true); return; }
        const found = [];
        for (const key of ["video_tracks", "audio_tracks"])
            T.state[key].forEach((lane, li) => lane.forEach((clip, index) => {
                if (T.selected.has(clip.id) && !trackDisabled(T.state,key,li)) found.push({key, li, clip, index});
            }));
        if (found.length < 2) { hint("请选择同一可见轨道的至少两个片段。",true); return; }
        const {key, li} = found[0];
        if (found.some(item => item.key !== key || item.li !== li)) { hint("请选择同一条轨道上的片段。", true); return; }
        const ordered = found.sort((a, b) => a.clip.tl_start - b.clip.tl_start);
        if (ordered.at(-1).index - ordered[0].index + 1 !== ordered.length) { hint("请选择连续相邻的片段，避免覆盖未选中的内容。", true); return; }
        const snapshot = JSON.stringify(T.state), selected = new Set(T.selected), sources = sourcesOf();
        T.merging = true; hint("正在合并实际保留的片段…");
        try {
            const clips = ordered.map(({clip}) => ({...sources[clip.source], src_in: clip.src_in, src_out: clip.src_out}));
            const response = await api.fetchApi("/aiden_h3/merge_clips", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({kind:key === "video_tracks" ? "video" : "audio", clips})});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "合并失败");
            if (JSON.stringify(T.state) !== snapshot) { hint("编辑内容已变化，合并结果已保留，请重新选择片段后合并。", true); return; }
            pushUndo();
            const source = result.source;
            source.name = `合并 · ${sources[ordered[0].clip.source].name}`;
            T.state.sources.push(source);
            const merged = {id:uid("c"), source:source.id, tl_start:ordered[0].clip.tl_start, src_in:0, src_out:source.duration};
            T.state[key][li] = [...T.state[key][li].filter(clip => !selected.has(clip.id)), merged].sort((a,b) => a.tl_start - b.tl_start);
            T.selected = new Set([merged.id]); afterEdit();
            hint(`已合并保留内容，共 ${source.duration.toFixed(3)} 秒；裁掉的内容未恢复。`);
        } catch (error) { hint(error.message, true); }
        finally { T.merging = false; }
    }

    // ---------- 工具栏 ----------
    root.addEventListener("click", async event => {
        const button = event.target.closest("button");
        if (!button || button.disabled) return;
        if(button.dataset.cursorStep){
            if(T.playing)stopPlayback();
            clearSourcePreview(root);
            T.playhead=Math.max(0,Math.min(Math.round(duration()*FPS),Math.round(T.playhead*FPS)+Number(button.dataset.cursorStep)))/FPS;
            renderOverlays();renderClock();syncButtons();return;
        }
        if (button.dataset.libraryPick) { void pickLibraryFiles(button.dataset.libraryPick); return; }
        if (button.hasAttribute("data-clear-images")) { clearReferenceImages(); return; }
        if (button.dataset.libraryPanel) { void openLibraryPanel(button.dataset.libraryPanel); return; }
        if (button.dataset.openFolder) {
            try { const response=await api.fetchApi("/aiden_h3/open_folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:button.dataset.openFolder})});const result=await response.json();if(!response.ok)throw new Error(result.error);hint("已打开目录。"); } catch(error){hint(error.message,true);} return;
        }
        if (button.dataset.load) {
            fileInput.dataset.reference="";
            fileInput.dataset.kind = button.dataset.load;
            fileInput.accept = { image: "image/*", video: "video/*", audio: "audio/*" }[button.dataset.load];
            fileInput.click();
            return;
        }
        if (button.hasAttribute("data-batch")) { fileInput.dataset.reference="";fileInput.dataset.kind = ""; fileInput.accept = "image/*,video/*,audio/*"; fileInput.click(); return; }
        if (button.hasAttribute("data-play")) { void play(); return; }
        if (button.hasAttribute("data-undo")) { doUndo(); return; }
        if (button.hasAttribute("data-redo")) { doRedo(); return; }
        if (button.hasAttribute("data-magnet")) { T.magnet = !T.magnet; button.classList.toggle("on", T.magnet); syncToggleA11y(); return; }
        if (button.hasAttribute("data-delete")) {
            if (!hasAvClips()) { editGeneration("delete"); return; }
            removeSelected(); return;
        }
        if (button.hasAttribute("data-remove-source")) {
            // 只移除所选使用实例；其它轨道/隐藏片段仍引用的同一素材不得连带删除。
            removeSelected();
            return;
        }
        if (button.hasAttribute("data-separate")) { void separateSelected(); return; }
        if (button.hasAttribute("data-merge")) { mergeSelected(); return; }
        if (button.hasAttribute("data-apply-split")) {
            const { frames, count, byCount } = readRecipeInputs();
            const hasClips = [...T.state.video_tracks, ...T.state.audio_tracks].some(lane => lane.length);
            if (count !== 0 && !byCount) { hint("分段数应为 1～1000 的整数；有音视频时也可填 0 表示按每段长度切开。", true); return; }
            if (!Number.isInteger(frames) || frames < 1) { hint("每段长度需要是正整数。", true); return; }
            const writeBackRecipe = (segFrames, segCount) => {
                T.unit = T.unit === "s" ? "s" : "f";
                $("[data-every]").value = T.unit === "s" ? Number((segFrames / FPS).toFixed(6)) : segFrames;
                $("[data-count]").value = segCount;
                T.state.segment_recipe = { frames: segFrames, count: segCount, unit: T.unit, custom: false };
            };
            const selectedClips = selectedEditableClips();
            if (hasClips && !selectedClips.length) { requireSelectedClips(); return; }
            // 有选中音视频：只拆选中片段
            if (selectedClips.length) {
                if (!byCount && count !== 0) { hint("分段数应为 1～1000，或填 0 按每段长度拆。", true); return; }
                const cutsByClip = new Map();
                for (const clip of selectedClips) {
                    const cuts = [];
                    cutsByClip.set(clip.id,cuts);
                    const from = clip.tl_start, to = clipEnd(clip), len = to - from;
                    const clipFrames = Math.floor(len * FPS + 1e-7);
                    if (!(len > FRAME) || clipFrames < 2) { hint("选中片段太短，无法继续分段。", true); return; }
                    if (byCount) {
                        if (count > clipFrames) { hint(`选中片段只有 ${clipFrames} 帧，不能拆成 ${count} 段。`, true); return; }
                        for (let i = 1; i < count; i++) cuts.push(snapFrame(from + len * i / count));
                    } else {
                        const step = frames / FPS;
                        for (let t = from + step; t < to - 0.5 * FRAME; t += step) cuts.push(snapFrame(t));
                    }
                }
                if (![...cutsByClip.values()].some(cuts=>cuts.length)) { hint(byCount ? "分段数为 1 时无需再拆。" : "按当前每段长度，选中片段内切不出更多点。", true); return; }
                pushUndo();
                splitSelectedClips(selectedClips, cutsByClip);
                T.segment = -1;
                const iv = intervals();
                const avg = iv.length
                    ? Math.max(1, Math.round(iv.reduce((s, [a, b]) => s + (b - a) * FPS, 0) / iv.length))
                    : frames;
                writeBackRecipe(byCount ? avg : frames, byCount ? count : Math.max(1, iv.length));
                afterEdit();
                hint(`已拆开选中片段；清单共 ${Math.max(0, intervals().length)} 段。`);
                return;
            }
            // 纯图片：段数改由「分段数」决定；保留已有各段长度，只在末尾追加或从末尾删除差值段（v0.4.23 用户新规则）。
            const imagePlan = imageOnlyClips(T.state, frames, byCount ? { count } : {});
            if (imagePlan) {
                if (!byCount) { hint("纯图片模式请填写 1～1000 的分段数；生成段数不再跟随图片数量。", true); return; }
                const before = (Array.isArray(T.state.generation_clips) ? T.state.generation_clips : [])
                    .filter(clip => clip && Number(clip.src_out) > Number(clip.src_in || 0)).length;
                pushUndo();
                T.state.generation_clips = imagePlan.clips;
                T.state.duration = imagePlan.duration;
                T.state.split_points = [];
                T.state.segmentation = "clips";
                T.state.segment_recipe = { ...(T.state.segment_recipe || {}), frames: imagePlan.defaultFrames, count: imagePlan.count, unit: T.unit, custom: false, manual_count: true };
                const countInput = $("[data-count]");
                if (countInput) countInput.value = String(imagePlan.count);
                afterEdit();
                const added = Math.max(0, imagePlan.count - before), removed = Math.max(0, before - imagePlan.count);
                hint(`纯图片：生成段 ${before} → ${imagePlan.count} 段`
                    + (added ? `，末尾新增 ${added} 段 × ${imagePlan.defaultFrames} 帧` : removed ? `，已删除末尾 ${removed} 段` : "，原有段长保留")
                    + "；之后增删图片不再改变段数。");
                return;
            }
            // 无音视频：两字段配合 → 总长 = 每段长度 × 分段数，均分
            if (!hasClips) {
                if (!byCount) { hint("纯生成模式请填写分段数（≥1）。总长 = 每段长度 × 分段数。", true); return; }
                const total = snapFrame(frames * count / FPS);
                if (!(total > 0)) { hint("请设置每段长度和分段数。", true); return; }
                const cuts = [];
                for (let i = 1; i < count; i++) cuts.push(snapFrame(total * i / count));
                pushUndo();
                T.state.duration = total;
                delete T.state.generation_clips;
                writeBackRecipe(frames, count);
                cutTimelineAt(T.state, cuts); T.selected.clear(); T.segment = -1;
                afterEdit();
                hint(`已建立 ${count} 段 × ${frames} 帧（共 ${(total).toFixed(3)} 秒）。`);
                return;
            }
            requireSelectedClips();
            return;
        }
        if(button.dataset.unit){
            const raw=Number($("[data-every]").value), frames=T.unit==="s"?Math.round(raw*FPS):raw;
            T.unit=button.dataset.unit;$("[data-every]").value=T.unit==="s"?Number((frames/FPS).toFixed(6)):frames;
            $("[data-every]").step=T.unit==="s"?"0.001":"17";
            root.querySelectorAll("[data-unit]").forEach(item=>item.classList.toggle("on",item===button));syncToggleA11y();return;
        }
        if (button.dataset.edit) {
            const action = button.dataset.edit;
            const t = T.playhead;
            // 无音视频：生成段走同一套裁切/切断
            if (!hasAvClips()) { editGeneration(action); return; }
            if (action === "split") {
                const clips = requireSelectedClips();
                const targets = clips.filter(clip => t > clip.tl_start + 0.5 * FRAME && t < clipEnd(clip) - 0.5 * FRAME);
                if (!targets.length) { hint("播放头没有落在任何可切断的片段上。", true); return; }
                pushUndo(); splitSelectedClips(targets,[t]); afterEdit();
                return;
            }
            const clips = requireSelectedClips();
            if (!clips.length) return;
            pushUndo();
            const changed=[];
            for (const clip of clips) {
                if (action === "left") {
                    if (t > clip.tl_start + 0.5 * FRAME && t < clipEnd(clip) - 0.5 * FRAME) {
                        clip.src_in += t - clip.tl_start;
                        clip.tl_start = snapFrame(t);
                        changed.push(clip);
                    }
                } else {
                    if (t > clip.tl_start + 0.5 * FRAME && t < clipEnd(clip) - 0.5 * FRAME) {clip.src_out = clip.src_in + (t - clip.tl_start);changed.push(clip);}
                }
            }
            for(const clip of changed){clip.retained_src_in=clip.src_in;clip.retained_src_out=clip.src_out;}
            afterEdit();
            hint(changed.length?"已即时裁剪；拖边不能找回已裁内容，可用撤销恢复。":"播放头未落在片段内部。");
            return;
        }
    });
    {
        const toggle = $("[data-frame-toggle]");
        const plan = $("[data-frame-plan]");
        if (toggle && plan) toggle.addEventListener("click", () => {
            plan.classList.toggle("collapsed");
            const open = !plan.classList.contains("collapsed");
            toggle.setAttribute("aria-expanded", String(open));
            toggle.textContent = open ? "帧数明细 ▾" : "帧数明细 ▸";
        });
    }
    {
        const input = $("[data-max-frames]");
        if (input) {
            input.addEventListener("change", () => applyMaxFrames(input.value));
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") { event.preventDefault(); input.blur(); return; }
                if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                    event.preventDefault();
                    const delta = event.key === "ArrowUp" ? 17 : -17;
                    const next = clampMaxFrames((Number(input.value) || 0) + delta);
                    input.value = String(next);
                    applyMaxFrames(next);
                }
            });
            syncMaxFramesControl(resolveMaxFrames(), { force: true });
        }
    }
    for (const sel of ["[data-every]", "[data-count]"]) {
        const input = $(sel);
        if (!input) continue;
        input.addEventListener("input", () => syncRecipePreview());
        input.addEventListener("change", () => syncRecipePreview());
    }
    syncRecipePreview();

    fileInput.onchange = () => {
        const kind = fileInput.dataset.kind;
        const files = [...fileInput.files].filter(file => !kind || file.type.startsWith(kind) ||
            (kind === "audio" && /\.(wav|mp3|flac|ogg|m4a|aac|opus|aiff?)$/i.test(file.name)) ||
            (kind === "video" && /\.(mp4|webm|mov|mkv|avi|m4v)$/i.test(file.name)) ||
            (kind === "image" && /\.(png|jpe?g|webp|bmp)$/i.test(file.name)));
        if (files.length) void uploadFiles(files,fileInput.dataset.reference==="1");
        fileInput.value = "";
    };
    root.addEventListener("pointerdown", event => {
        if (event.target?.closest?.("input, textarea, select")) return;  // 不抢输入框焦点，保证原生上下箭头可用
        activeEditorRoot = root;
        try { root.focus({ preventScroll: true }); } catch { try { root.focus(); } catch {} }
    }, {capture:true});
    // 点到时间线外（画布选节点等）：释放快捷键，Delete 还给 ComfyUI 删节点
    const onEditorPointerDown = event => {
        if (activeEditorRoot === root && !root.contains(event.target))
            activeEditorRoot = null;
    };
    document.addEventListener("pointerdown", onEditorPointerDown, true);
    const onEditorKeydown = event => {
        if (activeEditorRoot !== root) return;
        if (event.target?.closest?.("input,textarea,select,.h3-smartcut")) return;  // 输入与候选点按键各自处理，不能误删片段
        // 焦点不在本编辑器：不拦 Delete/空格，避免抢走 ComfyUI 删节点
        const inEditor = root.contains(event.target) || root.contains(document.activeElement) || document.activeElement === root;
        if (!inEditor) return;
        // 捕获阶段拦截：仅在编辑器内删片段/撤销，绝不误删整个节点
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==="a") {
            event.preventDefault();event.stopPropagation();
            T.selected=new Set((hasAvClips()?activeClips(T.state):generationClips(T.state)).map(clip=>clip.id));
            for(const element of canvas.querySelectorAll(".h3-clip")) element.classList.toggle("selected",T.selected.has(element.dataset.id));
            syncButtons();
        } else if ((event.key === "Delete" || event.key === "Backspace") && T.selected.size) {
            event.preventDefault(); event.stopPropagation();
            if (!hasAvClips()) editGeneration("delete");
            else removeSelected();
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && undoStack.length) {
            event.preventDefault(); event.stopPropagation();
            event.shiftKey ? doRedo() : doUndo();
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y" && redoStack.length) {
            event.preventDefault(); event.stopPropagation();
            doRedo();
        } else if (event.key === " " || event.code === "Space") {
            // 空格：播放 / 暂停（仅时间线聚焦时）
            event.preventDefault(); event.stopPropagation();
            void play();
        }
    };
    document.addEventListener("keydown", onEditorKeydown, {capture:true});
    root.tabIndex = 0;
    // 滚轮：选中了片段或生成段、且指针在时间线视口内 → 缩放时间线；否则交给工作流画布。
    // 无音频/视频时也可缩放（靠 T.segment / 生成段选中）。不恢复顶部缩放滑条。
    // 缩放节流：滚轮连续触发时每帧只重绘一次（原来每个事件都全量 render，素材多时卡）。
    let wheelZoomFrame = 0, wheelZoomPoint = null, rulerScrollFrame = 0;
    const onRulerScroll = () => {
        if (!rulerScrollFrame) rulerScrollFrame = requestAnimationFrame(() => {
            rulerScrollFrame = 0;
            ensureRulerCoverage();
            paintViewportMedia();
        });
    };
    view.addEventListener("scroll", onRulerScroll, {passive: true});
    const flushWheelZoom = () => {
        wheelZoomFrame = 0;
        const point = wheelZoomPoint;
        wheelZoomPoint = null;
        if (!point) return;
        T.pxs = point.pxs;
        renderTimelineZoom();
        // 用画布实际位置计算增量，包含视口边框与工作流画布缩放。
        view.scrollLeft += headOffset() + timeToX(point.anchor)
            - (point.clientX - canvas.getBoundingClientRect().left) / viewScale();
        ensureRulerCoverage();
        paintViewportMedia();
        applyLayoutHeight();  // 缩放后按内容收回节点高度，禁止下端空白弹回
    };
    root.addEventListener("wheel", event => {
        const inside = view.contains(event.target);
        const canZoomTimeline = inside && (T.selected.size > 0 || T.segment >= 0);
        if (!canZoomTimeline) {
            const target = app.canvas?.canvas;
            if (target) {
                event.preventDefault();
                event.stopImmediatePropagation();
                target.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, composed: true,
                    clientX: event.clientX, clientY: event.clientY, deltaX: event.deltaX, deltaY: event.deltaY,
                    deltaZ: event.deltaZ, deltaMode: event.deltaMode,
                    ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, altKey: event.altKey, metaKey: event.metaKey }));
            }
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        // 本帧始终用已绘制的状态取首个锚点，目标倍率累计到下一帧才生效。
        if (!wheelZoomPoint) wheelZoomPoint = {anchor: locateCanvas(event), clientX: event.clientX, pxs: T.pxs};
        wheelZoomPoint.pxs = Math.max(8, Math.min(240, wheelZoomPoint.pxs * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
        if (!wheelZoomFrame) wheelZoomFrame = requestAnimationFrame(flushWheelZoom);
    }, { capture: true, passive: false });

    const restoreEditor = value => {
            let rawSources = [];
            try {
                try { rawSources = JSON.parse(value || "{}").sources || []; } catch { rawSources = []; }
                T.state = loadStateValue(value);
                if(node._adWorkflowSession){T.state.session_id=node._adWorkflowSession;T.state.workflow_task_id=node._adWorkflowTask;}
            }
            catch (error) { hint(error.message, true); T.state = emptyTimeline(); }
            // 旧版本删除图片只清了参考记录：加载时清掉没有池条目的孤儿图片源
            T.state.sources = T.state.sources.filter(source =>
                source.kind !== "image" || T.state.images.some(image => image.source === source.id));
            pruneSources();  // 视频/音频：片段已全部删除的素材在加载时同样清理
            node._adH3MaterialsReady = true;
            if (Array.isArray(rawSources) && rawSources.some(isBlockedToneSource)) {
                hint("已自动移除内置嘟声测试音源；请加载真实音频/视频。");
                try { updateMaterialOutputs(node, T.state); } catch {}
            }
            T.selected.clear(); T.segment = -1;
            const iv=segmentIntervals(T.state), recipe=T.state.segment_recipe;
            T.unit=recipe?.unit==="s"?"s":"f";
            const frames=recipe?.frames||(iv.length?Math.max(1,Math.round((iv[0][1]-iv[0][0])*FPS)):DEFAULT_SEG_FRAMES);
            $("[data-every]").value=T.unit==="s"?Number((frames/FPS).toFixed(6)):frames;
            $("[data-every]").step=T.unit==="s"?"0.001":"17";
            root.querySelectorAll("[data-unit]").forEach(item=>item.classList.toggle("on",item.dataset.unit===T.unit));syncToggleA11y();
            $("[data-count]").value=recipe?.count||(iv.length||DEFAULT_SEG_COUNT);
            try { applyPromptOnlySegments(); } catch {}
            lastIntervals = snapshotIntervals();
            render();
            try { updateMaterialOutputs(node, T.state); } catch {}
            applyLayoutHeight();
    };
    // ========== Layout v4：宽度自由、高度按内容 ==========
    // 铁律：
    // 1) 绝不修改 root.parentElement 的 style（Comfy 靠它定位 DOM，一改就整块错位/叠层）
    // 2) 宽度交给用户拖拽，只兜 MIN_NODE_WIDTH 下限；不再用 Math.max(860) 把宽度弹回去
    // 3) 高度始终按内容收高：时间线操作框高度只由素材轨数决定，拖拽不会把它拉大，也不会无限往下长
    const CHROME = 56;
    const MIN_H = 240;  // 自动高度下限
    const MANUAL_HEIGHT_KEY = "ad_h3_manual_height";  // 旧版遗留的手动高度字段，读取时清掉

    const fillTimelineWidth = () => {
        root.style.width = "100%";
        root.style.maxWidth = "100%";
        root.style.minWidth = "0";
        root.style.boxSizing = "border-box";
        // 故意不碰 parent
    };

    const clipRoot = (h) => {
        root.style.height = `${h}px`;
        root.style.maxHeight = `${h}px`;
        root.style.minHeight = "0";
        root.style.overflowX = "hidden";
        root.style.overflowY = (root.querySelector(".h3-tl-inner")?.scrollHeight || 0) > h ? "auto" : "hidden";
    };

    // ---------- 智能切分（踩节拍 / 镜头 / 气口） ----------
    const clipScope = clip => JSON.stringify([clip.source,clip.tl_start,clip.src_in,clip.src_out]);
    function rememberCandidates(clip, frames) {
        T.smartOwners ||= {}; T.smartScopes ||= {};
        T.smartScopes[clip.id] = clipScope(clip);
        for (const frame of frames) T.smartOwners[frame] = [...new Set([...(T.smartOwners[frame] || []),clip.id])];
        T.smartCuts = [...new Set([...T.smartCuts,...frames])].sort((a,b)=>a-b);
    }
    function candidateCuts(clip) {
        if (T.smartScopes?.[clip.id] !== clipScope(clip)) return [];
        return T.smartCuts.filter(frame=>T.smartOwners?.[frame]?.includes(clip.id) && frame/FPS>clip.tl_start+.5/FPS && frame/FPS<clipEnd(clip)-.5/FPS);
    }
    function clearSelectedCandidates(clips) {
        const ids = new Set(clips.map(clip=>clip.id));
        for (const frame of T.smartCuts) {
            T.smartOwners ||= {};
            T.smartOwners[frame] = (T.smartOwners[frame] || []).filter(id=>!ids.has(id));
        }
        T.smartCuts = T.smartCuts.filter(frame=>T.smartOwners[frame]?.length);
        root._adH3SmartCuts = [...T.smartCuts];
    }
    function moveCandidateFrame(oldFrame,newFrame) {
        T.smartOwners ||= {};
        if (newFrame != null) T.smartOwners[newFrame] = [...new Set([...(T.smartOwners[newFrame]||[]),...(T.smartOwners[oldFrame]||[])])];
        delete T.smartOwners[oldFrame];
        T.smartCuts = [...new Set(T.smartCuts.flatMap(frame=>frame===oldFrame?(newFrame==null?[]:[newFrame]):[frame]))].sort((a,b)=>a-b);
    }
    function paintSmartInfo(extra = "") {
        const info = $("[data-smart-info]");
        if (info) info.textContent = extra + (T.smartCuts.length ? `候选 ${T.smartCuts.length} 个（仅对所属可见片段生效）` : "");
    }
    async function smartDetect() {
        if (T.detecting) { hint("正在检测，请稍候。",true); return; }
        const clips = requireSelectedClips(); if (!clips.length) return;
        const scopes = JSON.stringify(clips.map(clip=>[clip.id,clipScope(clip)]));
        const kindValue = $("[data-smart-kind]")?.value || "beats-beat";
        const kind = kindValue.startsWith("beats") ? "beats" : kindValue;
        const gap = Number($("[data-smart-gap]")?.value) || 1;
        const maxGap = Number($("[data-smart-max]")?.value) || 15;
        const sensitivity = Number($("[data-smart-sens]")?.value) || .5;
        const vocals = $("[data-smart-vocals]")?.getAttribute("aria-pressed") === "true";
        const options = kind === "beats" ? {mode:kindValue === "beats-bar"?"bar":"beat",min_gap:gap,max_gap:maxGap}
            : kind === "shots" ? {threshold:Math.max(.15,.6-sensitivity*.6),min_shot:Math.max(.5,gap/2),max_gap:maxGap}
            : {sensitivity,min_seg:Math.max(.8,gap/2),max_seg:maxGap,min_breath:.15,buffer:.05,vocals};
        T.detecting=true; $("[data-smart-detect]").disabled=true;
        setMaterialProgress(node,{busy:true,phase:"running",fraction:null,text:"检测中"});
        hint("正在检测所选可见片段…");
        try {
            const results=[];
            for (const clip of clips) {
                const source=sourcesOf()[clip.source];
                const response=await api.fetchApi("/aiden_h3/detect_cuts",{method:"POST",timeoutMs:null,headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({kind,origin:source.origin,file:source.file,options,range:{start:clip.src_in,end:clip.src_out}})});
                const data=await response.json();if(!response.ok||data.error)throw Error(data.error||"检测失败");
                results.push([clip,(data.cuts||[]).filter(frame=>frame/FPS>clip.src_in+.5/FPS&&frame/FPS<clip.src_out-.5/FPS)
                    .map(frame=>Math.round((clip.tl_start+frame/FPS-clip.src_in)*FPS))]);
            }
            if(scopes!==JSON.stringify(selectedEditableClips().map(clip=>[clip.id,clipScope(clip)]))){hint("所选片段或可见状态已变化，本次检测结果未应用。",true);setMaterialProgress(node,{busy:false,phase:"paused",fraction:null,text:"检测未应用"});return;}
            clearSelectedCandidates(clips);
            for(const [clip,frames] of results)rememberCandidates(clip,frames);
            root._adH3SmartCuts=[...T.smartCuts];renderOverlays();paintSmartInfo();
            hint(`检测完成：所选 ${clips.length} 个片段的候选切点已标黄。`);
            setMaterialProgress(node,{busy:false,phase:"completed",fraction:1,text:"检测完成"});
        }catch(error){hint(`检测失败：${error.message}`,true);setMaterialProgress(node,{busy:false,phase:"error",fraction:0,text:"检测失败"});}
        finally{T.detecting=false;$("[data-smart-detect]").disabled=false;if(node._adH3LocalProgress)node._adH3LocalProgress.busy=false;}
    }
    function splitVideoAtPlayhead() {
        if (T.playing) return;
        if (!hasAvClips()) { hint("时间线上没有音视频片段，无法切分", true); return; }
        const t = Math.round(T.playhead * FPS) / FPS;
        const total = Math.round(duration() * FPS) / FPS;
        if (t <= 0.5 / FPS || t >= total - 0.5 / FPS) {
            hint("请把白色游标移到时间线内部，再点「切分视频」。", true); return;
        }
        const selected = requireSelectedClips();
        if (!selected.length) return;
        const targets = selected.filter(clip => t > clip.tl_start + 0.5 * FRAME && t < clipEnd(clip) - 0.5 * FRAME);
        if (!targets.length) { hint("请把白色游标移到所选音视频片段内部。", true); return; }
        pushUndo();
        splitSelectedClips(targets, [t]);
        afterEdit();
        paintSmartInfo();
        hint(`已在 ${fmtClock(t)} 切分 ${targets.length} 个所选音视频片段；其它片段保持，可按撤销恢复。`);
    }
    function smartApply() {
        if (smartApply._busy) return;
        const clips = requireSelectedClips(); if (!clips.length) return;
        const cutsByClip = new Map(clips.map(clip=>[clip.id,candidateCuts(clip).map(frame=>frame/FPS)]));
        const targets = clips.filter(clip=>cutsByClip.get(clip.id).length);
        if (!targets.length) { hint("所选片段没有可应用的候选切点，请先检测或添加切分线。",true);return; }
        const maxFrames=Math.max(1,Math.round((Number($("[data-smart-max]")?.value)||15)*FPS));
        for(const clip of targets){
            const bounds=[clip.tl_start,...cutsByClip.get(clip.id),clipEnd(clip)].map(t=>Math.round(t*FPS));
            if(bounds.some((value,i)=>i&&value-bounds[i-1]>maxFrames)){hint(`所选片段内存在超过 ${maxFrames/FPS} 秒的区间，请增加候选切点后应用。`,true);return;}
        }
        smartApply._busy=true;setTimeout(()=>{smartApply._busy=false;},0);
        pushUndo();splitSelectedClips(targets,cutsByClip);clearSelectedCandidates(targets);T.segment=-1;
        afterEdit();paintSmartInfo();hint(`已应用所选可见片段的切分，清单共 ${intervals().length} 段；可撤销。`);
    }
    const detectButton = $("[data-smart-detect]");
    if (detectButton && !detectButton.dataset.adH3Bound) {
        detectButton.dataset.adH3Bound = "1";
        detectButton.addEventListener("click", () => void smartDetect());
    }
    const applyButton = $("[data-smart-apply]");
    if (applyButton && !applyButton.dataset.adH3Bound) {
        applyButton.dataset.adH3Bound = "1";
        applyButton.addEventListener("click", smartApply);
    }
    const addCutButton = $("[data-smart-add]");
    if (addCutButton && !addCutButton.dataset.adH3Bound) {
        addCutButton.dataset.adH3Bound = "1";
        addCutButton.addEventListener("click", () => {
            if (!hasAvClips()) { hint("请先在时间线上加载视频或音频，再添加切分线。", true); return; }
            const frame = Math.round(T.playhead * FPS);
            if (frame <= 0 || frame >= Math.round(duration() * FPS)) {
                hint("请把白色游标移到时间线内部，再添加切分线。", true); return;
            }
            const clips=requireSelectedClips().filter(clip=>frame/FPS>clip.tl_start+.5/FPS&&frame/FPS<clipEnd(clip)-.5/FPS);
            if(!clips.length){hint("请把游标移到所选可见片段内部。",true);return;}
            for(const clip of clips)rememberCandidates(clip,[frame]);
            root._adH3SmartCuts = [...T.smartCuts];
            renderOverlays(); paintSmartInfo();
            hint(`已在 ${fmtClock(frame/FPS)} 添加黄色候选线；点「应用切分」后生效。`);
        });
    }
    const clearButton = $("[data-smart-clear]");
    if (clearButton && !clearButton.dataset.adH3Bound) {
        clearButton.dataset.adH3Bound = "1";
        clearButton.addEventListener("click", () => {
            const clips=requireSelectedClips();if(!clips.length)return;
            clearSelectedCandidates(clips);renderOverlays();paintSmartInfo();hint("已清空所选片段的候选切点。");
        });
    }


    const smartCutButton = $("[data-smart-cut]");
    if (smartCutButton && !smartCutButton.dataset.adH3Bound) {
        smartCutButton.dataset.adH3Bound = "1";
        smartCutButton.addEventListener("click", () => splitVideoAtPlayhead());
    }
    let followedPlayhead = null;
    node._adH3FollowSegment = (seconds, {remember = false} = {}) => {
        const value = Number(seconds);
        if (!Number.isFinite(value)) return;
        if (remember && followedPlayhead === null) followedPlayhead = T.playhead;
        T.playhead = Math.max(0, Math.min(duration(), value));
        renderOverlays();
        renderClock({seekPreview: true});
        syncButtons();
    };
    node._adH3FollowRestore = () => {
        if (followedPlayhead === null) return;
        T.playhead = Math.max(0, Math.min(duration(), followedPlayhead));
        followedPlayhead = null;
        renderOverlays();
        renderClock({seekPreview: true});
        syncButtons();
    };
    const runAffectedButton = $("[data-run-affected]");
    $("[data-new-group]")?.addEventListener("click", async event => {
        const segment=linkedSegment();
        if (!segment?._adH3NewGroup) { hint("请先连接「分段生成」与「分段预览」后再新建一组。", true); return; }
        const button=event.currentTarget;button.disabled=true;
        try {
            const state=await segment._adH3NewGroup();
            if(state===false){hint("未新建一组，请查看「分段预览」中的提示。",true);return;}
            hint(`已开启第 ${state?.round_no || "新"} 组，从第1段待命；旧成果保留。`);
        } catch(error) { hint(error.message,true); }
        finally { button.disabled=false; }
    });
    if (runAffectedButton && !runAffectedButton.dataset.adH3Bound) {
        runAffectedButton.dataset.adH3Bound = "1";
        runAffectedButton.addEventListener("click", () => {
            const graph = node.graph;
            let segmentNode = null;
            const links = graph?.links;
            const values = typeof links?.values === "function" ? [...links.values()] : Object.values(links || {});
            for (const link of values) {
                if (!link) continue;
                if (String(link.origin_id) !== String(node.id) || Number(link.origin_slot) !== 0) continue;
                const target = graph.getNodeById?.(link.target_id)
                    || (graph._nodes || []).find(item => String(item.id) === String(link.target_id));
                if (target && (target.comfyClass || target.type) === "AidenH3Segment") { segmentNode = target; break; }
            }
            if (!segmentNode) { hint("请先把素材清单连接到「分段生成」的「queue」输入口。", true); return; }
            if (typeof segmentNode._adH3RunAffected !== "function") { hint("请保存工作流后刷新页面（Ctrl+F5），加载最新前端后再使用「运行此段」。", true); return; }
            const videos = activeClips(T.state,"video_tracks",T.selected);
            const audios = activeClips(T.state,"audio_tracks",T.selected);
            const generated = generationClips(T.state).filter(clip => T.selected.has(clip.id));
            const selected = videos.length ? videos : (audios.length ? audios : generated);
            const indices = [...new Set(selected.map(segmentIndexForClip).filter(index => index >= 0))].sort((a,b) => a-b);
            if (hasAvClips() && !selected.length) { requireSelectedClips(); return; }
            if (!hasAvClips() && !indices.length && T.segment >= 0) indices.push(T.segment);
            if (T.selected.size && !indices.length) { hint("所选片段没有对应的生成分段，请先应用分段。", true); return; }
            hint(indices.length ? `正在准备运行所选第 ${indices.map(i => i+1).join("、")} 段，完成后回到原进度。`
                : "正在检查并重跑受影响段，完成后回到原运行位置。");
            void segmentNode._adH3RunAffected(indices.length ? {indices} : {}).then(result => {
                if (result === false) hint("选段运行未完成，请查看分段预览中的提示。", true);
            });
        });
    }
    const vocalsButton = $("[data-smart-vocals]");
    if (vocalsButton && !vocalsButton.dataset.adH3Bound) {
        vocalsButton.dataset.adH3Bound = "1";
        vocalsButton.addEventListener("click", () => {
            const on = vocalsButton.getAttribute("aria-pressed") !== "true";
            vocalsButton.setAttribute("aria-pressed", String(on));
            vocalsButton.classList.toggle("on", on);
        });
    }
    let contentWidgetH = MIN_H;
    let layoutBusy = false;
    let applySizeGuard = 0;  // 我们自己 setSize 时忽略 onResize/afterResize，避免误锁高度
    let lastAutoNodeH = 0;   // 最近一次按内容算出的节点高度；拖拽高度时立即收回

    const measureContentH = () => {
        const inner = root.querySelector(".h3-tl-inner");
        if (!inner) return MIN_H;
        // 临时放开高度只为测量，测完立刻 clip 回去，避免泄出叠层
        const prevH = root.style.height;
        const prevMax = root.style.maxHeight;
        const prevOX = root.style.overflowX;
        const prevOY = root.style.overflowY;
        root.style.height = "auto";
        root.style.maxHeight = "none";
        root.style.overflowX = "hidden";
        root.style.overflowY = "visible";
        void inner.offsetHeight;
        const h = Math.max(MIN_H, Math.ceil(inner.scrollHeight + 8));
        root.style.height = prevH;
        root.style.maxHeight = prevMax;
        root.style.overflowX = prevOX;
        root.style.overflowY = prevOY;
        return h;
    };

    const currentWidgetH = () => contentWidgetH;

    const applyNodeSize = (width, height) => {
        applySizeGuard++;
        try {
            node.size = [width, height];
            node.setSize([width, height]);
        } finally {
            queueMicrotask(() => { applySizeGuard = Math.max(0, applySizeGuard - 1); });
        }
    };

    const syncFromContent = ({ forceSize = false } = {}) => {
        if (layoutBusy || interactionLock) return;
        layoutBusy = true;
        try {
            // 旧版本存过的手动高度会让节点被历史值撑高，读取时清掉。
            if (node.properties && MANUAL_HEIGHT_KEY in node.properties) delete node.properties[MANUAL_HEIGHT_KEY];
            markWidthBreakpoint(root);
                fillTimelineWidth();
            syncViewHeight();  // 操作框高度只由素材轨数决定，不随节点宽度/拖拽变化
            renderOverlays();
            paintViewportMedia();
            contentWidgetH = measureContentH();
            const h = currentWidgetH();
            clipRoot(h);
            const width = Math.max(MIN_NODE_WIDTH, Number(node.size?.[0]) || MIN_NODE_WIDTH);
            const autoH = Math.ceil(node.computeSize?.()[1] || (h + CHROME));
            lastAutoNodeH = autoH;
            if (forceSize || Math.abs((node.size?.[1] ?? 0) - autoH) > 2 || Math.abs((node.size?.[0] ?? 0) - width) > 1) {
                applyNodeSize(width, autoH);
            }
            node.setDirtyCanvas?.(true, true);
        } finally {
            setTimeout(() => { layoutBusy = false; }, 0);
        }
    };

    function applyPoolExtra(extra = 0) {
        const poolEl = $("[data-pool]");
        if (!poolEl) return;
        for (const image of poolEl.querySelectorAll(".h3-shot img")) {
            for (const name of ["height", "min-height", "max-height"]) image.style.setProperty(name, "90px", "important");
            for (const name of ["width", "min-width", "max-width"]) image.style.setProperty(name, "160px", "important");
            image.style.setProperty("aspect-ratio", "16/9", "important");
        }
        const addButton = poolEl.querySelector(".h3-image-add");
        if (addButton) { addButton.style.setProperty("height", "160px", "important"); addButton.style.setProperty("flex", "0 0 172px", "important"); addButton.style.setProperty("width", "172px", "important"); }
        for (const shot of poolEl.querySelectorAll(".h3-shot")) {
            shot.style.setProperty("min-height", "160px", "important");
            shot.style.setProperty("width", "172px", "important");
            shot.style.setProperty("min-width", "172px", "important");
            shot.style.setProperty("max-width", "172px", "important");
            shot.style.setProperty("flex", "0 0 172px", "important");
        }
    }
    const widget = node.addDOMWidget("queue_state", "div", root, {
        hideOnZoom: false,
        getHeight: () => currentWidgetH() + 8,
        getValue: () => {
            if(node._adWorkflowSession){T.state.session_id=node._adWorkflowSession;T.state.workflow_task_id=node._adWorkflowTask;}
            if (node._adH3MaterialsReady !== false && applyPromptOnlySegments()) {
                queueMicrotask(() => { if (node.graph) { render(); node.setDirtyCanvas?.(true, true); } });
            }
            return JSON.stringify(T.state);
        },
        setValue: restoreEditor,
        afterResize() {
            fillTimelineWidth();
            markWidthBreakpoint(root);
            if (layoutBusy || applySizeGuard) return;
            if ((Number(node.size?.[0]) || 0) < MIN_NODE_WIDTH) {
                applyNodeSize(MIN_NODE_WIDTH, Math.max(120, Number(node.size?.[1]) || 0));
                return;
            }
            // 手动拉高：多出来的高度给「参考图片」缩略图区（缩略图随之变高），下方不再留空白；拉矮则收回。
            const sizeH = Number(node.size?.[1]) || 0;
            const naturalH = Number(lastAutoNodeH) || sizeH;
            const extra = Math.max(0, Math.round(sizeH - naturalH));
            if (Math.abs(extra - (Number(node._adH3PoolExtra) || 0)) > 2) {
                node._adH3PoolExtra = extra;
                applyPoolExtra(extra);
                scheduleLayout(0);
                return;
            }
            if (extra <= 0 && naturalH && Math.abs(sizeH - naturalH) > 2) {
                applyNodeSize(node.size[0], naturalH);
            }
            scheduleLayout(0);
        },
    });
    bindWidgetWidth(node, widget);
    const requiredWidth = () => MIN_NODE_WIDTH;  // 工具栏自行换行，不反向撑宽用户节点。
    const previousOnResize = node.onResize;
    node.onResize = function () {
        const out = previousOnResize?.apply(this, arguments);
        if (!applySizeGuard) {
            const need = requiredWidth();
            if ((Number(this.size?.[0]) || 0) < need) {
                applyNodeSize(need, Math.max(120, Number(this.size?.[1]) || 0));
            }
        }
        return out;
    };
    fillTimelineWidth();
    syncFromContent({ forceSize: true });
    if ((Number(node.size?.[0]) || 0) < requiredWidth()) applyNodeSize(requiredWidth(), Math.max(120, Number(node.size?.[1]) || 0));
    const onConfigure = node.onConfigure;
    applyLayoutHeight = () => syncFromContent({ forceSize: false });
    // 旧节点可能先在画布外隐藏，挂载时只能量到0；显示或改宽后重新量真实内容。
    const contentObserver = new ResizeObserver(() => {
        if (root.isConnected && root.offsetWidth > 0) {
            markWidthBreakpoint(root);
            scheduleLayout(40);
        }
    });
    contentObserver.observe(root.querySelector(".h3-tl-inner"));
    node.onConfigure = function (info) {
        node._adH3MaterialsReady = false;
        const result = onConfigure?.apply(this, arguments);
        // 旧隐藏控件可能还保存着上次加载的 JSON；当前 DOM 编辑器的值追加在后，优先取它。
        const saved = savedTimelineValue(info);
        setTimeout(() => { if (node.graph) { if (saved) restoreEditor(typeof saved === "string" ? saved : JSON.stringify(saved)); else { node._adH3MaterialsReady = true; updateMaterialOutputs(node, T.state); } applyLayoutHeight(); } }, 0);
        return result;
    };
    const onRemoved = node.onRemoved;
    node.onRemoved = function () {
        storagePanel.dispose();
        stopPlayback();clearInterval(planTimer);
        contentObserver.disconnect(); clearTimeout(layoutTimer);
        document.removeEventListener("keydown", onEditorKeydown, {capture:true});
        document.removeEventListener("pointerdown", onEditorPointerDown, true);
        if (activeEditorRoot === root) activeEditorRoot = null;
        previewDisposed = true;
        view.removeEventListener("scroll", onRulerScroll);
        if (rulerScrollFrame) cancelAnimationFrame(rulerScrollFrame);
        if (wheelZoomFrame) cancelAnimationFrame(wheelZoomFrame);
        wheelZoomPoint = null;
        if (previewSeekTimer) cancelAnimationFrame(previewSeekTimer);
        previewSeekRevision++;
        clearInterval(planTimer);
        clearInterval(promptRuleTimer);
        for (const job of audioPending.values()) job.controller.abort();
        audioPending.clear(); audioCache.clear(); void audioCtx?.close(); T.buffers.clear();
        thumbCache.clear();
        
        root.remove();  // 节点删除时必须移除控件，否则残留的幽灵层会截走全部点击
        if (activeEditorRoot === root) activeEditorRoot = null;
        return onRemoved?.apply(this, arguments);
    };
    const planTimer = setInterval(() => {
        if (interactionLock || document.hidden) return;
        renderFramePlan();
    }, 5000);
    // 素材准备没有任何素材时，正向提示词的 # 段数变化后自动同步分段数（不用再手点「应用分段」）。
    const promptRuleTimer = setInterval(() => {
        if (interactionLock || document.hidden || !node.graph) return;
        node._adH3SyncPromptSegments();
    }, 2000);
    // 首次挂载：用 emptyTimeline 的 recipe 填输入并画出生成段（configure 有存档时再由 restoreEditor 覆盖）
    {
        const recipe = T.state.segment_recipe || defaultSegmentRecipe();
        T.unit = recipe.unit === "s" ? "s" : "f";
        const frames = recipe.frames || DEFAULT_SEG_FRAMES;
        $("[data-every]").value = T.unit === "s" ? Number((frames / FPS).toFixed(6)) : frames;
        $("[data-every]").step = T.unit === "s" ? "0.001" : "17";
        root.querySelectorAll("[data-unit]").forEach(item => item.classList.toggle("on", item.dataset.unit === T.unit));
        syncToggleA11y();
        $("[data-count]").value = recipe.count || DEFAULT_SEG_COUNT;
    }
    render();
    
    const prevConn = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = prevConn?.apply(this, arguments);
        node._adH3SyncPromptSegments?.();
        try { updateMaterialOutputs(node, T.state); } catch {}
        return result;
    };
try { updateMaterialOutputs(node, T.state); } catch {}
    // 只量一次高度，避免贴合死循环
    setTimeout(() => { if (node.graph) applyLayoutHeight(); }, 0);
}
