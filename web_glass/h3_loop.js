// CACHEBUST ref-link-preserve 20261025-053810
// cachebust 20261025-0440 segment-dedupe-materials
// CACHEBUST 2026-10-25-restore-v027-approved-043100
// CACHEBUST 20261102-group-v64：统一 web_glass 模块版本号，模块去重并避免沿用旧缓存
import "./ad_theme.js?v=20261102-group-v64";
import {ensureWorkflowTask,currentTaskId} from "./workflow_task.js?v=20261102-group-v64";
// final_output.js由ComfyUI作为扩展入口自动加载，不重复注册。
import { installGlassTheme } from "./glass_ui.js?v=20261102-group-v64";
import { executeQueueOnce, interruptOwnedPrompt } from "./queue_runner.js?v=20261102-group-v64";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { mountTimelineEditor, segmentRefCounts, updateSegmentMaterialOutputs, savedTimelineValue } from "./timeline_editor.js?v=20261102-group-v64";
import { mountNativePlayer } from "./native_player.js?v=20261102-group-v64";
import { bindWidgetWidth, fitPreviewContent } from "./dom_layout.js?v=20261102-group-v64";
import { mountNodeProgress, updateSessionProgress, installProgressEvents } from "./run_progress.js?v=20261102-group-v64";

import { installRoundQueue } from "./round_queue.js?v=20261102-group-v64";
installRoundQueue(api, app);
installProgressEvents(api);

const styles = document.createElement("style");
styles.id="ad-h3-loop-ref-links-20261025-053810";
styles.textContent = `
.aiden-h3{box-sizing:border-box!important;background:var(--ad-bg);color:var(--ad-text);border:none;border-radius:8px;padding:12px;font-family:var(--ad-font);font-size:var(--ad-font-body);line-height:1.5;width:100%!important;max-width:100%!important;min-width:0!important;overflow:visible}
.aiden-h3 *{box-sizing:border-box}.aiden-h3 h3{margin:0 0 10px;font-size:15px;font-weight:600}.aiden-h3 .h3-tools{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0}
.aiden-h3 button,.aiden-h3 select{font:inherit;color:inherit;background:var(--ad-surface-deep);border:1px solid var(--ad-border);border-radius:var(--ad-radius-sm);height:var(--ad-control-h);min-height:var(--ad-control-h);padding:0 10px;line-height:1.2;cursor:pointer}
.aiden-h3 button:hover{background:#333}.aiden-h3 button.primary{background:var(--ad-accent);border-color:var(--ad-accent-border);color:#fff}.aiden-h3 button.primary:hover{background:var(--ad-accent-hover)}
.aiden-h3 button.danger{border-color:var(--ad-danger);color:var(--ad-danger-text)}
.aiden-h3 button:disabled{opacity:.45;cursor:default}.aiden-h3 select{max-width:100%;min-width:0}.aiden-h3 .h3-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;padding:8px 0;border-bottom:1px solid var(--ad-border)}
.aiden-h3 .h3-name{word-break:break-word;min-width:0}.aiden-h3 small{color:var(--ad-text-dim)}.aiden-h3 .h3-row button{padding:2px 7px;min-height:27px}.aiden-h3 .h3-row .h3-tools{margin:0;gap:4px;align-items:center}
.aiden-h3 .h3-message{white-space:pre-wrap;overflow-wrap:anywhere;color:#b6c9ed;margin:6px 0}.aiden-h3 .h3-message.error{color:var(--ad-danger-text)}.aiden-h3 audio{width:100%;height:34px;margin-top:9px}.aiden-h3 .h3-list{max-height:285px;overflow:auto}
.aiden-h3 .h3-status{padding:8px;background:var(--ad-surface);border-radius:var(--ad-radius-sm)}.aiden-h3 .h3-current{color:var(--ad-accent-soft)}.aiden-h3 a{color:var(--ad-accent-soft)}.aiden-h3 .h3-review-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:5px 0}
.aiden-h3 .h3-scores{flex-basis:100%;color:var(--ad-text-dim);overflow-wrap:anywhere}.aiden-h3 .h3-score-bad{color:var(--ad-danger-text)}.aiden-h3 input[type=number]{width:83px;height:var(--ad-control-h);background:var(--ad-surface-deep);color:var(--ad-text);border:1px solid var(--ad-border);padding:0 6px;border-radius:4px;font:inherit}
.aiden-h3 .h3-settings label{display:inline-flex;gap:5px;align-items:center;flex-wrap:wrap}.aiden-h3 .h3-compare{display:flex;gap:10px;flex-wrap:wrap}.aiden-h3 .h3-compare figure{flex:1 1 220px;min-width:0;margin:5px 0}.aiden-h3 video{width:100%;max-height:300px;background:#111}.aiden-h3 .h3-compare select{width:100%}
.ad-h3-video *{font-family:var(--ad-font)!important}
.ad-h3-prefix-editor input{display:block;width:100%;max-width:100%;height:30px!important;min-height:30px!important;background:rgba(15,20,30,.92);color:#e8eef7;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:4px 10px;font:13px 'Microsoft YaHei',system-ui,sans-serif;box-sizing:border-box;outline:none}.ad-h3-prefix-editor input::placeholder{color:rgba(200,214,235,.45)}
.ad-h3-prompt-area{display:block;width:100%;max-width:100%;height:226px!important;min-height:226px!important;resize:none;background:rgba(15,20,30,.92);color:#e8eef7;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:8px 10px;font:13px/1.5 'Microsoft YaHei',system-ui,sans-serif;box-sizing:border-box;outline:none}.ad-h3-prompt-area::placeholder{color:rgba(200,214,235,.42);white-space:pre-line;line-height:1.5}
.ad-h3-prompt-area.ad-h3-negative-area{height:120px!important;min-height:120px!important;max-height:120px!important}
.ad-h3-prompt-hint{font-size:11px;line-height:1.5;color:rgba(200,214,235,.55);padding:2px 8px 6px;white-space:normal}
.ad-h3-resolution-badge{display:block;width:100%;box-sizing:border-box;font-size:16px;font-weight:700;color:#cfd9e8;padding:3px 6px;margin:0 0 4px;letter-spacing:.5px;text-align:center;line-height:1.3;min-height:24px}
.dom-widget:has(> .ad-h3-video),.dom-widget:has(.ad-h3-video){pointer-events:none!important}
.dom-widget:has(.aiden-h3){pointer-events:none!important}
.aiden-h3 button,.aiden-h3 input,.aiden-h3 select,.aiden-h3 textarea,.aiden-h3 video,.aiden-h3 audio,.aiden-h3 img,.aiden-h3 .h3-tlbar,.aiden-h3 .h3-tools,.aiden-h3 .h3-loop-actions,.aiden-h3 .h3-list,.aiden-h3 .ad-h3-media-list,.aiden-h3 .ad-h3-media-card,.aiden-h3 .h3-view,.aiden-h3 .h3-canvas,.aiden-h3 .h3-ruler,.aiden-h3 .h3-ev-wrap,.aiden-h3 .h3-scroll{pointer-events:auto}

.ad-h3-video{pointer-events:none;overflow:hidden}.ad-h3-video button,.ad-h3-video input,.ad-h3-video select,.ad-h3-video video,.ad-h3-video img,.ad-h3-video audio,.ad-h3-video .ad-h3-media-card,.ad-h3-video .ad-h3-preview-thumb{pointer-events:auto}
.ad-h3-preview-layout{display:flex;flex-direction:column;gap:10px;width:100%;min-width:0}
.ad-h3-strips{display:flex;flex-direction:column;gap:6px;min-width:0;padding:2px 4px}
.ad-h3-strip-row{display:flex;gap:8px;align-items:flex-start;min-width:0;width:100%;border:1px solid #3d5a80;border-radius:8px;background:#141b26;padding:4px 8px;min-height:104px;box-sizing:border-box}.ad-h3-strip-row>span{flex:0 0 64px;white-space:nowrap}
.ad-h3-segments,.ad-h3-segments-b,.ad-h3-merges{display:flex;gap:6px;overflow:auto;min-width:0;flex:1;min-height:96px;align-items:flex-start;padding:2px 0}
.aiden-h3 .ad-h3-preview-thumb{width:112px;min-width:112px;padding:4px;font-size:12px;overflow-wrap:anywhere;position:relative}
.ad-h3-preview-remove{position:absolute;top:3px;right:3px;width:18px;height:18px;border-radius:50%;background:rgba(0,0,0,.62);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;line-height:1;cursor:pointer;z-index:2}
.ad-h3-preview-thumb .ad-h3-preview-remove{display:flex}
.ad-h3-preview-remove:hover{background:#C0392B}
.ad-h3-preview-thumb img,.ad-h3-preview-thumb video{width:100%;aspect-ratio:16/9;height:auto;object-fit:contain;background:#111;display:block}.ad-h3-preview-thumb span{display:block}
.ad-h3-video.aiden-h3 .ad-h3-loop-actions button.ad-h3-playlist-toggle[aria-pressed="false"]{background:#282f3c!important;border-color:#505865!important;color:#b8c0cc!important;box-shadow:none!important}
.ad-h3-video.aiden-h3 .ad-h3-loop-actions button.ad-h3-playlist-toggle.active{background:#3f91ef!important;border-color:#c4e2ff!important;color:#fff!important;box-shadow:0 0 0 2px #69b5ff66,inset 0 0 8px #cceaff33}
.aiden-h3 .ad-h3-preview-thumb.selected{border-color:#7aafff;background:#254d81}
.aiden-h3 .ad-h3-preview-thumb.merge-selected{outline:3px solid #4cd964;outline-offset:-3px;box-shadow:0 0 0 2px rgba(76,217,100,.35)} .ad-h3-merge-wrap{position:relative;display:inline-flex}.ad-h3-merge-menu{position:absolute;left:0;top:calc(100% + 4px);z-index:12;display:flex;flex-direction:column;gap:4px;padding:5px;border:1px solid #4A80BF;border-radius:9px;background:#1b2b40;box-shadow:0 8px 22px rgba(0,0,0,.5)}.ad-h3-merge-menu[hidden]{display:none}.ad-h3-merge-menu button{white-space:nowrap;padding:6px 14px;border-radius:7px;background:#3B7DC4;color:#fff;border:1px solid #4A80BF;font:600 13px "Microsoft YaHei",system-ui;cursor:pointer}.ad-h3-merge-menu button:hover{background:#326BA8}
.aiden-h3 .ad-h3-preview-thumb.redraw span:last-child{background:#F2C94C!important;border-top-color:#F2C94C!important;color:#1b1b1b!important;font-weight:700}
.ad-h3-screen{min-width:0;background:var(--ad-surface);aspect-ratio:16/9;overflow:hidden;position:relative}.ad-h3-video.aiden-h3 .ad-h3-screen:fullscreen,
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen{
 position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;
 min-width:0!important;min-height:0!important;max-width:none!important;max-height:none!important;
 aspect-ratio:auto!important;display:block!important;margin:0!important;padding:0!important;
 border:0!important;border-radius:0!important;box-shadow:none!important;background:#000!important;box-sizing:border-box!important}
.ad-h3-screen:fullscreen::backdrop,.ad-h3-ab-overlay:fullscreen::backdrop{background:#000}
.ad-h3-screen:fullscreen>.ad-h3-preview-title{display:none!important}
.ad-h3-video.aiden-h3 .ad-h3-screen:fullscreen>.ad-h3-player-controls,
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen>.ad-h3-ab-controls{background:rgba(0,0,0,.7)!important;border:0!important}
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen>.ad-h3-ab-controls{padding-right:260px!important}
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen .ad-h3-ab-videos{gap:0!important;background:#000!important}
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen .ad-h3-ab-vcell,
.ad-h3-video.aiden-h3 .ad-h3-ab-overlay:fullscreen .ad-h3-ab-acell{border:0!important;border-radius:0!important;background:#000!important}
.ad-h3-lightbox:fullscreen{background:#000!important;border:0!important;border-radius:0!important;padding:0!important}
.ad-h3-lightbox:fullscreen>.ad-h3-lightbox-video,.ad-h3-lightbox:fullscreen>.ad-h3-lightbox-img{width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;object-fit:contain;object-position:center}

/* 空闲态：没有结果时预览窗口保持同样大小（16:9 黑屏），不再收成小占位 */
.ad-h3-idle-hint{display:none;color:rgba(200,214,235,.58);font-size:13px;text-align:center;padding:0 16px;line-height:1.9;letter-spacing:1px}
.ad-h3-idle-hint span{display:block}
.ad-h3-preview-layout.is-idle .ad-h3-screen{background:#000!important;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px}
.ad-h3-preview-layout.is-idle .ad-h3-screen video,.ad-h3-preview-layout.is-idle .ad-h3-player-controls{display:none!important}
.ad-h3-screen>video{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;object-fit:contain;object-position:center center!important;background:#000;display:block}
.ad-h3-video-actions{display:none;gap:8px;flex-wrap:wrap;align-items:center}
.ad-h3-loop-hint{width:100%;font-size:12px;color:var(--ad-text-dim);white-space:pre-wrap}
.ad-h3-loop-hint.error{color:#f87171}
.ad-h3-loop-actions{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0}
.ad-h3-loop-actions button{padding:4px 14px;border-radius:6px;border:1px solid #4A80BF;background:#3B7DC4;color:#fff;cursor:pointer;font:600 13px var(--ad-font)}
.ad-h3-loop-actions button:hover{background:#326BA8}
.ad-h3-tabs{display:flex;gap:6px;margin:2px 0}
.ad-h3-tabs button{padding:4px 16px;border-radius:6px;border:1px solid var(--ad-border);background:transparent;color:var(--ad-text-dim);cursor:pointer;font:600 13px var(--ad-font)}
.ad-h3-tabs button.active{background:#254d81;color:#fff;border-color:#4d7ea8}
.ad-h3-media-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:8px;min-height:320px;height:320px;max-height:320px;overflow-y:scroll!important;overflow-x:auto;align-content:start;align-items:start;padding:4px;border:1px solid rgba(255,255,255,.12);border-radius:10px;background:rgba(10,14,22,.45);box-shadow:inset 0 0 12px rgba(0,0,0,.35)}.ad-h3-media-list::-webkit-scrollbar{width:10px;height:10px}.ad-h3-media-list::-webkit-scrollbar-thumb{background:#3B7DC4;border-radius:6px}.ad-h3-media-list::-webkit-scrollbar-track{background:rgba(255,255,255,.06);border-radius:6px}
.ad-h3-video .ad-h3-media-card{display:flex!important;flex:0 0 auto;flex-direction:column;gap:0;width:100%;padding:0;border:1px solid rgba(255,255,255,.16);border-radius:9px;background:#15181f;cursor:pointer;overflow:hidden;min-height:0!important;height:auto!important;box-shadow:0 2px 8px rgba(0,0,0,.35)}.ad-h3-media-card:hover{border-color:#4A80BF}
.aiden-h3 .ad-h3-media-card.selected,.aiden-h3.ad-h3-video .ad-h3-media-card.selected{outline:3px solid #4da3ff!important;outline-offset:-3px!important;border-color:#4da3ff!important;background:#25405f!important;box-shadow:0 0 0 2px rgba(77,163,255,.55),0 0 20px rgba(77,163,255,.85)!important}
.ad-h3-media-card img{flex:0 0 auto;width:100%;height:auto;aspect-ratio:16/9;object-fit:contain;background:#0b0e13;display:block}
.ad-h3-media-card video{flex:0 0 auto;width:100%;height:auto;aspect-ratio:16/9;object-fit:contain;background:#0b0e13;display:block;pointer-events:none}
.ad-h3-panel[data-panel="video"] .ad-h3-media-list{height:200px;min-height:200px;max-height:200px;margin-bottom:8px}
.ad-h3-audio-icon{aspect-ratio:16/9;height:auto;display:flex;align-items:center;justify-content:center;font-size:32px;color:#7aafff}
.ad-h3-tabs{display:flex;align-items:center;gap:8px;flex-wrap:nowrap;overflow:hidden;min-height:34px}.ad-h3-tabs [data-tab]{flex:0 0 auto;white-space:nowrap;padding:6px 14px}.ad-h3-tab-hint{margin-left:8px;font:700 14px 'Microsoft YaHei',system-ui;color:#FFD166;background:rgba(255,209,102,.12);border:1px solid rgba(255,209,102,.45);padding:5px 12px;border-radius:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 auto;min-width:0;max-width:none}.ad-h3-tab-hint.error{color:#ff6b6b;background:rgba(255,107,107,.14);border-color:rgba(255,107,107,.5)}.ad-h3-tab-hint[hidden]{display:none}
.ad-h3-preview-layout{position:relative}.ad-h3-preview-tools{display:flex;gap:8px;margin-left:auto;align-items:center;flex:0 0 auto;white-space:nowrap}.ad-h3-preview-tools[hidden]{display:none}.ad-h3-preview-tool{white-space:nowrap;padding:5px 12px;border-radius:9px;background:#3B7DC4 !important;color:#fff;border:1px solid #4A80BF;font:600 13px 'Microsoft YaHei',system-ui;cursor:pointer}.ad-h3-preview-tool:hover{background:#326BA8}
.ad-h3-preview-download{position:absolute;right:12px;bottom:12px;z-index:6;padding:7px 16px;border-radius:9px;background:#3B7DC4;color:#fff;border:1px solid #4A80BF;font:600 13px 'Microsoft YaHei',system-ui;cursor:pointer}.ad-h3-preview-download:hover{background:#326BA8}.ad-h3-preview-download[hidden]{display:none}
.ad-h3-lightbox{position:fixed;inset:0;background:rgba(0,0,0,.94);display:flex;align-items:center;justify-content:center;z-index:99999;cursor:zoom-out}
.ad-h3-lightbox-img{max-width:92vw;max-height:90vh;object-fit:contain;cursor:grab;user-select:none;transition:transform .05s linear}
.ad-h3-lightbox-video{max-width:92vw;max-height:88vh;object-fit:contain;background:#000;outline:none}
.ad-h3-lightbox-fullscreen{position:fixed;right:22px;top:calc(50% + 56px);transform:translateY(-50%);padding:10px 16px;border-radius:10px;background:#3B7DC4;color:#fff;border:1px solid #4A80BF;font:600 14px 'Microsoft YaHei',system-ui;cursor:pointer;z-index:100000}
.ad-h3-lightbox-fullscreen:hover{background:#326BA8}
.ad-h3-lightbox-download{position:fixed;right:22px;top:50%;transform:translateY(-50%);padding:10px 16px;border-radius:10px;background:#3B7DC4;color:#fff;border:1px solid #4A80BF;font:600 14px 'Microsoft YaHei',system-ui;cursor:pointer;z-index:100000}
.ad-h3-lightbox-download:hover{background:#326BA8}
.ad-h3-lightbox-close{position:fixed;right:22px;top:18px;width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.12);color:#fff;border:none;font-size:24px;line-height:1;cursor:pointer;z-index:100000}
.ad-h3-lightbox-close:hover{background:#C0392B}
.ad-h3-lightbox-hint{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);color:#d7e2f2;font:13px 'Microsoft YaHei',system-ui;background:rgba(0,0,0,.55);padding:6px 14px;border-radius:8px;max-width:80vw;z-index:100000}
.ad-h3-media-card{position:relative}
.ad-h3-video .ad-h3-media-list{grid-template-columns:repeat(auto-fill,162px)!important;justify-content:start!important;align-items:start!important}
.ad-h3-video .ad-h3-media-card{box-sizing:border-box!important;width:162px!important;min-width:162px!important;max-width:162px!important}
.ad-h3-video .ad-h3-media-card img,.ad-h3-video .ad-h3-media-card video,.ad-h3-video .ad-h3-audio-icon{box-sizing:content-box!important;width:160px!important;min-width:160px!important;max-width:160px!important;height:90px!important;min-height:90px!important;max-height:90px!important;aspect-ratio:16/9!important;object-fit:contain!important;flex:0 0 90px!important}
.ad-h3-media-remove{position:absolute;top:5px;right:5px;width:22px;height:22px;border-radius:50%;background:rgba(0,0,0,.62);color:#fff;display:flex;align-items:center;justify-content:center;font-size:15px;line-height:1;cursor:pointer;z-index:2}.ad-h3-media-remove:hover{background:#C0392B}
.ad-h3-media-serial{padding:6px 10px;font:600 13px var(--ad-font);color:var(--ad-text);text-align:center;background:#1b1f27;border-top:1px solid rgba(255,255,255,.1)}
/* 本段抽卡结果：编号黄色高亮，和普通生成结果区分。 */
.aiden-h3 .ad-h3-media-card.redraw .ad-h3-media-serial{background:#F2C94C!important;border-top-color:#F2C94C!important;color:#1b1b1b!important;font-weight:700}
.ad-h3-screen [hidden]{display:none!important}
.ad-h3-screen img[data-media-image]{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:100%!important;height:100%!important;max-height:none!important;object-fit:contain;background:#000;display:block}
.ad-h3-screen audio[data-media-audio]{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:90%;max-width:560px;margin:0;display:block}
.ad-h3-preview-layout.is-idle .ad-h3-screen img[data-media-image],.ad-h3-preview-layout.is-idle .ad-h3-screen audio[data-media-audio]{display:none!important}
.ad-h3-preview-layout.is-idle .ad-h3-preview-title{display:none}
.ad-h3-preview-layout.is-initial .ad-h3-idle-hint{display:block!important}
.ad-h3-strip-empty{color:var(--ad-text-mute);font-size:13px;line-height:30px;padding:0 4px}
.ad-h3-preview-title{position:absolute;top:6px;left:8px;z-index:3;padding:5px;overflow-wrap:anywhere;background:rgba(0,0,0,.38);border-radius:6px;max-width:calc(100% - 16px)}.ad-h3-video video{width:100%;max-height:540px;aspect-ratio:16/9;object-fit:contain;background:#111}
.ad-h3-player-controls{display:flex;flex-wrap:wrap;gap:5px;padding:6px;align-items:center}.ad-h3-player-controls input[type=range]{min-width:90px;flex:1}
.aiden-h3 .h3-tools{width:100%;display:flex;flex-wrap:wrap;gap:7px}
.aiden-h3 .h3-compare-empty{padding:12px;text-align:center;color:#93a5bf;font-size:12px;border:1px dashed #3c495d;border-radius:8px;background:#1b2431}
.aiden-h3 .h3-list,.aiden-h3 .h3-settings,.aiden-h3 .h3-compare,.aiden-h3 .h3-status,.aiden-h3 .h3-message{width:100%;max-width:100%}
.dom-widget .aiden-h3{width:100%!important}
/* 节点宽度档位：data-w 由 dom_layout.js 按控件实际宽度标记 */
.ad-h3-video[data-w="sm"] .ad-h3-preview-thumb{width:112px;min-width:112px}
.ad-h3-video[data-w="lg"] .ad-h3-preview-thumb{width:112px;min-width:112px}

`;
document.head.appendChild(styles);

const label = {ready:"待生成",queued:"已排队",running:"生成中",review:"已完成待继续",paused:"已停止",completed:"全部完成",merging:"正在合并",error:"出错了"};
const natural = new Intl.Collator(undefined, {numeric:true, sensitivity:"base"});
const esc = value => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const mediaUrl = (origin, file) => api.apiURL(`/aiden_h3/media/${origin}/${file.split("/").map(encodeURIComponent).join("/")}`);
const chineseLabels={index:"段号",multiple:"对齐倍数",media:"媒体",positive_prompt:"正向提示词",negative_prompt:"反向提示词",round_image:"本段图像",round_audio:"本段音频",round_video:"本段视频",source_latent:"源视频编码",latent:"音视频潜变量",queue:"分段清单",queue_state:"素材清单",video_queue:"(已弃用)视频分段清单",video_queue_state:"视频分段清单（JSON）",audio_oasis_ui:"音频预览",session_id:"进度名称",fps:"帧率",run_token:"执行标识",overlap_frames:"重叠帧数",guard_frames:"保护帧数",aspect_ratio:"宽高比",megapixels:"百万像素",reanchor_every:"重锚定间隔（段）",max_frames:"单段生成帧数上限",context:"当前段上下文",audio:"本段音频",guide_audio:"含重叠前导音频",prompt_index:"索引号",multiline:"多行模式",seconds_float:"时长（小数秒）",seconds_int:"时长（整数秒）",generation_frames:"本段帧数",start_frame:"起始帧（源）",target_frames:"目标帧数",filename:"素材文件名",video_frames:"视频画面",mask:"遮罩",segment:"覆盖段号（0为全局）",mode:"参考模式",previous:"上一级参考",references:"参考设置",frames:"历史画面帧数",previous_tail:"前段保真尾帧",best_handoff:"最佳交接参考帧",has_history:"存在前段历史",video:"视频",strict_sync:"严格音画同步",prompt_text:"实际提示词",video_oasis_ui:"视频预览",strings:"分段提示词",select:"生成段号",global_prefix:"统一提示词前缀",global_suffix:"统一提示词后缀",prompt:"提示词",width:"画面宽度",height:"画面高度"};
const chineseTitles={AidenH3EditMask:"角色替换遮罩适配",AidenH3AudioQueue:"素材准备",AidenH3VideoQueue:"视频分段清单",AidenH3Segment:"分段生成",AidenH3SegmentMedia:"视频与遮罩取段",AidenH3References:"全局与本段参考",AidenH3PickReferences:"当前段参考",AidenH3PreviousFrames:"前段末尾画面",AidenH3Commit:"记录分段结果",AidenH3VideoPreview:"分段预览",AidenH3Prompt:"分段提示词",AidenH3Resolution:"分辨率",AidenH3MergedVideo:"读取合并结果",AidenH3ContinuumStatus:"续段检测",AidenH3ContinuumPrepare:"续段准备",AidenH3ContinuumCapture:"续段存档",AidenH3ContinuumRecall:"续段调取",};

function adEnsureWidgetOptions(widget){
    if(!widget) return null;
    if(!widget.options || typeof widget.options!=="object") widget.options={};
    return widget.options;
}
function adSetWidgetReadOnly(widget, value){
    const options=adEnsureWidgetOptions(widget);
    if(!options) return;
    options.read_only=!!value;
    if("readOnly" in options || options.readOnly!==undefined) options.readOnly=!!value;
}

const AD_INITIAL_SIZES = {AidenH3AudioQueue: [1120, 1411], AidenH3Segment: [350, 0], AidenH3VideoPreview: [760, 785]};
function applyInitialNodeSize(node) {
    const target = AD_INITIAL_SIZES[node?.comfyClass];
    if (!target || !node.graph) return;
    if (node._adH3Configured) return; // 从工作流加载的节点保留用户尺寸
    const width = Number(node.size?.[0]) || 0;
    const height = Number(node.size?.[1]) || 0;
    // 目标高度为 0：按内容收高（新建分段生成节点不再写死高度、不留大块空白）
    if (target[1] <= 0) {
        let fit = null;
        try { fit = node.computeSize?.(); } catch {}
        const needed = fit && Number.isFinite(fit[1]) ? Math.max(240, Math.round(fit[1])) : height;
        const nextWidth = Math.max(width, target[0]);
        if (Math.abs(width - nextWidth) > 0.5 || Math.abs(height - needed) > 1) node.setSize([nextWidth, needed]);
        try { node._adRenderResolution?.(); } catch {}
        node.setDirtyCanvas?.(true, true);
        return;
    }
    if (width >= target[0] - 1) return;
    const nextHeight = Math.max(target[1], height);
    node.setSize([target[0], nextHeight]);
    try { node._adH3ApplyDynamicOutputs?.(); } catch {}
    try { node._adRenderResolution?.(); } catch {}
    node.setDirtyCanvas?.(true, true);
}
function localizeNode(node){
    if(!chineseTitles[node.comfyClass])return;
    const category=["AidenH3AudioQueue","AidenH3Segment","AidenH3VideoPreview"].includes(node.comfyClass)?"AD-Segmented-Loop":"AD-Segmented-Loop/兼容节点（旧工作流）";
    node.category=category;node.constructor.category=category;
    node.color="#414957";node.bgcolor="#2b303b";node.boxcolor="#6b8ec6";
    node.constructor.title_text_color="#eef3fa";
    if(true)node.title=`AD-${chineseTitles[node.comfyClass]}`;
    const extras={source_fps:"源视频帧率",source_video:"源视频",segment_video:"本段视频",original_audio:"源音频",model:"模型",conditioning:"条件",continuity:"续段强度",extend_frames:"新增帧数",audio_continuity:"保持音频连续",first_frame_policy:"首帧处理",preserve_last_frame:"保留末帧",strict_compatibility:"严格兼容检查",debug:"调试信息",plan:"续段计划",report:"状态说明",available:"组件可用",used_continuum:"已使用续段",samples:"采样结果",images:"视频画面",state:"续段状态",path:"状态文件",previous_state:"前段状态",has_state:"存在前段状态",h3_editor_state:"编辑状态"};
    const display=name=>extras[name]||chineseLabels[name]||(/^ref_image_\d+$/.test(name)?`参考图 ${Number(name.split("_").at(-1))+1}`:/^ref_video_audio_\d+$/.test(name)?`参考视频音频 ${Number(name.split("_").at(-1))+1}`:/^ref_video_\d+$/.test(name)?`参考视频 ${Number(name.split("_").at(-1))+1}`:/^ref_audio_\d+$/.test(name)?`参考音频 ${Number(name.split("_").at(-1))+1}`:/^audio\d+$/.test(name)?`音频${name.slice(5)}`:/^seconds_float\d+$/.test(name)?`时长${name.slice(13)}（小数秒）`:/^seconds_int\d+$/.test(name)?`时长${name.slice(11)}（整数秒）`:/^image\d+$/.test(name)?`参考图${name.slice(5)}`:name);
    // 旧工作流里 H3 参考节点的 ref_video 槽位仍记成 VIDEO；按最新定义纠正为 IMAGE，否则连不上
    if(node.comfyClass==="MiniMaxH3ReferenceToVideo"){
        for(const slot of node.inputs||[]){
            const name=String(slot.name||"").split(".").pop();
            if(/^ref_(image|video)_\d+$/.test(name)) slot.type="IMAGE";
            else if(/^ref_(video_audio|audio)_\d+$/.test(name)) slot.type="AUDIO";
        }
    }
    if(node.comfyClass==="AidenH3Segment"&&!node._adSegSizeFix){
        node._adSegSizeFix=true;
        const previousConnections=node.onConnectionsChange;
        node.onConnectionsChange=function(){
            const out=previousConnections?.apply(this,arguments);
            try{
                const need=Math.max(this.size?.[1]||0,this.computeSize?.()[1]||0);
                if(need>((this.size?.[1])||0)) this.setSize([this.size[0],need]);
            }catch(error){}
            return out;
        };
    }
    for(const slot of [...node.inputs||[],...node.outputs||[]]){slot.label=display(slot.name);slot.localized_name=slot.label;}
    for(const widget of node.widgets||[]){adEnsureWidgetOptions(widget);widget.label=display(widget.name);widget.localized_name=widget.label;}
    // 宽高比下拉值中文化：界面显示中文，提交时还原后端识别的英文值。
    const ratioLabels={"1:1 (Square)":"1:1 方形","16:9 (Landscape)":"16:9 横屏","9:16 (Portrait)":"9:16 竖屏","4:3":"4:3 标准","3:4":"3:4 竖版","3:2":"3:2 经典","2:3":"2:3 竖版","21:9":"21:9 超宽"};
    const ratioReverse=Object.fromEntries(Object.entries(ratioLabels).map(([key,value])=>[value,key]));
    const ratioWidget=node.widgets?.find(widget=>widget.name==="aspect_ratio");
    if(ratioWidget){
        if(!ratioWidget._adChinese){
            ratioWidget._adChinese=true;
            adEnsureWidgetOptions(ratioWidget).values=Object.values(ratioLabels);
            ratioWidget.serializeValue=()=>ratioReverse[ratioWidget.value]||ratioWidget.value;
        }
        ratioWidget.value=ratioLabels[ratioWidget.value]||ratioWidget.value;
    }
    const mode=node.comfyClass==="AidenH3References"&&node.widgets?.find(widget=>widget.name==="mode");
    if(mode&&!mode._adChinese){
        const labels={inherit:"跟随全局",custom:"本段自定义",none:"本段不使用"};
        mode._adChinese=true;adEnsureWidgetOptions(mode).values=Object.values(labels);
        mode.serializeValue=()=>Object.entries(labels).find(([,value])=>value===mode.value)?.[0]||mode.value;
        mode.value=labels[mode.value]||mode.value;
    }
    if(mode){const labels={inherit:"跟随全局",custom:"本段自定义",none:"本段不使用"};mode.value=labels[mode.value]||mode.value;}
    if(node.comfyClass==="AidenH3ContinuumPrepare"){
        const cont=node.widgets?.find(w=>w.name==="continuity");
        if(cont&&!cont._adChinese){
            const labels={
                "Balanced — 22 frames":"均衡 · 22 帧",
                "Fast — 5 frames":"快速 · 5 帧",
                "Strong — 39 frames (Experimental)":"强力 · 39 帧（实验）",
            };
            cont._adChinese=true;
            adEnsureWidgetOptions(cont).values=Object.values(labels);
            cont.serializeValue=()=>labels[cont.value]||cont.value;
            cont.value=labels[cont.value]||cont.value;
        }
        const pol=node.widgets?.find(w=>w.name==="first_frame_policy");
        if(pol&&!pol._adChinese){
            const labels={
                "Use Continuum context; keep visual identity tokens (Recommended)":"使用 Continuum 上下文并保留视觉身份（推荐）",
                "Error if a first-frame keyframe exists":"若存在首帧关键帧则报错",
                "Keep first-frame keyframe too (Advanced)":"同时保留首帧关键帧（高级）",
            };
            pol._adChinese=true;
            adEnsureWidgetOptions(pol).values=Object.values(labels);
            pol.serializeValue=()=>labels[pol.value]||pol.value;
            pol.value=labels[pol.value]||pol.value;
        }
    }
}

async function request(path, body) {
    const response = await api.fetchApi(path, body === undefined ? {} : {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
}

function hideWidget(node, name) {
    const at = node.widgets?.findIndex(widget => widget.name === name);
    if (at >= 0) node.widgets.splice(at, 1);
}

function upstreamSegment(viewer) {
    const graph=viewer.graph;if(!graph)return null;
    // 新布局：直接连「当前段上下文」；旧布局兼容 视频→记录分段结果→context
    const bound=viewer.properties?.ad_execution_round;
    const saved=bound?.id && graph.getNodeById(Number(bound.segment_node));
    if(saved?.comfyClass==="AidenH3Segment")return saved;
    const direct=graph.links?.get?.(viewer.inputs?.find(input=>input.name==="context")?.link)||graph.links?.[viewer.inputs?.find(input=>input.name==="context")?.link];
    const directNode=direct && graph.getNodeById(direct.origin_id);
    if(directNode?.comfyClass==="AidenH3Segment") return directNode;
    const link=graph.links?.get?.(viewer.inputs?.find(input=>input.name==="video")?.link)||graph.links?.[viewer.inputs?.find(input=>input.name==="video")?.link];
    const commit=link && graph.getNodeById(link.origin_id);
    if(commit?.comfyClass!=="AidenH3Commit")return null;
    const context=graph.links?.get?.(commit.inputs?.find(input=>input.name==="context")?.link)||graph.links?.[commit.inputs?.find(input=>input.name==="context")?.link];
    const segment=context && graph.getNodeById(context.origin_id);
    return segment?.comfyClass==="AidenH3Segment" ? segment : null;
}

function runSelectedSegments(segment, options = {}) {
    const viewer = (segment.graph?._nodes || []).find(candidate =>
        candidate.comfyClass === "AidenH3VideoPreview" && upstreamSegment(candidate) === segment);
    if (!viewer?._adH3RunAffected) return Promise.resolve(false);
    return viewer._adH3RunAffected(options);
}

function followRunningSegment(viewer, state) {
    if (!["queued", "running"].includes(state.status)) { viewer._adH3FollowedSegment = ""; return; }
    const index = Number(state.execution?.index ?? state.cursor), row = state.segments?.[index];
    const identity = `${state.id}:${row?.segment_key || index}`;
    if (!row || viewer._adH3FollowedSegment === identity) return;
    const segment = upstreamSegment(viewer), graph = segment?.graph;
    const input = segment?.inputs?.find(item => item.name === "queue");
    const link = graph?.links?.get?.(input?.link) || graph?.links?.[input?.link];
    const queue = link && graph?.getNodeById?.(link.origin_id);
    const seconds = Number(row.entry?.source_start);
    if (!queue?._adH3FollowSegment || !Number.isFinite(seconds)) return;
    queue._adH3FollowSegment(seconds);
    viewer._adH3FollowedSegment = identity;
}

function mountReview(node) {
    if(node._aidenH3Review)return;
    node._aidenH3Review=true;
    // 「循环控制」面板按用户要求移除：不再创建/挂载 DOM 控件，仅保留后台状态机（刷新/批量/提示）。
    const root=document.createElement("div");root.className="aiden-h3";
    root.innerHTML=`<div class="h3-status"></div><div class="h3-message"></div><div class="h3-list"></div>`;
    const status=root.querySelector(".h3-status"),message=root.querySelector(".h3-message"),list=root.querySelector(".h3-list");
    const compare=document.createElement("div");compare.className="h3-compare";compare.hidden=true;
    let disposed=false,busy=false,lastState=null,lastSession="",lastRendered="",connectionFailed=false,lastETag="";
    let refreshing=false,lastPolled=0,lastPayload="";
    node._adH3FollowCurrent = () => lastState && followRunningSegment(node, {...lastState, status: "queued"});
    const say=(text,error=false)=>{message.textContent=text;message.classList.toggle("error",error);try{node._adH3Hint?.(text,error);}catch{}};
    function sessionId(segment){
        if(segment?.properties?.ad_task_session)return segment.properties.ad_task_session;
        const frozen = segment?.properties?.ad_execution_round;
        if (frozen?.session_id && frozen.id) return frozen.session_id;
        const direct=segment?.widgets?.find(widget=>widget.name==="session_id")?.value;
        if(direct) return direct;
        // 新布局：当前段不再带 session_id 控件，从上游素材清单库的状态里取
        try{
            const graph=segment?.graph;
            const link=graph?.links?.get?.(segment?.inputs?.find(input=>input.name==="queue")?.link)||graph?.links?.[segment?.inputs?.find(input=>input.name==="queue")?.link];
            const queueNode=link&&graph.getNodeById(link.origin_id);
            const widget=queueNode?.widgets?.find(item=>item.name==="queue_state");
            const raw=typeof widget?.getValue==="function"?widget.getValue():widget?.value;
            const state=typeof raw==="string"?JSON.parse(raw):raw;
            return (state&&state.session_id)||(segment?`AD-H3-${segment.id}`:"AD-H3-session");
        }catch{return segment?`AD-H3-${segment.id}`:"AD-H3-session";}
    }
    function draw(state) {
        lastState=state;
        node.properties ||= {};
        if(state.execution_round) node.properties.ad_execution_round={...state.execution_round,session_id:state.id};
        else delete node.properties.ad_execution_round;
        followRunningSegment(node, state);
        const segment = upstreamSegment(node), graph = segment?.graph;
        const input = segment?.inputs?.find(item => item.name === "queue");
        const link = graph?.links?.get?.(input?.link) || graph?.links?.[input?.link];
        updateSessionProgress(node, segment, link && graph?.getNodeById?.(link.origin_id), state);
        node._adH3Preview?.updateState(state);
        const shownIndex=Math.min(Math.max(Number(state.cursor)||0,0),Math.max(0,state.segments.length-1));
        status.textContent=`第 ${shownIndex+1}/${state.segments.length} 段 · ${label[state.status]||state.status}`;
        if(state.error)say(state.error,true);
        const running=["queued","running","merging"].includes(state.status);
        const row=state.segments[state.cursor];
        const canMerge=state.segments.every(item=>item.valid&&item.accepted);
        const enabled={start:!running&&!row?.valid,
            redo:!running,auto:!running,merge:!running&&canMerge,clear:!running,
            run_segment:!running,run_pending:!running};
        for(const button of root.querySelectorAll("[data-command]"))button.disabled=busy||!enabled[button.dataset.command];
        const renderKey=JSON.stringify([state.segments,state.cursor,state.merged,state.manifest,state.merge_options,running]);
        if(renderKey===lastRendered)return;
        lastRendered=renderKey;
        let cumulative=0;
        list.innerHTML=state.segments.map((row,i)=>{
            const version=row.versions.find(version=>version.id===row.selected);
            const text=row.valid?(row.accepted?"已生成":"生成中"):(row.versions.length?"待更新":"未生成");
            const source=version?`<a href="${esc(mediaUrl("session",`${state.id}/${version.file}`))}" target="_blank" rel="noopener">播放</a>`:"";
            const options=row.versions.filter(version=>!version.archived).map((version,j)=>`<option value="${esc(version.id)}" ${version.id===row.selected?"selected":""}>版本 ${j+1}</option>`).join("");
            cumulative+=version?.sync_error_ms||0;
            const sync=version?`<div class="h3-scores">${version.frames} 帧 · 本段偏差 ${(version.sync_error_ms||0).toFixed(3)} ms · 累计 ${cumulative.toFixed(3)} ms${version.reanchor?" · 原始参考重锚定":""}${version.reference_transition?" · 参考集已切换，请检查场景衔接":""}</div>`:"";
            return `<div class="h3-review-row ${i===state.cursor?"h3-current":""}"><span>第 ${i+1} 段 · ${text}</span>${source}${row.versions.length?`<select data-version="${i}" aria-label="第 ${i+1} 段版本">${!row.selected?'<option value="">选择版本</option>':""}${options}</select>`:""}${row.versions.length>1?`<button data-compare="${i}">A/B 对比</button>`:""}${sync}</div>`;
        }).join("");
        if(state.merged)list.insertAdjacentHTML("beforeend",`<div class="h3-review-row"><a href="${esc(mediaUrl("session",`${state.id}/${state.merged}`))}" target="_blank" rel="noopener">打开最终合并视频</a></div>`);
        if(state.segments.every(row=>row.valid&&row.accepted))list.insertAdjacentHTML("beforeend",`<a href="${esc(api.apiURL(`/aiden_h3/export/${encodeURIComponent(state.id)}`))}" download>导出分段清单</a>`);
        list.querySelectorAll("[data-version]").forEach(select=>{select.disabled=running;select.onchange=()=>void act("select",{index:Number(select.dataset.version),version:select.value});});
        list.querySelectorAll('a[href]:not([download])').forEach(link=>link.onclick=event=>{
            event.preventDefault();node._adH3Preview?.show({url:link.href,label:link.textContent,kind:"review"});
        });
        list.querySelectorAll("[data-compare]").forEach(button=>button.onclick=()=>showCompare(Number(button.dataset.compare)));
    }
    function showCompare(index){
        compare.hidden=false;
        const row=lastState.segments[index];
        const selected=row.versions.findIndex(v=>v.id===row.selected);
        compare.innerHTML=[Math.max(0,selected-1),Math.max(0,selected)].map((value,side)=>`<figure><figcaption>${side?"B":"A"} · 第 ${index+1} 段</figcaption><select aria-label="${side?"B":"A"} 版本">${row.versions.map((v,j)=>`<option value="${j}" ${j===value?"selected":""}>版本 ${j+1}${v.id===row.selected?"（当前选用）":""}</option>`).join("")}</select><button>查看 ${side?"B":"A"}</button></figure>`).join("");
        compare.querySelectorAll("figure").forEach((figure,side)=>{
            const select=figure.querySelector("select");
            figure.querySelector("button").onclick=()=>node._adH3Preview?.show({
                url:mediaUrl("session",`${lastState.id}/${row.versions[Number(select.value)].file}`),
                label:`${side?"B":"A"} · 第 ${index+1} 段 · 版本 ${Number(select.value)+1}`,kind:"compare"});
        });
        const close=document.createElement("button");close.textContent="关闭对比";close.onclick=()=>{compare.replaceChildren();compare.hidden=true;};compare.append(close);
        say("选择 A/B 版本后在同一播放器切换查看；不会改变当前选用版本。");
    }
    node._adH3BatchStop = false;
    let actionRevision=0;
    async function act(command,extra={}) {
        if(command==="stop") {
            node._adH3BatchStop=true;node._adH3StopEpoch=(Number(node._adH3StopEpoch)||0)+1;
            try { await interruptOwnedPrompt(api,node); } catch(error) { say(error.message,true); }
        }
        if(node._adH3AffectedRunning && command!=="stop" && !(command==="redo_preview" && extra.draft_target)) {say("「运行此段」还在进行，请等待完成或先点「停止」。",true);return false;}
        if(busy && command !== "stop") {node._adH3LastActionError="上一项操作尚未结束，请稍后重试。";say(node._adH3LastActionError,true);return false;}
        const segment=upstreamSegment(node);
        if(!segment){say("请先连接当前段上下文。",true);return false;}
        if(command==="clear"&&!extra.skipConfirm&&!window.confirm("清空全部生成进度和选用记录？分段、提示词、参考素材和已生成文件都会保留。"))return;
        if(command==="approve" && !segment.inputs?.find(input=>input.name==="queue")?.link){
            // 独立模式没有分段清单/会话：仅本地标记本次结果通过，不推进分段
            node._adH3StandaloneReady=false;
            say("独立模式没有会话进度，本次结果无法标记为已通过（仅保留预览）。");
            void refresh();
            return true;
        }
        const operation=++actionRevision;
        let previewId="";
        const approvedIndex=Number(lastState?.cursor??0)+1;
        node._adH3LastActionError="";
        busy=true;say("正在处理…");
        try{
            const serialized=await app.graphToPrompt();
            if(operation!==actionRevision)return false;
            let result=null;
            let effective=command;
            try{
                // 停止后改了种子：点开始应使用新种子重跑当前段，而不是接受旧结果继续。
                if(command==="start" && lastState?.status==="paused"){
                    const row=lastState.segments?.[lastState.cursor];
                    const version=(row?.versions||[]).find(v=>v.id===row?.selected);
                    if(row?.valid && row?.selected && version?.seeds){
                        const norm=obj=>Object.fromEntries(Object.entries(obj||{}).filter(([,v])=>v&&typeof v==="object")
                            .map(([k,v])=>[k,Object.fromEntries(Object.entries(v).map(([a,b])=>[a,String(b)]))]));
                        const now={};
                        for(const [nid,nd] of Object.entries(serialized.output||{})){
                            const inputs=nd?.inputs||{},seeds={};
                            for(const k of ["seed","noise_seed"])if(k in inputs)seeds[k]=inputs[k];
                            if(Object.keys(seeds).length)now[nid]=seeds;
                        }
                        if(JSON.stringify(norm(now))!==JSON.stringify(norm(version.seeds))){
                            effective="redo";say("检测到种子已修改，正在用新种子重跑当前段…");
                        }else{
                            effective="approve";
                        }
                    }
                }
                result=await request("/aiden_h3/action",{command:effective,session_id:sessionId(segment),segment_node:String(segment.id),
                    prompt:serialized.output,workflow:serialized.workflow,client_id:api.clientId,confirmed:command==="clear",auto_accept:true,...extra});
                if(operation!==actionRevision)return false;
                if (command === "clear" || command === "new_group") {
                    if(segment.properties) delete segment.properties.ad_execution_round;
                    if(node.properties) delete node.properties.ad_execution_round;
                    if(command === "new_group")node._adH3Preview?.resetGroupSelection?.();
                }
                previewId=result?.preview_hold?.request_id||"";
                command=effective;
                draw(result);
            }catch(error){
                // 独立模式没有会话记录：重做/开始时直接重新排队当前轮
                const queueLinked=segment.inputs?.find(input=>input.name==="queue")?.link;
                if(queueLinked || !["redo","redo_preview","start"].includes(command)) throw error;
            }
            const messages={stop:"已停止：当前生成已中断，分段循环待命；可点任意预览后「本段抽卡」，抽完点「开始」续接分段循环。",
                new_group:"已新建一组，旧成果保留；点开始或分段循环生成。",clear:"已重置本轮进度；组号、预览与已生成文件保留。",redo:"已重置当前段，正在重新抽卡…",redo_preview:"正在本段抽卡（新结果将直接作为该段当前选用）…",
                start:"已开始生成当前段…",approve:"已接受当前段。",auto:"已开始分段循环。",merge:"合并操作已完成。",
                run_segment:"已切换到指定段：点「开始」只生成这一段，通过后自动回到原进度位置。",
                run_pending:"已定位到待生成/待更新段：点「开始」只生成这一段，通过后自动回到原进度位置。"};
            say(command==="redo_preview" && result?.preview_hold?.select_on_success
                ? "正在重抽停止段；成功后直接作为该段当前选用，不自动跳段…" : (messages[command] || "操作已完成。"));
            if(command==="redo_preview"){
                const stopEpoch=Number(node._adH3StopEpoch)||0;
                const queued=await executeQueueOnce(app,api,segment.id,{isStopped:()=>(Number(node._adH3StopEpoch)||0)!==stopEpoch,owner:node});
                if(operation!==actionRevision)return false;
                if(queued.stopped){node._adH3ActionCancelled=true;say("本段抽卡已停止，原循环位置已恢复。");return false;}
                for (let attempt=0;attempt<40;attempt++) {
                    const response=await api.fetchApi("/aiden_h3/session/"+encodeURIComponent(sessionId(segment))+"?t="+Date.now(),{cache:"no-store"});
                    const state=await response.json();
                    if (!response.ok) throw new Error(state.error || "读取试跑结果失败");
                    draw(state);
                    if (!state.draft_trial) {result=state;break;}
                    await new Promise(resolve=>setTimeout(resolve,100));
                }
                if(result?.draft_trial) throw new Error("试跑已结束，等待结果登记，请稍后再继续");
                say("本段抽卡完成：新结果已作为该段当前选用；点「开始」继续原循环。");
            }else if(command==="redo"||command==="start"){
                // 含“记录分段结果”的工作流由后台 action start 直接排队；此时前端不能再排一次，
                // 否则第二次提交会被 round/prepare 拒绝并显示红色报错（后台其实已在生成）。
                if(!result?.execution?.prompt_id){
                    if(typeof node._adH3Run==="function") {
                        if(await node._adH3Run()===false)throw new Error("工作流未能加入队列：请先确认当前段没有在运行，并检查节点输入是否完整；详细原因见 ComfyUI 运行记录。");
                    }else if(app.queuePrompt) await app.queuePrompt(0,1);
                    else throw new Error("找不到工作流排队入口");
                }
            }
            if(command==="approve" && result){
                const cursor=Number(result.cursor)||0;
                const total=(result.segments||[]).length;
                const approved=approvedIndex;
                if(result.status==="review")say(`已接受第 ${approved} 段；第 ${cursor+1} 段已有生成结果。`);
                else if(result.status!=="completed" && cursor<total){
                    say(`已接受第 ${approved} 段，正在生成第 ${cursor + 1} 段…`);
                    // 后台已按执行快照排队时不再让前端重复提交，避免双提交报错。
                    if(!result?.execution?.prompt_id){
                        if(typeof node._adH3Run==="function") node._adH3Run();
                        else try{ if(app.queuePrompt) await app.queuePrompt(0,1); }catch{}
                    }
                }else{
                    say("全部段已接受完成。");
                }
            }
        return result || true;
        }catch(error){
            if(operation!==actionRevision)return false;
            const detail=error?.message||String(error);
            node._adH3LastActionError=detail;
            if(command==="redo_preview"){
                if(previewId)try{await request("/aiden_h3/action",{command:"cancel_preview",session_id:sessionId(segment),reason:detail,preview_id:previewId});}catch{}
                if(operation!==actionRevision)return false;
                say("本段抽卡未成功排队或执行失败："+detail,true);
            }else say(detail,true);
            return false;
        }finally{if(operation===actionRevision){busy=false;void refresh();}}
    }
    root.querySelectorAll("[data-command]").forEach(button=>button.onclick=()=>button.dataset.command==="auto"?void node._adH3Batch({merge:false}):void act(button.dataset.command));
    node._adH3Act = act;
    node._adH3QueueOwned = async () => {
        const segment = upstreamSegment(node);
        if (!segment) return false;
        const epoch = Number(node._adH3StopEpoch) || 0;
        await executeQueueOnce(app, api, segment.id, {owner:node, waitForCompletion:false,
            isStopped:()=>(Number(node._adH3StopEpoch)||0)!==epoch});
        return true;
    };

    async function batchRun({resumeSelection=true}={}) {
        const segment=upstreamSegment(node);
        if(!segment) throw new Error("请先连接当前段上下文");
        node._adH3BatchStop=false;
        const stopped=()=>!!node._adH3BatchStop;
        // 后台继续运行：循环只依赖启动时序列化的队列副本和已捕获的节点，不依赖当前激活画布。
        // 切到其它工作流标签后旧图 DOM/连线可能不可用，界面刷新失败不能中断生成。
        const safeDraw = state => { try { if(node.graph?.getNodeById?.(node.id)===node)draw(state); } catch {} };
        const queueLinked=!!segment.inputs?.find(item=>item.name==="queue")?.link || !!segment.properties?.ad_execution_round?.id;
        if(!queueLinked) {
            const widget=segment.widgets?.find(item=>item.name==="正向提示词");
            const text=String(widget?.value||"");
            if(!text.trim())throw new Error("请先填写正向提示词再开始分段循环");
            const parts=text.split(/^[ \t]*#[ \t]*/m);
            const count=Math.max(1,(parts.length>1?parts.slice(1):parts).filter(part=>part.trim()).length);
            const index=segment.widgets?.find(item=>item.name==="段索引");
            const serialized=await app.graphToPrompt();
            for(let i=0;i<count;i++) {
                if(stopped()) return;
                if(index) index.value=i;
                say("正在生成第 "+(i+1)+"/"+count+" 段…");
                let submission=serialized;
                if(app.graph===node.graph && segment.graph===node.graph && app.graph.getNodeById?.(segment.id)===segment) submission=await app.graphToPrompt();
                // 切页仍更新自己的提交副本，不触碰另一张画布。
                submission=structuredClone(submission);
                if(submission.output?.[String(segment.id)]) submission.output[String(segment.id)].inputs["段索引"]=i;
                const result=await executeQueueOnce(app,api,segment.id,{isStopped:stopped,serialized:submission,owner:node});
                if(result.stopped) return;
            }
            say("独立模式分段循环完成，共 "+count+" 段。");return;
        }
        const serialized=await app.graphToPrompt();
        // 切页后保留本任务最近提交副本；回到原图时再读取最新编辑。
        let roundSubmission=serialized;
        const batchTask=serialized.workflow?.extra?.ad_segmented_task?.id;
        // 分段循环续跑：报错后按最新编辑重冻结并从报错段继续；清空重置后从选中预览卡之后继续。
        let currentState=null;
        try {
            const response=await api.fetchApi("/aiden_h3/session/"+encodeURIComponent(sessionId(segment))+"?t="+Date.now(),{cache:"no-store"});
            if(response.ok) currentState=await response.json();
        } catch {}
        const selectedPreview=resumeSelection ? node._adH3Preview?.resumeTarget?.() : null;
        const selectedSegment=resumeSelection ? Number(selectedPreview?.segment_index || (typeof node._adH3Preview?.resumeTarget !== "function" ? node._adH3Preview?.selectedSegment?.() : 0) || 0) : 0;
        let state;
        if(currentState && selectedSegment > 0){
            state=await request("/aiden_h3/action",{command:"resume_after",session_id:currentState.id,index:selectedSegment,preview:selectedPreview,group_previews:node._adH3Preview?.groupRecords?.(selectedPreview),segment_node:String(segment.id),prompt:serialized.output,workflow:serialized.workflow,client_id:api.clientId,frontend_queue:true,auto_accept:true});
            node._adH3Preview?.consumeResumeTarget?.();
            safeDraw(state);
            say(state.status==="completed" ? "所选为本轮最后一段，本轮已完成，没有后续段。" : "已从选中的第 "+state.cursor+" 段之后继续；本轮素材与前段结果保留。");
        } else if(currentState?.status==="error"){
            state=await request("/aiden_h3/action",{command:"resume_round",session_id:currentState.id,segment_node:String(segment.id),prompt:serialized.output,workflow:serialized.workflow,client_id:api.clientId,frontend_queue:true,auto_accept:true});
            safeDraw(state);
            say("已按当前编辑重新生成，从报错段继续…");
        } else {
            state=await request("/aiden_h3/sync",{segment_node:String(segment.id),prompt:serialized.output,workflow:serialized.workflow,client_id:api.clientId,round_session:sessionId(segment),freeze_round:true,auto_accept:true});
        }
        const finish=async({restart=false}={})=>{
            const batch=state.segments.some(row=>row.entry?.batch_source);
            try {
                state=await request("/aiden_h3/action",{command:"clear",session_id:state.id,confirmed:true});
                safeDraw(state);
                say(batch
                    ? "原文件批处理完成（若保存失败，预览卡片会提示）；本轮已自动清空并重置，点「开始」继续新一轮。"
                    : "整轮分段循环已完成，已自动清空重置；点「开始」或「分段循环」开始新一轮。");
                return restart ? state : null;
            } catch (error) {
                say((batch ? "原文件批处理完成；" : "整轮分段循环已完成；") + "自动清空失败：" + error.message, true);
                return null;
            }
        };
        const waitIdle=async()=>{
            for(let attempt=0;attempt<3600;attempt++){
                if(stopped()) return false;
                if(!["queued","running","merging"].includes(state.status)) return true;
                await new Promise(resolve=>setTimeout(resolve,500));
                const response=await api.fetchApi("/aiden_h3/session/"+encodeURIComponent(state.id),{cache:"no-store"});
                state=await response.json();
                if(!response.ok) throw new Error(state.error||"读取进度失败");
                safeDraw(state);
            }
            throw new Error("等待当前任务完成超时（可能仍在长时间生成或队列阻塞）：请先点「停止」，再到 ComfyUI 队列与运行记录确认状态。");
        };
        if(!(await waitIdle())) return;
        if(state.status==="error") throw new Error(state.error||"当前任务失败，请检查后重试");
        // 选中末段的续跑已经结束；不能进入普通“已完成后再次开始”的重开分支。
        if(selectedSegment>0&&state.status==="completed")return;
        if(state.status==="completed"){
            // 点「分段循环」时若上一轮已完成：先自动重置，再从第1段开始新一轮。
            const cleared=await finish({restart:true});
            if(!cleared) return;
            state=cleared;
        }
        for(let guard=0;guard<state.segments.length+2;guard++) {
            if(stopped()) return;
            if(state.status==="completed"){await finish();return;}
            if(state.status==="error") throw new Error(state.error||"当前任务失败，请检查后重试");
            const beforeCursor=Number(state.cursor);
            const row=state.segments[beforeCursor];
            if(!row) break;
            if(!row.valid) {
                try { followRunningSegment(node, {...state, status: "queued"}); } catch {}
                say("正在生成第 "+(state.cursor+1)+"/"+state.segments.length+" 段…");
                // A 方案：同一画布内每段重新读取当前连线/参数，改线从下一段立即生效；切到别的工作流时仍用启动副本。
                let submission=roundSubmission;
                try { if(app.graph===node.graph && segment.graph===node.graph && app.graph.getNodeById?.(segment.id)===segment && (!batchTask||currentTaskId(app.graph)===batchTask)) {submission=await app.graphToPrompt();roundSubmission=structuredClone(submission);} } catch {}
                const result=await executeQueueOnce(app,api,segment.id,{isStopped:stopped,serialized:submission,owner:node});
                if(result.stopped) return;
                // 生成即通过：等待后台自动登记有效/通过并把游标前移，无需再点同意批准。
                let advanced=false;
                for(let attempt=0;attempt<160;attempt++){
                    const response=await api.fetchApi("/aiden_h3/session/"+encodeURIComponent(state.id),{cache:"no-store"});
                    state=await response.json();
                    if(!response.ok) throw new Error(state.error||"读取进度失败");
                    if(state.status==="error") throw new Error(state.error||"本次任务失败");
                    if(state.status==="completed" || Number(state.cursor)!==beforeCursor
                        || (state.segments?.[beforeCursor]?.valid && state.segments?.[beforeCursor]?.accepted)) {advanced=true;break;}
                    if(stopped()) return;
                    await new Promise(resolve=>setTimeout(resolve,250));
                }
                if(!advanced) throw new Error(state.error||"本次任务没有有效结果，未能继续下一段");
                if(state.status==="completed"){await finish();return;}
                continue;
            }
            if(!row.accepted){
                // 旧会话兼容：仅对待审核的有效结果自动接受一次；新会话生成即通过，不会走到这里。
                state=await request("/aiden_h3/action",{command:"approve",session_id:state.id});safeDraw(state);
                if(state.status==="completed"){await finish();return;}
                if(Number(state.cursor)===beforeCursor)
                    throw new Error(`进度没有自动前进（仍停在第 ${beforeCursor+1} 段）：请点「停止」后确认当前段状态，必要时重做该段。`);
                continue;
            }
            break;
        }
    }
    node._adH3Batch=async options=>{
        if(node._adH3AffectedRunning){say("「运行此段」还在进行，请等待完成或先点「停止」。",true);return;}
        if(node._adH3BatchRunning){say("分段循环正在进行，请先停止当前任务。",true);return;}
        node._adH3BatchRunning=true;
        try{await batchRun(options);}
        catch(error){say(error.message,true);}
        finally{node._adH3BatchRunning=false;if(node._adH3BatchStop)say("分段循环已停止，流程待命；点「开始」继续。");void refresh();}
    };
    node._adH3RunAffected = async (options = {}) => {
        if (node._adH3AffectedRunning) { say("正在重跑修改的段，请等待完成或先点停止。", true); return; }
        const segment = upstreamSegment(node);
        if (!segment) { say("请先连接当前段上下文。", true); return; }
        if (!segment.inputs?.find(item => item.name === "queue")?.link) { say("请先连接素材清单后再使用运行此段。", true); return; }
        const queueNodeOf = () => {
            const input = (segment.inputs || []).find(item => item.name === "queue");
            const link = input?.link != null ? (node.graph?.links?.[input.link] || node.graph?.links?.get?.(input.link)) : null;
            return link ? (node.graph?.getNodeById?.(link.origin_id) || null) : null;
        };
        const follow = seconds => { try { queueNodeOf()?._adH3FollowSegment?.(seconds, {remember: true}); } catch (_error) {} };
        const restore = () => { try { queueNodeOf()?._adH3FollowRestore?.(); } catch (_error) {} };
        node._adH3AffectedRunning = true;
        const stopEpoch = Number(node._adH3StopEpoch) || 0;
        const explicitlyStopped = () => (Number(node._adH3StopEpoch) || 0) !== stopEpoch;
        const resumeBatch = !!node._adH3BatchRunning;
        if (resumeBatch) node._adH3BatchStop = true;
        const stopped = () => explicitlyStopped() || !!node._adH3BatchStop;
        let succeeded = false;
        try {
            // Yield the sequential driver without interrupting its current ComfyUI task.
            for (let attempt = 0; node._adH3BatchRunning; attempt++) {
                if (explicitlyStopped()) return false;
                if (attempt >= 7200) throw new Error("等待分段循环让出任务超时：请先点「停止」，再到 ComfyUI 队列确认任务状态。");
                await new Promise(resolve => setTimeout(resolve, 250));
            }
            for (let attempt = 0; attempt < 7200; attempt++) {
                if (explicitlyStopped()) return false;
                const response = await api.fetchApi("/aiden_h3/session/" + encodeURIComponent(sessionId(segment)), {cache: "no-store"});
                if (response.status === 404) break;
                const state = await response.json();
                if (!response.ok) throw new Error(state.error || "读取进度失败");
                draw(state);
                if (!["queued", "running", "merging"].includes(state.status)) break;
                if (attempt === 7199) throw new Error("等待当前任务完成超时（可能仍在长时间生成或队列阻塞）：请先点「停止」，再到 ComfyUI 队列与运行记录确认状态。");
                say("等待当前任务结束后运行所选段…");
                await new Promise(resolve => setTimeout(resolve, 500));
            }
            if (explicitlyStopped()) return false;
            node._adH3BatchStop = false;
            const serialized = await app.graphToPrompt();
            if (stopped()) return false;
            let state = await request("/aiden_h3/sync", {segment_node: String(segment.id), prompt: serialized.output,
                workflow: serialized.workflow, client_id: api.clientId});
            if (stopped()) return false;
            draw(state);
            if (state.execution_round) {
                const indices = options.indices?.length ? [...new Set(options.indices)] : [state.cursor];
                for (const index of indices) {
                    if (stopped()) return false;
                    const result = await act("redo_preview", {index, draft_target:true});
                    if (result === false) return false;
                }
                succeeded = true;
                say("所选片段已生成；原循环进度保持不变。");
                return true;
            }
            state = await request("/aiden_h3/action", {command: "run_affected", session_id: state.id,
                ...(options.indices ? {indices: options.indices} : {})});
            if (stopped()) {
                await request("/aiden_h3/action", {command: "stop", session_id: state.id});
                return false;
            }
            draw(state);
            const total = Number(state.affected_run?.remaining?.length || 0);
            if (total) say(`检测到 ${total} 个需要重跑的段：自动逐个生成并完成，完成后回到原运行位置…`);
            const limit = total + 1;
            for (let guard = 0; guard < limit; guard++) {
                if (stopped()) return;
                for (let attempt = 0; attempt < 3600; attempt++) {
                    if (stopped()) return;
                    if (!["queued", "running", "merging"].includes(state.status)) break;
                    await new Promise(resolve => setTimeout(resolve, 500));
                    const response = await api.fetchApi("/aiden_h3/session/" + encodeURIComponent(state.id), {cache: "no-store"});
                    state = await response.json();
                    if (!response.ok) throw new Error(state.error || "读取进度失败");
                    draw(state);
                }
                if (state.status === "error") throw new Error(state.error || "本次任务失败，请检查后重试");
                if (!state.affected_run) break;
                const index = Number(state.cursor);
                const row = state.segments?.[index];
                if (!row) throw new Error("找不到要重跑的段");
                const start = Number(row.entry?.source_start);
                if (Number.isFinite(start)) follow(start);
                if (!row.valid) {
                    say(`正在重跑第 ${index + 1} 段（自动处理受影响段）…`);
                    const queued = await executeQueueOnce(app, api, segment.id, {isStopped: stopped,owner:node});
                    if (queued.stopped) return;
                    for (let attempt = 0; attempt < 120; attempt++) {
                        const response = await api.fetchApi("/aiden_h3/session/" + encodeURIComponent(state.id), {cache: "no-store"});
                        state = await response.json();
                        if (!response.ok) throw new Error(state.error || "读取进度失败");
                        if (state.status === "error") throw new Error(state.error || "本次任务失败");
                        if (state.segments?.[state.cursor]?.valid) break;
                        if (stopped()) return;
                        await new Promise(resolve => setTimeout(resolve, 250));
                    }
                    if (!state.segments?.[state.cursor]?.valid) throw new Error("本次任务没有产生有效结果，当前段未完成；请重跑该段或查看 ComfyUI 运行记录。");
                }
                if (stopped()) return false;
                state = await request("/aiden_h3/action", {command: "approve", session_id: state.id, frontend_queue: true});
                draw(state);
            }
            if (state.affected_run) throw new Error("所选段还没有全部完成：请先查看当前段状态，完成后再继续。");
            succeeded = true;
            say("指定的段已运行完成，已回到原运行位置。");
            return true;
        } catch (error) {
            say(error?.message || String(error), true);
            return false;
        } finally {
            node._adH3AffectedRunning = false;
            restore();
            void refresh();
            if (succeeded && resumeBatch && !explicitlyStopped()) void node._adH3Batch({merge: false});
        }
    };
    node._adH3ReviewStatus = say;
    node._adH3RefreshState = async () => {
        const segment=upstreamSegment(node);
        if(!segment) throw new Error("找不到本轮的分段生成节点");
        const response=await api.fetchApi("/aiden_h3/session/"+encodeURIComponent(sessionId(segment))+"?t="+Date.now(),{cache:"no-store"});
        const state=await response.json();
        if(!response.ok) throw new Error(state.error || "读取本轮进度失败");
        draw(state);
        return state;
    };
    async function refresh(force=true){
        const pollRevision=actionRevision;
        if(app.configuringGraph)return;
        try{if(node.graph===app.graph)await ensureWorkflowTask(node.graph);}catch(error){if(!disposed)say(error.message,true);return;}
        const pollTask=currentTaskId(node.graph);
        if(disposed || refreshing || pollRevision!==actionRevision || (!force && document.hidden))return;
        const interval=lastState && ["queued","running","merging"].includes(lastState.status)?2000:10000;
        if(!force && Date.now()-lastPolled<interval)return;
        const segment=upstreamSegment(node);const current=sessionId(segment);
        if(!segment){ status.textContent="连接「当前段上下文」后启用循环控制。"; for(const button of root.querySelectorAll("[data-command]")) button.disabled=true; return; }
        if(!current)return;
        if(current!==lastSession){lastETag="";lastSession=current;lastState=null;lastRendered="";list.textContent="";compare.querySelectorAll("video").forEach(video=>video.pause());compare.replaceChildren();compare.hidden=true;}
        refreshing=true;lastPolled=Date.now();
        try{
            const response=await api.fetchApi(`/aiden_h3/session/${encodeURIComponent(current)}`, {cache:"no-store",headers:lastETag ? {"If-None-Match":lastETag} : {}});
            if(disposed || pollRevision!==actionRevision || pollTask!==currentTaskId(node.graph) || current!==sessionId(segment)){lastPolled=0;return;}
            if(response.status===304){
                const payload=`${current}|${lastETag}|${busy}`;
                if(lastState && payload!==lastPayload){lastPayload=payload;draw(lastState);}
                return;
            }
            if(response.status===404){lastETag="";
                if(connectionFailed){say("");connectionFailed=false;}
                const standaloneReady=!!node._adH3StandaloneReady;
                status.textContent=standaloneReady?"独立模式 · 本次结果可用":"已连接 · 可以开始第一段";
                root.querySelectorAll("[data-command]").forEach(button=>{
                    const allowed=["start","auto"].includes(button.dataset.command)||(button.dataset.command==="approve"&&standaloneReady);
                    button.disabled=busy||!allowed;
                });
                return;
            }
            const state=await response.json();if(!response.ok)throw new Error(state.error);
            if(disposed || current!==lastSession || pollRevision!==actionRevision || pollTask!==currentTaskId(node.graph) || current!==sessionId(segment))return;
            if(Number(state.updated_at||0)<Number(lastState?.updated_at||0))return;
            if(connectionFailed){say("");connectionFailed=false;}
            lastETag=response.headers?.get?.("ETag") || "";
            const payload=lastETag ? `${current}|${lastETag}|${busy}` : JSON.stringify([current,state,busy]);
            if(payload!==lastPayload){lastPayload=payload;draw(state);}
        }catch(error){if(!disposed && pollRevision===actionRevision){connectionFailed=true;say(error.message,true);}}
        finally{refreshing=false;}
    }
    const timer=setInterval(()=>void refresh(false),2000);
    const onProgress=()=>void refresh();api.addEventListener("aiden-h3/progress",onProgress);
    const removed=node.onRemoved;node.onRemoved=function(){disposed=true;clearInterval(timer);api.removeEventListener("aiden-h3/progress",onProgress);removed?.apply(this,arguments);};
    void refresh();
}


function adPinSegmentWidgetOrder(node){
    if(!node?.widgets) return;
    // 固定控件顺序：宽高比 → 百万像素 → 对齐倍数 → 分辨率 → 正向提示词 → 反向提示词 → 段索引 → 统一提示词前缀（其余隐藏控件排最后）
    const order=["session_id","aspect_ratio","megapixels","multiple","ad_h3_resolution","ad_h3_prompt_editor","正向提示词",
        "ad_h3_negative_editor","反向提示词","段索引","ad_h3_prefix_editor","统一提示词前缀","ref_counts","reanchor_every","run_token",
        "fps","overlap_frames","guard_frames","max_frames"];
    const rank=name=>{const index=order.indexOf(name);return index<0?order.length:index;};
    const original=new Map(node.widgets.map((widget,index)=>[widget,index]));
    node.widgets=[...node.widgets].sort((a,b)=>rank(a.name)-rank(b.name)||original.get(a)-original.get(b));
}

function fitNodeToWidgets(node) {
    setTimeout(() => {
        try {
            if (node._adH3Configured) return; // 旧工作流节点保留用户保存的高度，不按内容强制收缩
            const size = node.computeSize?.();
            if (size) node.setSize([node.size[0], Math.max(240, Math.round(size[1]))]);
            node.setDirtyCanvas?.(true, true);
        } catch {}
    }, 0);
}

function normalizeAspectWidget(node) {
    const widget = node.widgets?.find(item => item.name === "aspect_ratio");
    if (!widget) return;
    const choose = node.widgets?.find(item => item.name === "aspect_ratio")?.options?.values;
    const valid = Array.isArray(choose) ? choose : [];
    const mapRatio = text => {
        const match = String(text || "").match(/(\d+)\s*[:：]\s*(\d+)/);
        if (!match) return null;
        const pair = `${match[1]}:${match[2]}`;
        return {"1:1": "1:1 (Square)", "16:9": "16:9 (Widescreen)", "9:16": "9:16 (Portrait)",
            "4:3": "4:3 (Standard)", "3:4": "3:4 (Portrait)", "3:2": "3:2 (Photo)",
            "2:3": "2:3 (Portrait)", "21:9": "21:9 (Cinematic)"}[pair] || null;
    };
    if (valid.length && !valid.includes(widget.value)) {
        const next = mapRatio(widget.value);
        if (next) widget.value = next;
    }
}

function normalizePreviewInputOrder(node) {
    const inputs = node.inputs || [];
    const context = inputs.find(input => input.name === "context");
    if (!context) return;
    node.inputs = [context, ...inputs.filter(input => input !== context)];
    // 输入对象和连线ID保持，按端口名迁移目标槽位，兼容旧工作流与Map链接表。
    node.inputs.forEach((input, index) => {
        if (input.link == null) return;
        const link = node.graph?.links?.get?.(input.link) || node.graph?.links?.[input.link];
        if (link && Number(link.target_id) === Number(node.id)) link.target_slot = index;
    });
}

const AD_SEG_FIXED = ["context", "index", "start_frame", "generation_frames", "fps", "width", "height",
    "positive_prompt", "negative_prompt", "round_image", "round_audio", "round_video", "source_video", "original_audio"];
const AD_SEG_REF_NAMES = [
    ...Array.from({length: 9}, (_, i) => `ref_image_${i}`),
    ...Array.from({length: 3}, (_, i) => `ref_video_${i}`),
    ...Array.from({length: 3}, (_, i) => `ref_video_audio_${i}`),
    ...Array.from({length: 3}, (_, i) => `ref_audio_${i}`),
];
const AD_SEG_EXTRA = ["mask_frames"];
const AD_SEG_OUTPUTS = [...AD_SEG_FIXED, ...AD_SEG_REF_NAMES, ...AD_SEG_EXTRA];
const AD_SEG_TYPES = [
    "H3_LOOP_CONTEXT", "INT", "IMAGE", "INT", "FLOAT", "INT", "INT", "STRING", "STRING", "IMAGE", "AUDIO", "VIDEO", "VIDEO", "AUDIO",
    ...AD_SEG_REF_NAMES.map(name => name.startsWith("ref_image") || (name.startsWith("ref_video") && !name.startsWith("ref_video_audio")) ? "IMAGE" : "AUDIO"),
    "IMAGE",
];
function repairSegmentPromptLinks(node) {
    // 按采样器的 positive/negative 条件链反推：应当来自第 9/10 口，错了就自动纠正
    const graph = node?.graph;
    if (!graph) return;
    const move = (linkId, slot) => {
        const link = (graph.links?.get?.(linkId)||graph.links?.[linkId]);
        if (!link || link.origin_slot === slot) return;
        const from = node.outputs?.[link.origin_slot];
        if (from?.links) from.links = from.links.filter(id => id !== linkId);
        link.origin_slot = slot;
        const to = node.outputs?.[slot];
        if (to && !(to.links || []).includes(linkId)) (to.links ||= []).push(linkId);
    };
    const climb = start => {
        let target = start;
        for (let hop = 0; hop < 4 && target; hop++) {
            if ((target.comfyClass || target.type) === "CLIPTextEncode") return target;
            const input = (target.inputs || []).find(item => item.type === "CONDITIONING" && item.link != null);
            const link = input && graph.links?.[input.link];
            target = link && graph.getNodeById?.(link.origin_id);
        }
        return null;
    };
    for (const sampler of graph._nodes || []) {
        for (const [name, slot] of [["positive", 7], ["negative", 8]]) {
            const input = (sampler.inputs || []).find(item => item.name === name && item.type === "CONDITIONING");
            const link = input && input.link != null && graph.links?.[input.link];
            if (!link) continue;
            const clip = climb(graph.getNodeById?.(link.origin_id));
            if (!clip) continue;
            const text = (clip.inputs || []).find(item => item.name === "text");
            const textLink = text && text.link != null && graph.links?.[text.link];
            if (!textLink || String(textLink.origin_id) !== String(node.id)) continue;
            if (textLink.origin_slot !== slot) move(text.link, slot);
        }
    }
}

function normalizeSegmentOutputs(node) {
    const outputs = node.outputs || [];
    // 动态参考口是标准顺序的紧凑子序列（固定 14 口 + 各组实际参考口）；
    // 不能当成旧版 33 口布局去迁移，否则每次载入都会先膨胀成 33 口再收缩（端口跳闪、连线错位）。
    const positions = outputs.map(output => AD_SEG_OUTPUTS.indexOf(output.name));
    const ordered = outputs.length >= AD_SEG_FIXED.length &&
        AD_SEG_FIXED.every((name, index) => outputs[index]?.name === name) &&
        positions.every((position, index) => position >= 0 && (!index || position > positions[index - 1]));
    if (ordered) {
        outputs.forEach((output,index)=>{output.slot_index=index;output.type=AD_SEG_TYPES[positions[index]];for(const id of output.links||[]){const link=node.graph?.links?.get?.(id)||node.graph?.links?.[id];if(link){link.origin_slot=index;link.type=output.type;}}});
        applySegmentOutputLabels(node); return;
    }
    const alias = {prompt_text: "positive_prompt", filename: "positive_prompt",
        negative_text: "negative_prompt", positive: "positive_prompt", negative: "negative_prompt", audio:"round_audio",guide_audio:"round_audio",prompt_index:"index"};
    const entries = AD_SEG_OUTPUTS.map((name, index) => ({name, type: AD_SEG_TYPES[index], links: [], slot_index: index}));
    outputs.forEach((output, oldIndex) => {
        let targetName = output.name;
        if (!AD_SEG_OUTPUTS.includes(targetName)) targetName = alias[targetName] || "";
        let newIndex = AD_SEG_OUTPUTS.indexOf(targetName);
        if (newIndex < 0) {
            // 旧版本“额外输出在固定 12 口之后”或“在末尾”的位置兼容
            if (oldIndex === 12 || oldIndex === 31) newIndex = AD_SEG_OUTPUTS.indexOf("positive_prompt");
            else if (oldIndex === 13 || oldIndex === 32) newIndex = AD_SEG_OUTPUTS.indexOf("negative_prompt");
        }
        if (newIndex < 0) return;
        const links = output.links || [];
        for (const linkId of links) {
            const link = node.graph?.links?.get?.(linkId)||node.graph?.links?.[linkId];
            if (link) { link.origin_slot = newIndex; link.type=AD_SEG_TYPES[newIndex]; entries[newIndex].links.push(linkId); }
        }
    });
    entries.forEach((output,index)=>{output.slot_index=index;});
    node.outputs = entries;
    applySegmentOutputLabels(node);
}
function applySegmentOutputLabels(node) {
    // 固定端口顺序：当前段上下文 → 段号/起始帧/本段帧数/帧率 → 画面宽度/高度 → 正向/反向提示词 →
    // 本段图像/本段音频/本段视频/源视频/源音频 → 参考图/参考视频/参考视频音频/参考音频。
    const labels = {context: "当前段上下文", index: "段号", start_frame: "分段首帧",
        generation_frames: "本段帧数", fps: "帧率", width: "画面宽度", height: "画面高度",
        positive_prompt: "正向提示词", negative_prompt: "反向提示词",
        round_image: "本段图像", round_audio: "本段音频", round_video: "本段视频",
        source_video: "源视频", original_audio: "源音频", mask_frames: "遮罩专用"};
    const refLabel = name => {
        const match = String(name || "").match(/^ref_(image|video_audio|video|audio)_(\d+)$/);
        if (!match) return "";
        const prefix = {image: "参考图", video: "参考视频", video_audio: "参考视频音频", audio: "参考音频"}[match[1]];
        return `${prefix} ${Number(match[2]) + 1}`;
    };
    for (const output of node.outputs || []) {
        const label = labels[output.name] || refLabel(output.name);
        if (label) { output.label = label; output.localized_name = label; }
    }
}

// 端口顺序调整前的旧固定顺序：载入旧工作流时，按“旧序号 → 端口名 → 新序号”把连错口的连线迁移回来。
const LEGACY_SEG_FIXED_ORDER = ["context", "index", "generation_frames", "start_frame", "fps", "source_video",
    "original_audio", "width", "height", "positive_prompt", "negative_prompt", "round_image", "round_audio", "round_video"];
function migrateLegacySegmentLinks(node) {
    const graph = node?.graph;
    if (!graph) return [];
    const outputs = node.outputs || [];
    const slotByName = new Map(outputs.map((output, index) => [output.name, index]));
    const links = graph.links;
    const list = [];
    if (links && typeof links.forEach === "function" && typeof links.get === "function") links.forEach(link => list.push(link));
    else for (const id in (links || {})) list.push(links[id]);
    const moved = [];
    for (const link of list) {
        if (!link || Number(link.origin_id) !== Number(node.id)) continue;
        const slot = Number(link.origin_slot);
        const current = outputs[slot];
        if (!current) continue;
        // 类型一致 → 说明连线没受端口顺序影响，保持不动
        if (link.type && current.type && link.type === current.type) continue;
        const oldName = LEGACY_SEG_FIXED_ORDER[slot];
        if (!oldName) continue;
        const targetSlot = slotByName.get(oldName);
        if (targetSlot == null || targetSlot === slot) continue;
        const target = outputs[targetSlot];
        if (!target || (link.type && target.type && link.type !== target.type)) continue;
        if (Array.isArray(current.links)) current.links = current.links.filter(id => id !== link.id);
        if (!Array.isArray(target.links)) target.links = [];
        if (!target.links.includes(link.id)) target.links.push(link.id);
        link.origin_slot = targetSlot;
        if (target.type) link.type = target.type;
        moved.push({ link: link.id, from: slot, to: targetSlot });
    }
    if (moved.length) {
        try { applySegmentOutputLabels(node); } catch (_error) {}
        node.setDirtyCanvas?.(true, true);
    }
    return moved;
}

// 优先用工作流里保存的 outputs 名称精确迁移（可处理 INT 口之间顺序对调，例如 起始帧/本段帧数）。
function migrateSegmentLinksBySavedOutputs(node, savedOutputs) {
    if (!Array.isArray(savedOutputs) || !savedOutputs.length) return [];
    const slotByName = new Map((node.outputs || []).map((output, index) => [output.name, index]));
    const graph = node?.graph;
    const moved = [];
    const fix = (linkId, newSlot) => {
        const link = graph?.links?.get?.(linkId) || graph?.links?.[linkId];
        if (!link || Number(link.origin_id) !== Number(node.id)) return;
        const target = node.outputs?.[newSlot];
        if (!target) return;
        const currentSlot = Number(link.origin_slot);
        const current = node.outputs?.[currentSlot];
        if (typeof node.getInputLink !== "function" && current && current !== target && Array.isArray(current.links)) current.links = current.links.filter(id => id !== linkId);
        if (!Array.isArray(target.links)) target.links = [];
        if (!target.links.includes(linkId)) target.links.push(linkId);
        if (currentSlot !== newSlot || (target.type && link.type !== target.type)) moved.push({ link: linkId, from: currentSlot, to: newSlot });
        link.origin_slot = newSlot;
        if (target.type) link.type = target.type;
    };
    for (const saved of savedOutputs) {
        if (!saved || !saved.name) continue;
        const newSlot = slotByName.get(saved.name);
        if (newSlot == null) continue;
        for (const linkId of saved.links || []) fix(linkId, newSlot);
    }
    if (moved.length) {
        try { applySegmentOutputLabels(node); } catch (_error) {}
        node.setDirtyCanvas?.(true, true);
    }
    return moved;
}


// 端口顺序调整后，旧工作流可能把线留在错的口上（例如“本段视频 VIDEO”接到了需要 IMAGE 的输入）。
// 若目标输入类型唯一匹配某个输出口，就自动把线迁移到正确口；有多义时不猜，保持原样。
function repairSegmentLinkTypes(node) {
    const graph = node?.graph;
    if (!graph) return [];
    const links = graph.links;
    const list = [];
    if (links && typeof links.forEach === "function" && typeof links.get === "function") links.forEach(link => list.push(link));
    else for (const id in (links || {})) list.push(links[id]);
    const moved = [];
    for (const link of list) {
        if (!link || Number(link.origin_id) !== Number(node.id)) continue;
        const targetNode = graph.getNodeById?.(link.target_id);
        const input = targetNode?.inputs?.[link.target_slot];
        const wanted = input?.type;
        if (!wanted || wanted === "*") continue;
        const currentSlot = Number(link.origin_slot);
        const current = node.outputs?.[currentSlot];
        const currentType = current?.type || link.type;
        if (currentType === wanted) continue;
        const candidates = (node.outputs || []).map((output, index) => ({ output, index })).filter(item => item.output?.type === wanted);
        if (candidates.length !== 1) continue;
        const target = candidates[0];
        if (Array.isArray(current?.links)) current.links = current.links.filter(id => id !== link.id);
        if (!Array.isArray(target.output.links)) target.output.links = [];
        if (!target.output.links.includes(link.id)) target.output.links.push(link.id);
        moved.push({ link: link.id, from: currentSlot, to: target.index, type: wanted });
        link.origin_slot = target.index;
        link.type = wanted;
    }
    if (moved.length) {
        try { applySegmentOutputLabels(node); } catch (_error) {}
        node.setDirtyCanvas?.(true, true);
    }
    return moved;
}

function mountResolutionBadge(node) {
    if (!node || node._adH3ResolutionMounted) return;
    node._adH3ResolutionMounted = true;
    const ratios = {"1:1 (Square)": [1, 1], "16:9 (Landscape)": [16, 9], "9:16 (Portrait)": [9, 16],
        "4:3": [4, 3], "3:4": [3, 4], "3:2": [3, 2], "2:3": [2, 3], "21:9 (Cinematic)": [21, 9]};
    const chinese = {"1:1": [1, 1], "16:9": [16, 9], "9:16": [9, 16], "4:3": [4, 3], "3:4": [3, 4],
        "3:2": [3, 2], "2:3": [2, 3], "21:9": [21, 9]};
    const widgetValue = name => node.widgets?.find(item => item.name === name)?.value;
    function pairOf(aspect) {
        const text = String(aspect || "16:9");
        if (ratios[text]) return ratios[text];
        const match = text.match(/(\d+)\s*:\s*(\d+)/);
        return match ? [Number(match[1]), Number(match[2])] : [16, 9];
    }
    function compute() {
        const [a, b] = pairOf(widgetValue("aspect_ratio"));
        let mp = parseFloat(widgetValue("megapixels"));
        if (!Number.isFinite(mp)) mp = 0.5;
        mp = Math.max(0.05, Math.min(64.0, mp));
        const step = Math.max(1, Math.round(Number(widgetValue("multiple")) || 32));
        const pyRound = value => {
            const floor = Math.floor(value);
            return value - floor === 0.5 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(value);
        };
        const scale = Math.sqrt((mp * 1024 * 1024) / (a * b));
        const snap = value => Math.max(step, pyRound(value / step) * step);
        return [snap(a * scale), snap(b * scale)];
    }
    const el = document.createElement("div");
    el.className = "ad-h3-resolution-badge";
    const render = () => {
        const [w, h] = compute();
        el.textContent = w && h ? `分辨率：${w} × ${h}` : "分辨率：—";
    };
    node._adRenderResolution=render;
    render();
    const widget = node.addDOMWidget("ad_h3_resolution", "div", el, {
        serialize: false, hideOnZoom: false, getHeight: () => 22,
    });
    widget.serialize = false;
    widget.computeSize = width => [width, 26];
    bindWidgetWidth(node,widget);
    fitNodeToWidgets(node);
    // 放到“倍数”控件下面，居中显示
    try {
        const list = node.widgets || [];
        const index = list.indexOf(widget);
        if (index >= 0) list.splice(index, 1);
        const multipleIndex = list.findIndex(item => item.name === "multiple");
        list.splice(multipleIndex >= 0 ? multipleIndex + 1 : list.length, 0, widget);
    } catch {}
    for (const name of ["aspect_ratio", "megapixels", "multiple"]) {
        const target = node.widgets?.find(item => item.name === name);
        if (!target) continue;
        const original = target.callback;
        target.callback = function () {
            const out = original?.apply(this, arguments);
            render();
            node.setDirtyCanvas?.(true, true);
            return out;
        };
    }
}

function syncLinkedPromptTimeline(segment) {
    const graph = segment?.graph;
    const input = segment?.inputs?.find(item => item.name === "queue");
    const link = graph?.links?.get?.(input?.link) || graph?.links?.[input?.link];
    const queue = link && graph?.getNodeById?.(link.origin_id);
    queue?._adH3SyncPromptSegments?.({promptChanged: true});
}

function mountPromptEditor(node) {
    if (!node || node._adH3PromptMounted) return;
    node._adH3PromptMounted = true;
    const native = node.widgets?.find(item => item.name === "正向提示词") || node.widgets?.find(item => item.name === "prompt_text");
    if (!native) return;
    native.type = "hidden";
    native.computeSize = () => [0, -4];
    native.hidden = true;
    const wrap = document.createElement("div");
    wrap.className = "ad-h3-prompt-editor";
    wrap.style.width = "100%"; wrap.style.boxSizing = "border-box"; wrap.style.padding = "0 8px";
    const area = document.createElement("textarea");
    area.className = "ad-h3-prompt-area";
    area.rows = 5;
    const promptHint = "输入正向提示词；每行以 # 开头可按段编排。";
    area.setAttribute("aria-label","正向提示词");
    area.placeholder = promptHint;
    area.addEventListener("focus", () => { area.placeholder = ""; });
    area.addEventListener("blur", () => { if (!area.value.trim()) area.placeholder = promptHint; });
    let initial = String(native.value || "");
    if (initial.trim() === "# 第一段提示词\n# 第二段提示词") { initial = ""; native.value = ""; }
    area.value = initial;
    area.addEventListener("input", () => {
        native.value = area.value;
        try { native.callback?.(area.value); } catch {}
        syncLinkedPromptTimeline(node);
    });
    wrap.append(area);
    const fitAreaWidth = () => {
        const width = Math.max(160, (Number(node.size?.[0]) || 400) - 32);
        area.style.width = "100%";area.style.maxWidth="100%";area.style.boxSizing="border-box";
    };
    fitAreaWidth();
    setTimeout(fitAreaWidth, 0);
    area.addEventListener("focus", fitAreaWidth);
    const previousResize = node.onResize;
    node.onResize = function () { const out = previousResize?.apply(this, arguments); fitAreaWidth(); return out; };
    const widget = node.addDOMWidget("ad_h3_prompt_editor", "div", wrap, {
        serialize: false, hideOnZoom: false, getHeight: () => 238,
    });
    widget.serialize = false;
    widget.computeSize = width => [width, 238];
    bindWidgetWidth(node,widget);
    fitNodeToWidgets(node);
    // 放到“倍数”/分辨率之后
    try {
        const list = node.widgets || [];
        const index = list.indexOf(widget);
        if (index >= 0) list.splice(index, 1);
        const multipleIndex = list.findIndex(item => item.name === "multiple");
        list.splice(multipleIndex >= 0 ? multipleIndex + 2 : list.length, 0, widget);
    } catch {}
}

function mountNegativeEditor(node) {
    if (!node || node._adH3NegativeMounted) return;
    node._adH3NegativeMounted = true;
    const native = node.widgets?.find(item => item.name === "反向提示词") || node.widgets?.find(item => item.name === "negative_prompt");
    if (!native) return;
    native.type = "hidden";
    native.computeSize = () => [0, -4];
    native.hidden = true;
    const wrap = document.createElement("div");
    wrap.className = "ad-h3-prompt-editor";
    wrap.style.width = "100%"; wrap.style.boxSizing = "border-box"; wrap.style.padding = "0 8px";
    const area = document.createElement("textarea");
    area.className = "ad-h3-prompt-area ad-h3-negative-area";
    area.rows = 4;
    area.setAttribute("aria-label", "反向提示词");
    area.placeholder = "输入反向提示词";
    area.value = String(native.value || "");
    area.addEventListener("input", () => { native.value = area.value; try { native.callback?.(area.value); } catch {} });
    wrap.append(area);
    const fitWidth = () => { area.style.width = "100%"; area.style.maxWidth = "100%"; area.style.boxSizing = "border-box"; };
    fitWidth(); setTimeout(fitWidth, 0);
    const widget = node.addDOMWidget("ad_h3_negative_editor", "div", wrap, {
        serialize: false, hideOnZoom: false, getHeight: () => 132,
    });
    widget.serialize = false;
    widget.computeSize = width => [width, 132];
    bindWidgetWidth(node, widget);
    try {
        const list = node.widgets || [];
        const index = list.indexOf(widget);
        if (index >= 0) list.splice(index, 1);
        const positiveIndex = list.findIndex(item => item.name === "ad_h3_prompt_editor");
        list.splice(positiveIndex >= 0 ? positiveIndex + 1 : list.length, 0, widget);
    } catch {}
}

function mountPromptPrefix(node) {
    if (!node || node._adH3PrefixMounted) return;
    node._adH3PrefixMounted = true;
    const native = node.widgets?.find(item => item.name === "统一提示词前缀") || node.widgets?.find(item => item.name === "prompt_prefix");
    if (!native) return;
    native.type = "hidden";
    native.computeSize = () => [0, -4];
    native.hidden = true;
    const el = document.createElement("div");
    el.className = "ad-h3-prefix-editor";
    el.style.width = "100%"; el.style.boxSizing = "border-box"; el.style.padding = "0 8px";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "统一提示词前缀";
    input.value = String(native.value || "");
    input.addEventListener("input", () => { native.value = input.value; try { native.callback?.(input.value); } catch {} });
    el.append(input);
    const fitInputWidth = () => {
        const width = Math.max(160, (Number(node.size?.[0]) || 400) - 32);
        input.style.width = "100%";input.style.maxWidth="100%";input.style.boxSizing="border-box";
    };
    fitInputWidth();
    setTimeout(fitInputWidth, 0);
    input.addEventListener("focus", fitInputWidth);
    const prevResize2 = node.onResize;
    node.onResize = function () { const out = prevResize2?.apply(this, arguments); fitInputWidth(); return out; };
    const widget = node.addDOMWidget("ad_h3_prefix_editor", "div", el, {
        serialize: false, hideOnZoom: false, getHeight: () => 34,
    });
    widget.serialize = false;
    widget.computeSize = width => [width, 34];
    // 统一提示词前缀始终显示（不再因空值隐藏）
    const applyPrefixVisibility = () => {
        el.style.display = "";
        widget.hidden = false;
        widget.computeSize = width => [width, 34];
        widget.getHeight = () => 34;
        node.setDirtyCanvas?.(true, true);
    };
    applyPrefixVisibility();
    setTimeout(applyPrefixVisibility, 0);
    setTimeout(applyPrefixVisibility, 600);
    input.addEventListener("input", () => { applyPrefixVisibility(); });
    bindWidgetWidth(node,widget);
    fitNodeToWidgets(node);
    // 放到所有控件最后（反向提示词下面）
    try {
        const list = node.widgets || [];
        const index = list.indexOf(widget);
        if (index >= 0) list.splice(index, 1);
        list.push(widget);
    } catch {}
}

function mountPromptHint(node) {
    if (!node || node._adH3PromptHintMounted) return;
    node._adH3PromptHintMounted = true;
    const el = document.createElement("div");
    el.className = "ad-h3-prompt-hint";
    el.textContent = "提示词规则：每行以 # 开头表示一段（第 1 段、第 2 段…）；不带 # 时整段提示词每轮复用；前缀/后缀会自动拼在每轮提示词前后。";
    const widget = node.addDOMWidget("ad_h3_prompt_hint", "div", el, {
        serialize: false, hideOnZoom: false, getHeight: () => 34,
    });
    widget.serialize = false;
    widget.computeSize = width => [width, 34];
}

function updateSegmentIndexVisibility(node) {
    const widget = node.widgets?.find(item => item.name === "段索引");
    if (!widget) return;
    if (!("__adH3OriginalCompute" in widget)) {
        widget.__adH3OriginalCompute = widget.computeSize || null;
        widget.__adH3OriginalHidden = !!widget.hidden;
    }
    const connected = !!node.inputs?.find(input => input.name === "queue")?.link;
    if (connected) {
        widget.hidden = true;
        widget.computeSize = () => [0, -4];
    } else {
        widget.hidden = widget.__adH3OriginalHidden || false;
        if (widget.__adH3OriginalCompute) widget.computeSize = widget.__adH3OriginalCompute;
        else delete widget.computeSize;
        node.setDirtyCanvas?.(true, false);
    }
}
function repairSegmentNode(node) {
    try {
        const prefixWidget = (node.widgets || []).find(item => item.name === "ad_h3_prefix_editor");
        const nativePrefix = (node.widgets || []).find(item => item.name === "统一提示词前缀" || item.name === "prompt_prefix");
        const prefixElement = prefixWidget && (prefixWidget.element || prefixWidget.el);
        if (prefixWidget && prefixElement) {
            prefixElement.style.display = "";
            prefixWidget.hidden = false;
            prefixWidget.computeSize = width => [width, 34];
            prefixWidget.getHeight = () => 34;
        }
    } catch {}

    repairSegmentPromptLinks(node);
    updateSegmentIndexVisibility(node);
    const width = Math.max(320, Number(node.size?.[0]) || 400);
    const area = node.widgets?.find(item => item.name === "ad_h3_prompt_editor")?.element?.querySelector?.(".ad-h3-prompt-area");
    const prefix = node.widgets?.find(item => item.name === "ad_h3_prefix_editor")?.element?.querySelector?.("input");
    if (area) {
        area.style.width = "100%";
        const holder = area.parentElement;
        if (holder) { holder.style.width = "100%"; holder.style.boxSizing = "border-box"; holder.style.padding = "0 8px"; }
        area.style.maxWidth = "100%";
        if (!area.placeholder&&document.activeElement!==area) area.placeholder = "输入正向提示词；每行以 # 开头可按段编排。";
    }
    if (prefix) {
        prefix.style.width = "100%";prefix.style.maxWidth="100%";
        const holder = prefix.parentElement;
        if (holder) { holder.style.width = "100%"; holder.style.boxSizing = "border-box"; holder.style.padding = "0 8px"; }
    }
    const nativePrompt=node.widgets?.find(widget=>widget.name==="正向提示词");
    const nativePrefix=node.widgets?.find(widget=>widget.name==="统一提示词前缀");
    if(area&&document.activeElement!==area)area.value=String(nativePrompt?.value||"");
    if(prefix&&document.activeElement!==prefix)prefix.value=String(nativePrefix?.value||"");
    node._adRenderResolution?.();
    try {
        const target = node.computeSize?.();
        if (target && Number(node.size?.[1]) < target[1] - 4) node.setSize([node.size[0], Math.round(target[1])]);
        node.setDirtyCanvas?.(true, false);
    } catch {}
}
function segmentRepairSignature(node) {
    return JSON.stringify([node.size?.[0], node.graph?._version,
        (node.widgets || []).map(widget => [widget.name, widget.value, !!widget.hidden]),
        (node.inputs || []).map(input => [input.name, input.type, input.link]),
        (node.outputs || []).map(output => [output.name, output.type, output.links])]);
}
function startSegmentRepairTimer() {
    if (window.__adH3RepairTimer) return;
    window.__adH3RepairTimer = setInterval(() => {
        if (document.hidden || !app?.graph?._nodes) return;
        for (const node of app.graph._nodes) {
            if (node?.comfyClass !== "AidenH3Segment") continue;
            const signature = segmentRepairSignature(node);
            if (signature === node._adH3RepairSignature) continue;
            try { repairSegmentNode(node); } catch {}
            try { node._adH3ApplyDynamicOutputs?.(); } catch {}
            try { repairSegmentLinkTypes(node); } catch {}
            node._adH3RepairSignature = segmentRepairSignature(node);
        }
    }, 4000);
}

const savedSegmentSchemas = new Map();
const schemasBySavedInfo = new WeakMap();
function setupNode(node) {
    if (["AidenH3AudioQueue", "AidenH3Segment", "AidenH3VideoPreview"].includes(node.comfyClass)) mountNodeProgress(node);
    installGlassTheme();
    if(node.comfyClass==="AidenH3AudioQueue")mountTimelineEditor(node);
    if(node.comfyClass==="AidenH3Segment"){
      normalizeAspectWidget(node);
      const prevConfigure=node.onConfigure;
      const syncSegmentOutputs=()=>{normalizeSegmentOutputs(node);try{node._adH3ApplyDynamicOutputs?.();}catch(_error){}};
      node.onConfigure=function(){this._adH3Configured=true;this._adH3GraphReady=false;const info=arguments[0];const savedOutputs=(info&&schemasBySavedInfo.get(info))||savedSegmentSchemas.get(String(this.id))||(info&&Array.isArray(info.outputs)?info.outputs.map(output=>({...output,links:[...(output.links||[])]})):null);this._adH3SavedSchema=savedOutputs;const restoreNames=()=>{if(savedOutputs)node.outputs=savedOutputs.map((saved,index)=>{const output=node.outputs?.[index]||{links:[]};output.name=saved.name;output.type=saved.type;output.h3_key=undefined;output.slot_index=index;return output;});};restoreNames();const out=prevConfigure?.apply(this,arguments);restoreNames();normalizeAspectWidget(node);syncSegmentOutputs();const repairLinks=()=>{try{migrateSegmentLinksBySavedOutputs(node,savedOutputs);}catch(_error){}try{migrateLegacySegmentLinks(node);}catch(_error){}};repairLinks();setTimeout(syncSegmentOutputs,0);setTimeout(syncSegmentOutputs,400);setTimeout(repairLinks,0);setTimeout(repairLinks,120);return out;};
      mountResolutionBadge(node);mountPromptEditor(node);mountNegativeEditor(node);mountPromptPrefix(node);
      startSegmentRepairTimer(); repairSegmentNode(node);
      const relabel={"正向提示词":"正向提示词","统一提示词前缀":"统一提示词前缀","反向提示词":"反向提示词",multiple:"倍数"};
      syncSegmentOutputs();
      setTimeout(syncSegmentOutputs,0);
      setTimeout(syncSegmentOutputs,600);
      for(const widget of node.widgets||[]){ if(relabel[widget.name]){ widget.label=relabel[widget.name]; widget.localized_name=relabel[widget.name]; } }
      // 动态参考端口：素材库实际有多少参考素材就显示多少口
      node._adH3ApplyDynamicOutputs = () => {
        const graph = node.graph;
        if (!graph) return;  // 已切走/移除节点的迟到回调不能操作另一个工作流。
        const queueInput = (node.inputs || []).find(input => input.name === "queue");
        const link = queueInput?.link != null ? (graph.links?.get?.(queueInput.link) || graph.links?.[queueInput.link]) : null;
        const queue = link && graph.getNodeById?.(link.origin_id);
        const widget = queue?.widgets?.find(item => item.name === "queue_state");
        if (queueInput?.link == null) {
            if (node._adH3SavedSchema && !node._adH3GraphReady) return;
            updateSegmentMaterialOutputs(node, {sources:[], images:[], video_tracks:[], audio_tracks:[]});
            applySegmentOutputLabels(node);
            return;
        }
        if (!widget || queue?._adH3MaterialsReady === false) return;
        try {
            const raw = typeof widget.getValue === "function" ? widget.getValue() : widget.value;
            const state = typeof raw === "string" ? JSON.parse(raw || "{}") : raw;
            if (!Array.isArray(state?.sources)) return;
            updateSegmentMaterialOutputs(node, state);
            applySegmentOutputLabels(node);
        } catch {}
      };
      const refCountsWidget = (node.widgets || []).find(widget => widget.name === "ref_counts");
      if (refCountsWidget) { refCountsWidget.type = "hidden"; refCountsWidget.computeSize = () => [0, -4]; refCountsWidget.hidden = true; }
      node._adH3ApplyDynamicOutputs();
      setTimeout(() => node._adH3ApplyDynamicOutputs?.(), 0);
      setTimeout(() => node._adH3ApplyDynamicOutputs?.(), 800);
      setTimeout(() => { try { repairSegmentLinkTypes(node); } catch (_error) {} }, 900);
    }
    if(node.comfyClass==="AidenH3VideoPreview"){
      // 兼容旧工作流：只保留一个“任意”输入口 media，旧 video 连线迁移过来
      const normalizePreviewInputs=()=>{
        const inputs=node.inputs||[]; const legacy=inputs.find(item=>item.name==="video");
        if(legacy){
          const media=inputs.find(item=>item.name==="media");
          if(media&&legacy.link!=null&&media.link==null){
            media.link=legacy.link;
            const l=node.graph?.links?.get?.(legacy.link)||node.graph?.links?.[legacy.link];
            if(l){l.target_slot=inputs.indexOf(media);}
          }
          node.inputs=inputs.filter(item=>["media","对比媒体","compare_media","context"].includes(item.name)||item.widget);
          node.setDirtyCanvas?.(true,true);
        }
        if(node.outputs&&node.outputs.length>1) node.outputs=node.outputs.slice(0,1);
      };
      const applyPreviewInputLabels = () => {
        normalizePreviewInputOrder(node);
        for (const input of node.inputs || []) {
          if (input.name === "media") input.label = "媒体A（原图/原视频/音频）";
          else if (input.name === "对比媒体" || input.name === "compare_media") input.label = "媒体B（超分/插帧后/音频）";
        }
        node.setDirtyCanvas?.(true, true);
      };
      normalizePreviewInputs();
      applyPreviewInputLabels();
      setTimeout(applyPreviewInputLabels, 0);
      setTimeout(applyPreviewInputLabels, 600);
      const previewConfigure=node.onConfigure;
      node.onConfigure=function(){const out=previewConfigure?.apply(this,arguments);normalizePreviewInputs();applyPreviewInputLabels();return out;};
      const saveWidget=node.widgets?.find(widget=>widget.name==="save_config");
      if(saveWidget){saveWidget.type="hidden";saveWidget.computeSize=()=>[0,-4];saveWidget.hidden=true;}
      mountNativePlayer(node,api);mountReview(node);
    }
    if(node.comfyClass==="AidenH3Segment"){
        const token=node.widgets?.find(widget=>widget.name==="run_token");
        node._adH3RunAffected = options => runSelectedSegments(node, options);
        node._adH3NewGroup = () => {
            const viewer=(node.graph?._nodes||[]).find(candidate =>
                candidate.comfyClass === "AidenH3VideoPreview" && upstreamSegment(candidate) === node);
            return viewer?._adH3Act?.("new_group") ?? Promise.resolve(false);
        };
        // Keep 执行标识 visible (user requested); do not hide run_token.
        const session=node.widgets?.find(widget=>widget.name==="session_id");
        if(session && (session.value==="h3-session" || session.value==="AD-H3-session" || session.value==="")){
            session.value="AD-H3-"+crypto.randomUUID().replaceAll("-","").slice(0,12);
        }
        for (const name of ["fps","overlap_frames","guard_frames","max_frames"]) {
            const w = node.widgets?.find(widget => widget.name === name);
            if (w) { w.type = "hidden"; w.computeSize = () => [0, -4]; }
        }
        const reanchor=node.widgets?.find(widget=>widget.name==="reanchor_every");
        if(reanchor){
            const n=Number(reanchor.value);
            if(!Number.isFinite(n) || Number.isNaN(n)) reanchor.value=0;
        }
    }
    localizeNode(node);
    if(node.comfyClass==="AidenH3Segment") adPinSegmentWidgetOrder(node);
    if(chineseTitles[node.comfyClass]&&!node._adChineseConfigure){
        node._adChineseConfigure=true;
        const configured=node.onConfigure;
        node.onConfigure=function(){const result=configured?.apply(this,arguments);this._adH3Configured=true;localizeNode(this);return result;};
    }
    setTimeout(() => applyInitialNodeSize(node), 0);
    setTimeout(() => applyInitialNodeSize(node), 250);
    setTimeout(() => applyInitialNodeSize(node), 800);
}


const H3_ASPECT_16x9={0.2:[608,352],0.3:[736,416],0.4:[864,480],0.5:[960,544],0.6:[1056,608],0.7:[1152,640],0.8:[1216,672],0.9:[1280,736],0.98:[1344,768],1.0:[1376,768],1.2:[1504,832],1.5:[1664,928],1.8:[1824,1024],2.0:[1920,1088]};
const H3_ASPECT_RATIOS={"1:1 (Square)":[1,1],"16:9 (Landscape)":[16,9],"9:16 (Portrait)":[9,16],"4:3":[4,3],"3:4":[3,4],"3:2":[3,2],"2:3":[2,3],"21:9":[21,9]};
function h3ResolveCanvas(aspectRatio="16:9 (Landscape)", megapixels="0.5", multiple=32){
    const mp=Number(megapixels); if(!Number.isFinite(mp)||mp<=0) return null;
    let label=String(aspectRatio||"16:9 (Landscape)");
    if(!(label in H3_ASPECT_RATIOS)){
        const hit=Object.keys(H3_ASPECT_RATIOS).find(key=>key.startsWith(label)||label.startsWith(key.split(/\s+/)[0]));
        if(!hit) return null; label=hit;
    }
    const [aw,ah]=H3_ASPECT_RATIOS[label];
    const nearest=Object.keys(H3_ASPECT_16x9).map(Number).sort((a,b)=>Math.abs(a-mp)-Math.abs(b-mp)||a-b)[0];
    if(aw===16&&ah===9) return H3_ASPECT_16x9[nearest];
    if(aw===9&&ah===16){const [w,h]=H3_ASPECT_16x9[nearest]; return [h,w];}
    const scale=Math.sqrt((mp*1024*1024)/(aw*ah));
    const pyRound=value=>{const floor=Math.floor(value);return value-floor===0.5?(floor%2===0?floor:floor+1):Math.round(value);};
    const step=Math.max(1,Number(multiple)||32);
    return [Math.max(step,pyRound((aw*scale)/step)*step),Math.max(step,pyRound((ah*scale)/step)*step)];
}
/* ad-res-footer-glue-widgets 20261025-0458 */


// 把本插件自定义端口类型登记为“默认连接节点”：从输出口拉线时，菜单里直接出现可连接的 AD 节点。
function registerCustomSlotDefaults(){
    const litegraph = window.LiteGraph || (typeof LiteGraph!=="undefined"?LiteGraph:null);
    if(!litegraph) return false;
    litegraph.slot_types_default_out = litegraph.slot_types_default_out || {};
    litegraph.slot_types_default_in = litegraph.slot_types_default_in || {};
    litegraph.slot_types_default_out["H3_AUDIO_QUEUE"] = [{node:"AidenH3Segment"}];
    litegraph.slot_types_default_in["H3_AUDIO_QUEUE"] = [{node:"AidenH3AudioQueue"}];
    // 插件只注册 3 个节点（AidenH3Commit 只是历史遗留类，未注册），这里只给真实存在的节点
    litegraph.slot_types_default_out["H3_LOOP_CONTEXT"] = [{node:"AidenH3VideoPreview"}];
    litegraph.slot_types_default_in["H3_LOOP_CONTEXT"] = [{node:"AidenH3Segment"}];
    return true;
}
app.registerExtension({
    name:"Aiden.H3Loop",
    async setup(app){
        registerCustomSlotDefaults();
        setTimeout(registerCustomSlotDefaults, 1000);
        setTimeout(registerCustomSlotDefaults, 3000);
        // 兼容保护：本插件使用 LiteGraph 自定义 DOM 控件，Nodes 2.0 下布局/交互不受支持。
        // 检测到 Nodes 2.0 开启时自动切回经典节点设计，避免功能不可用。
        for(let attempt=0;attempt<60;attempt++){
            const manager=app?.extensionManager;
            if(manager?.setting?.get){
                try{
                    if(manager.setting.get("Comfy.VueNodes.Enabled")){
                        await manager.setting.set("Comfy.VueNodes.Enabled", false);
                        try{
                            manager.toast?.add?.({severity:"warn",summary:"AD-Segmented-Loop",
                                detail:"已自动切回经典节点设计：本插件使用自定义 DOM 界面，暂不支持 Nodes 2.0。",
                                life:8000});
                        }catch(_error){}
                    }
                }catch(_error){}
                return;
            }
            await new Promise(resolve=>setTimeout(resolve,250));
        }
    },
    beforeConfigureGraph(graphData){
        savedSegmentSchemas.clear();
        const infos = new Map((graphData?.nodes || []).map(info => [String(info.id), info]));
        const rawLinks = graphData?.links || [];
        const links = Array.isArray(rawLinks) ? rawLinks : Object.values(rawLinks);
        // 旧工作流使用元组，新前端在扩展钩子前转换为对象；两种格式都按名称恢复。
        const field = (link, key, index) => Array.isArray(link) ? link[index] : link?.[key];
        const setField = (link, key, index, value) => { if (Array.isArray(link)) link[index] = value; else link[key] = value; };
        for (const info of graphData?.nodes || []) {
            if (info.type !== "AidenH3Segment") continue;
            const queueInput = info.inputs?.find(input => input.name === "queue");
            if (queueInput?.link == null) continue;
            const queueLink = links.find(link => String(field(link,"id",0)) === String(queueInput.link));
            const queue = queueLink && infos.get(String(field(queueLink,"origin_id",1)));
            let state;
            try { const value = savedTimelineValue(queue); state = typeof value === "string" ? JSON.parse(value) : value; } catch {}
            if (!Array.isArray(state?.sources)) continue;
            const counts = segmentRefCounts(state);
            const desired = [...AD_SEG_FIXED];
            for (const [kind, prefix] of [["image","ref_image"],["video","ref_video"],["video_audio","ref_video_audio"],["audio","ref_audio"]])
                for (let index=0; index<counts[kind]; index++) desired.push(`${prefix}_${index}`);
            desired.push(...AD_SEG_EXTRA);
            const previous = info.outputs || [];
            const byName = new Map(previous.map(output => [output.name, output]));
            for (const link of links) {
                if (String(field(link,"origin_id",1)) !== String(info.id)) continue;
                const name = previous[field(link,"origin_slot",2)]?.name;
                const index = desired.indexOf(name);
                if (index >= 0) {
                    setField(link,"origin_slot",2,index);
                    setField(link,"type",5,AD_SEG_TYPES[AD_SEG_OUTPUTS.indexOf(name)]);
                }
            }
            info.outputs = desired.map((name, index) => ({
                ...(byName.get(name) || {}), name, type: AD_SEG_TYPES[AD_SEG_OUTPUTS.indexOf(name)],
                slot_index: index, links: [...(byName.get(name)?.links || [])],
            }));
            const schema = info.outputs.map(output => ({...output,links:[...(output.links||[])],
                targets:(output.links||[]).map(id=>{
                    const link=links.find(item=>String(field(item,"id",0))===String(id));
                    if(!link)return null;
                    const target=field(link,"target_id",3),slot=field(link,"target_slot",4);
                    return {id:target,slot,name:infos.get(String(target))?.inputs?.[slot]?.name};
                }).filter(Boolean),
            }));
            savedSegmentSchemas.set(String(info.id), schema);
            schemasBySavedInfo.set(info, schema);
        }
    },
    loadedGraphNode(node){
        if ((node.comfyClass || node.type) !== "AidenH3Segment") return;
        const saved = node._adH3SavedSchema;
        if (!saved) return;
        const previous = node.outputs || [];
        node.outputs = saved.map((slot,index)=>{
            const output=previous[index]||{links:[]};
            output.name=slot.name; output.type=slot.type;
            output.h3_key=undefined; output.slot_index=index;
            return output;
        });
        node._adH3MaterialSignature = "";
        try { node._adH3ApplyDynamicOutputs?.(); } catch {}
    },
    afterConfigureGraph(){
        for (const node of app.graph?._nodes || []) {
            if ((node.comfyClass || node.type) !== "AidenH3Segment") continue;
            node._adH3GraphReady = true;
            setTimeout(() => { if (node.graph) node._adH3ApplyDynamicOutputs?.(); }, 0);
        }
        // 核心版本可能按固定返回类型先移除紧凑参考口的连线；按保存的两端名称恢复。
        for (const node of app.graph?._nodes || []) {
            const saved = node._adH3SavedSchema;
            if (!saved || !node.graph) continue;
            const graph = node.graph;
            for(const output of saved){
                const slot=node.outputs?.findIndex(item=>item.name===output.name);
                if(slot==null||slot<0)continue;
                for(const spec of output.targets||[]){
                    const target=graph.getNodeById(spec.id);
                    const named=target?.inputs?.findIndex(input=>input.name===spec.name);
                    const index=named>=0?named:spec.slot;
                    const input=target?.inputs?.[index];
                    if(!input)continue;
                    const link=graph.links?.get?.(input.link)||graph.links?.[input.link];
                    if(link&&String(link.origin_id)!==String(node.id))continue;
                    if(link&&link.origin_slot===slot)continue;
                    try{node.connect(slot,target,index);}catch{}
                }
            }
        }
    },
    async beforeRegisterNodeDef(nodeType,nodeData){
        if (Object.prototype.hasOwnProperty.call(chineseTitles, nodeData.name)) {
            // 来源提示使用统一产品名；保留旧安装地址与内部节点类型以兼容已有工作流。
            nodeData.python_module = "custom_nodes.AD-Segmented-Loop";
        }
                if(nodeData.name==="AidenH3Segment"){
            const createdSegment=nodeType.prototype.onNodeCreated;
            const dedupeSegmentOutputs=normalizeSegmentOutputs;
            const fixReanchor=(node)=>{
                const widget=node.widgets?.find(w=>w.name==="reanchor_every");
                if(!widget) return;
                const n=Number(widget.value);
                if(!Number.isFinite(n) || Number.isNaN(n)) widget.value=0;
            };
            // 最后一个可见控件的底部，用于把“分辨率”文字放在执行标识等全部控件之下。
            const widgetBottom=(node)=>{
                let bottom=0;
                for(const widget of node.widgets||[]){
                    if(widget.type==="hidden") continue;
                    const y=Number(widget.last_y);
                    if(!Number.isFinite(y)) continue;
                    let height=20;
                    try{
                        const size=widget.computeSize?.(node.size[0]);
                        if(Array.isArray(size)&&Number.isFinite(size[1])&&size[1]>0) height=size[1];
                    }catch(_error){}
                    bottom=Math.max(bottom,y+height);
                }
                return bottom;
            };
            // context 是否接到“记录分段结果”（后端只接受一个该节点，否则执行直接报错）。
            const commitConnected=(node)=>{
                const output=(node.outputs||[]).find(slot=>slot.name==="context");
                if(!output||!Array.isArray(output.links)||!output.links.length) return false;
                const graph=node.graph;
                if(!graph) return false;
                return output.links.some(id=>{
                    const link=graph.links?.get?.(id)||graph.links?.[id];
                    const target=link&&graph.getNodeById?.(link.target_id);
                    return !!target&&(target.comfyClass==="AidenH3Commit"||String(target.type)==="AidenH3Commit");
                });
            };
            // 旧工作流保存的高度不含底部文字区，载入后补足，避免“分辨率”被裁掉或压在控件上。
            const ensureFooterSpace=(node)=>{
                if(node._adH3Configured) return; // 旧工作流：尊重用户保存的高度，载入时不做高度补足
                const needed=Math.ceil(widgetBottom(node)+34+(commitConnected(node)?0:20));
                // 只补足被裁掉的高度，绝不压缩用户保存/拖动过的高度
                if((node.size?.[1]??0)<needed-2) node.setSize([node.size[0],needed]);
            };
            nodeType.prototype.onNodeCreated=function(){
                const result=createdSegment?.apply(this,arguments);
                // 不再重排输出口：保持后端定义顺序，避免旧工作流连线按序号错位
                fixReanchor(this);
                                if(false){
                    this._adResFooter=true;
                    const previousDraw=this.onDrawForeground;
                    this.onDrawForeground=function(ctx){
                        previousDraw?.apply(this,arguments);
                        const value=name=>this.widgets?.find(widget=>widget.name===name)?.value;
                        const resolved=h3ResolveCanvas(value("aspect_ratio"), value("megapixels"), Number(value("multiple"))||32);
                        const text=resolved?`分辨率 ${resolved[0]} x ${resolved[1]}`:`分辨率 -- x --`;
                        const connected=commitConnected(this);
                        // 放在全部可见控件（含执行标识）下方，节点底部不够时由 ensureFooterSpace 补足。
                        const overflow=connected?0:Math.max(0,widgetBottom(this)+18+34-this.size[1]);
                        const footerY=(connected?Math.max(widgetBottom(this)+18, this.size[1]-16):widgetBottom(this)+18)-overflow;
                        ctx.save();
                        ctx.fillStyle="#8ecbff";
                        ctx.font="bold 16px system-ui,'Microsoft YaHei',sans-serif";
                        ctx.textAlign="center";
                        ctx.textBaseline="middle";
                        ctx.fillText(text, this.size[0]/2, footerY);
                        if(!connected){
                            ctx.fillStyle="#ffb86b";
                            ctx.font="bold 14px system-ui,'Microsoft YaHei',sans-serif";
                            ctx.fillText("未连接「记录分段结果」节点（context 输出）", this.size[0]/2, footerY+20);
                        }
                        ctx.restore();
                    };
                }
                ensureFooterSpace(this);
                if(false){
                    this._adSegConn=true;
                    const previousConn=this.onConnectionsChange;
                    this.onConnectionsChange=function(){
                        const out=previousConn?.apply(this,arguments);
                        ensureFooterSpace(this);
                        this.setDirtyCanvas?.(true,true);
                        return out;
                    };
                }
                if(!this._adSegDedupeConfigure){
                    this._adSegDedupeConfigure=true;
                    const previous=this.onConfigure;
                    this.onConfigure=function(){
                        const out=previous?.apply(this,arguments);
                        dedupeSegmentOutputs(this);
                        fixReanchor(this);
                        try{this._adH3ApplyDynamicOutputs?.();}catch(_error){}
                        setTimeout(()=>{ if(this.graph){ ensureFooterSpace(this); this.setDirtyCanvas?.(true,true); } },0);
                        return out;
                    };
                }
                return result;
            };
        }
if(!chineseTitles[nodeData.name])return;
        const created=nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated=function(){
            const result=created?.apply(this,arguments);
            setupNode(this);return result;
        };
    },
    async nodeCreated(node){
        try { registerCustomSlotDefaults(); } catch (_error) {}
        setupNode(node);
        // Native canvas nodes already zoom. Forward wheel events from embedded
        // controls to that same canvas, including third-party DOM widgets.
        const attach=element=>{
            if(!element||element._adWheelZoom)return;element._adWheelZoom=true;
            element.addEventListener("wheel",event=>{
                if(event.target?.closest?.('.ad-h3-ab-overlay[data-zoom="image"]')) return;
                const canvas=app.canvas?.canvas;if(!canvas)return;
                event.preventDefault();event.stopImmediatePropagation();
                canvas.dispatchEvent(new WheelEvent("wheel",{bubbles:true,cancelable:true,composed:true,
                    clientX:event.clientX,clientY:event.clientY,deltaX:event.deltaX,deltaY:event.deltaY,deltaZ:event.deltaZ,
                    deltaMode:event.deltaMode,ctrlKey:event.ctrlKey,shiftKey:event.shiftKey,altKey:event.altKey,metaKey:event.metaKey}));
            },{capture:true,passive:false});
        };
        for(const widget of node.widgets||[])attach(widget.element||widget.inputEl);
        const add=node.addDOMWidget;
        if(!node._adWheelWidgetHook){node._adWheelWidgetHook=true;node.addDOMWidget=function(name,type,element,...args){const widget=add.call(this,name,type,element,...args);attach(element);return widget;};}
    },
});







