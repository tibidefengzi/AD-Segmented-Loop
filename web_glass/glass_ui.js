// AD-H3 自有界面适配：只移动现有控件，不替代时间线状态或生成逻辑。
const ID = "ad-h3-liquid-glass";
const CSS = `
.aiden-h3{
 --ad-bg:#282e3a;--ad-surface:#323946;--ad-surface-deep:#272f3a;--ad-surface-dark:#252e3b;
 --ad-screen:#252f3c;--ad-border:#4a5465;--ad-border-soft:#404a5a;
 --ad-text:#f4f7fa;--ad-text-dim:#cdd4de;--ad-text-mute:#a4aebd;
 --ad-accent:#3976cc;--ad-accent-hover:#6d9ee4;--ad-accent-border:#7ba6e2;--ad-accent-soft:#bfd5f5;
 --ad-danger:#a24e5d;--ad-danger-text:#ffb2bf;--ad-radius:14px;--ad-radius-sm:8px;
 --ad-font:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;--ad-control-h:33px;
 color:var(--ad-text)!important;background:var(--ad-bg)!important;
 border:1px solid var(--ad-border)!important;border-radius:16px!important;
 box-shadow:0 8px 24px #0003;font-weight:400;
}
.aiden-h3 button,.aiden-h3 select,.aiden-h3 input:not([type=range]),.aiden-h3 textarea{color:var(--ad-text);border-color:var(--ad-border);background:#394150;border-radius:8px;box-shadow:none}
.aiden-h3 button{cursor:pointer}.aiden-h3 button:hover{background:#455161}
.aiden-h3 button:disabled{cursor:default;opacity:.42}
.aiden-h3 button.primary,.aiden-h3 .ad-h3-loop-actions button{background:var(--ad-accent)!important;border:1px solid var(--ad-accent-border)!important;color:#fff!important;border-radius:8px!important;box-shadow:none}
.aiden-h3 button.primary:hover,.aiden-h3 .ad-h3-loop-actions button:hover{background:var(--ad-accent-hover)!important}
.aiden-h3 [hidden]{display:none!important}
.aiden-h3 button:focus-visible,.aiden-h3 select:focus-visible,.aiden-h3 input:focus-visible,.aiden-h3 summary:focus-visible{outline:2px solid #9dc0f3;outline-offset:2px}
.aiden-h3.h3-tl{container-type:inline-size;overflow:hidden!important}
.aiden-h3.h3-tl .h3-tl-inner{padding:18px;gap:12px}
.ad-glass .h3-glass-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.ad-glass .h3-glass-title{font-size:20px;font-weight:650;color:#f3f6fc}
.ad-glass .h3-glass-subtitle{font-size:11px;color:#abb7c7;margin-top:4px}
.ad-glass .h3-glass-badge{font-size:11px;border:1px solid #4e5c78;background:#324460;border-radius:16px;padding:6px 10px;white-space:nowrap;color:#ccdff7}
.ad-glass .h3-load-row{gap:8px!important}.ad-glass .h3-load-row>button{padding:6px 15px;height:36px}
.ad-glass .h3-path-field{height:auto!important;min-width:0!important;flex:1 1 100%!important;background:#252f3c!important;border:1px solid var(--ad-border)!important;padding:6px 9px;border-radius:9px;flex-wrap:wrap}
.ad-glass .h3-path-label{color:#c2cad6!important;font-weight:500}
.ad-glass .h3-path-field input{color:#f4f7fa!important;min-width:140px;background:#343e4e!important}
.ad-glass .h3-path-field input::placeholder{color:#9faaba!important}
.ad-glass .h3-path-field button{color:#dde3ec!important;background:#384558!important;border:1px solid #4f5c72!important;font-size:11px}
.ad-glass .h3-glass-workspace{display:grid;grid-template-columns:minmax(0,1fr);min-width:0}
.ad-glass .h3-player,.ad-glass .h3-glass-library{border:1px solid #4c586d!important;background:#2c3543!important;border-radius:12px!important;min-width:0;padding:12px;box-sizing:border-box}
.ad-glass h3{margin:0;font-size:13px;font-weight:650;color:#f1f4f9}
.ad-glass .h3-player{min-height:0;display:flex!important;flex-direction:column;overflow:hidden}
.ad-glass .h3-player-head{padding:0 0 10px;background:transparent!important;font-size:12px;gap:8px;color:#d3dbe7;flex-wrap:wrap}
.ad-glass .h3-player-head [data-player-meta]{font-size:11px;white-space:normal;color:#abb7c7}
.ad-glass .h3-player .screen,.ad-glass .h3-player.is-empty .screen{aspect-ratio:21/9!important;height:auto!important;min-height:0!important;max-height:none!important;flex:none;box-sizing:border-box;background:#232f3e;border:1px solid #444f63;border-radius:8px;overflow:hidden}
.ad-glass .h3-player .screen video{position:absolute;inset:0}
.ad-glass .h3-player .ph{background:#222d3e;color:#adb8c8;font-size:13px}
.ad-glass [data-glass-image]{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block}
.ad-glass [data-glass-audio]{position:absolute;top:40%;left:5%;width:90%}
.ad-glass .h3-tlbar{flex-wrap:wrap!important;overflow:visible!important;gap:8px!important;padding:2px 0;align-items:flex-start}
.ad-glass .h3-tlbar>.h3-group{min-height:34px;box-sizing:border-box;padding-top:0;padding-bottom:0}
.ad-glass .h3-tlbar .h3-group{flex-wrap:wrap!important;border-color:#4b586d!important;gap:5px}
.ad-glass .h3-tlbar button{background:#364251!important;border:1px solid #505f77!important;color:#e4ebf4!important;border-radius:7px}
.ad-glass .h3-tlbar button.on{background:#274c79!important;color:#f0f6ff!important}
.ad-glass .h3-glass-inspector{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:7px;margin-left:auto;flex:1 1 360px;min-width:0;font-size:12px}
.ad-glass .h3-inline-segment-controls{flex:1 1 100%;min-width:0}
.ad-glass .h3-inspector-meta{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:nowrap;min-width:0;line-height:1.5;padding:7px 10px;border:1px solid #44556a;border-radius:8px;background:#293546}
.ad-glass .h3-inspector-meta [data-recipe-preview]{display:none}
.ad-glass .h3-inspector-name{font-size:11px;color:#c4d0df;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;max-width:100%;flex:0 1 220px}
.ad-glass .h3-inspector-selection{min-width:0;max-width:100%;flex:1 1 140px}
.ad-glass .h3-inspector-selection:empty{display:none}
.ad-glass .h3-selection-summary{font-size:11px!important;line-height:1.5;color:#e5edf7!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:transparent!important;border:0!important;padding:0!important}
.ad-glass .h3-split-group{display:flex!important;flex-direction:row;align-items:center!important;justify-content:flex-end;flex-wrap:wrap!important;gap:6px!important;padding:0!important;border:0!important}
.ad-glass .h3-split-group label{font-size:11px!important;display:inline-flex!important;align-items:center;white-space:nowrap!important;gap:5px;color:#dae2ed}
.ad-glass .h3-split-group input{width:55px!important;min-height:30px;height:30px;padding:0 6px!important}
.ad-glass .h3-unit button{background:#3f4c60!important;color:#d9e2ee!important;border:1px solid #56677e!important;min-height:28px;height:28px;font-size:12px}
.ad-glass .h3-unit button.on{background:#376496!important;color:#f0f7ff!important}
.ad-glass [data-apply-split]{margin:0;height:30px!important;border-radius:7px!important;font-size:11px}
.ad-glass .h3-recipe-meta{font-size:11px;line-height:1.6;display:flex;flex-wrap:wrap;gap:6px;color:#b5c3d6;min-width:0;max-width:100%}
.ad-glass .h3-recipe-preview{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-glass .h3-glass-library{display:grid;gap:10px;padding:12px!important}
.ad-glass .h3-library-title{display:flex;justify-content:space-between;align-items:center;gap:12px}
.ad-glass .h3-library-title>button[data-clear-images]{padding:4px 12px;min-height:26px;font-size:12px;color:#e5edf7;background:#334155;border:1px solid #475569;border-radius:6px;cursor:pointer}
.ad-glass .h3-library-title>button[data-clear-images]:hover{background:#475569}
.ad-glass .h3-library-title>[data-load=image]{padding:6px 15px;min-height:32px;color:#f0f7ff;background:#385f92;border-color:#5880b2}
.ad-glass .h3-pool,.ad-glass .h3-pool.is-empty{border:0!important;background:transparent!important;border-radius:0!important;display:flex!important;flex-direction:row!important;flex-wrap:nowrap!important;gap:10px!important;padding:2px!important;min-height:0!important;max-height:none!important;overflow-x:auto!important;overflow-y:hidden!important;align-items:flex-start!important;justify-content:flex-start!important}
.ad-glass .h3-shot{position:relative!important;width:132px!important;flex:0 0 132px!important;min-width:132px!important;max-width:132px!important;border:1px solid #4f5f78!important;background:#354051!important;border-radius:9px!important;overflow:hidden!important;padding:5px!important;box-sizing:border-box;align-self:start}
.ad-glass .h3-shot.is-preview{outline:1px solid #9abef3;background:#364f72!important}
.ad-glass .h3-shot img{height:84px!important;max-height:84px!important;width:100%!important;object-fit:contain!important;display:block;background:#232e3e;border-radius:5px;cursor:pointer;font-size:0}
.ad-glass .h3-shot select{width:100%;min-width:0;font-size:11px;min-height:29px;height:29px;padding:2px 3px;border:1px solid #4c5e79!important;background:#29384e!important;border-radius:5px!important;color:#e5eef7}
.ad-glass .h3-source-remove{position:absolute!important;top:7px!important;right:7px!important;width:22px!important;height:22px!important;min-height:22px!important;padding:0!important;background:#23354fe6!important;border:1px solid #98a6ba!important;color:#f0f6ff!important;border-radius:50%!important;font-size:14px!important}
.ad-glass .h3-shot-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;line-height:1.5;padding:6px 1px;color:#d1dceb}
.ad-glass .h3-image-add{flex:0 0 132px!important;width:132px!important;min-width:132px!important;height:152px!important;border:1px dashed #6a84a8!important;border-radius:9px;background:#27374e!important;color:#ddeafc!important;font-size:12px;white-space:normal;display:flex;align-items:center;justify-content:center;box-sizing:border-box}
.ad-glass .h3-pool-empty{color:#adbbcd!important;font-size:12px!important;line-height:1.6;padding:18px 0!important;text-align:left!important;width:auto!important}
.ad-glass .h3-thumb-unavailable{height:84px!important;width:100%;font-size:11px!important;color:#c1ccdc!important;background:#253346!important;padding:4px!important}
.ad-glass .h3-view{background:#242e3d!important;border:1px solid #4a5a72!important;border-radius:10px!important;max-height:490px}
.ad-glass .h3-ruler{background:#313f53!important;border-color:#4a5a72!important}.ad-glass .h3-head{background:#2e3d53!important;border-color:#455874!important}
.ad-glass .h3-lane{border-color:#3a4c65!important}.ad-glass .h3-tick span{color:#aebcce!important}
.ad-glass .h3-clip.v{background:#3d5e8b!important;border-color:#7a95ba!important}.ad-glass .h3-clip.a{background:#285955!important;border-color:#538e88!important}
.ad-glass .h3-clip.selected,.ad-glass .h3-generation-clip.selected{border-color:#accdff!important;box-shadow:0 0 0 1px #99bff2!important}
.ad-glass .h3-generation-clip{background:#324e73!important;border-color:#496e9b!important;color:#dfebfa!important}
.ad-glass .h3-smart-row{flex-wrap:wrap!important;gap:8px;font-size:12px;padding:8px;border:1px solid #4b5b74;border-radius:9px;background:#2e3b4e}
.ad-glass .h3-smart-row select,.ad-glass .h3-smart-row input{background:#38475d;color:#f0f4fa;border-color:#536682;font-size:12px}
.ad-glass .h3-smart-row input[type=checkbox]{width:14px;height:14px;min-height:14px}.ad-glass .h3-smart-info{color:#e0b871;font-size:11px;white-space:normal}
.ad-glass .h3-smartcut{background:#d2a54f}.ad-glass .h3-smartcut:before{background:#d2a54f}
.ad-glass .h3-transport{margin-top:-4px;gap:8px}.ad-glass .h3-clock{color:#dfe9f6;font-size:12px}
.ad-glass .h3-frame-plan{border-color:#485973!important;background:#29364a!important;border-radius:8px!important}.ad-glass .h3-frame-toggle{border-radius:7px!important;font-size:11px!important}
.aiden-h3.ad-h3-video .ad-h3-screen{background:#1e2c42;border:1px solid #495f7f;border-radius:10px;overflow:hidden}
.aiden-h3.ad-h3-video .ad-h3-tabs{border-bottom:1px solid #435876}.aiden-h3.ad-h3-video .ad-h3-tabs button.active{background:#325c93;color:#f0f7ff;border-color:#80a0cc}
.aiden-h3.ad-h3-video .ad-h3-media-list{background:#223044!important;border-color:#465a75!important}.aiden-h3 .ad-h3-media-list::-webkit-scrollbar-thumb{background:#576f92!important}
.aiden-h3.ad-h3-video .ad-h3-media-card{background:#2d405b!important;border-color:#4a658a!important;box-shadow:none!important}
.aiden-h3.ad-h3-video .ad-h3-media-serial{background:#2f4d73!important;color:#eef5fe!important;border-color:#4f7098!important}
.aiden-h3 .ad-h3-preview-thumb.selected{background:#355d91!important;border-color:#a8c9f9!important}
.aiden-h3 .h3-status,.aiden-h3 .h3-settings{background:#28374c;border-radius:9px}.aiden-h3 .h3-message{color:#cee0f7}
.ad-h3-prompt-area,.ad-h3-prefix-editor input{background:#242f41!important;border:1px solid #516583!important;color:#f3f7fc!important;border-radius:8px!important}
.ad-h3-prompt-area::placeholder,.ad-h3-prefix-editor input::placeholder{color:#a4b2c6!important}.ad-h3-prompt-hint{color:#adbace!important}.ad-h3-resolution-badge{color:#d6e2f3!important}
@container (max-width:700px){
 .aiden-h3.h3-tl .h3-tl-inner{padding:12px}
 .ad-glass .h3-glass-inspector{flex-basis:100%;margin-left:0}.ad-glass .h3-inspector-meta,.ad-glass .h3-split-group{justify-content:flex-start}
 .ad-glass .h3-load-row>button{flex:1 1 auto;font-size:12px}.ad-glass .h3-player-head [data-player-meta]{width:100%}
 .ad-glass .h3-shot{width:120px!important;flex-basis:120px!important;min-width:120px!important;max-width:120px!important}
 .ad-glass .h3-image-add{width:120px!important;flex-basis:120px!important;min-width:120px!important}
}
.ad-glass .h3-tlbar{flex-wrap:nowrap!important;overflow-x:auto!important;overflow-y:hidden!important;align-items:center!important}
.ad-glass .h3-tlbar>.h3-group{flex:0 0 auto;flex-wrap:nowrap!important;align-items:center}
.ad-glass .h3-glass-inspector{flex:0 0 auto!important;flex-wrap:nowrap!important;align-items:center;justify-content:flex-start!important;margin-left:0!important;min-width:0}
.ad-glass .h3-tlbar [data-apply-split],.ad-glass .h3-tlbar button[data-apply-split]{background:#3B7DC4!important;border:1px solid #5b8cc5!important;color:#fff!important;font-weight:700!important}
.ad-glass .h3-tlbar [data-apply-split]:hover{background:#326BA8!important}
.ad-glass .h3-inline-segment-controls{flex:0 0 auto!important;min-width:0}
.ad-glass .h3-split-group{flex-wrap:nowrap!important;align-items:center!important;justify-content:flex-start;gap:5px!important}
.ad-glass .h3-split-group input{width:46px!important;min-height:26px;height:26px}
.ad-glass .h3-split-group label{font-size:11px!important;gap:4px}
.ad-glass .h3-max-frames-input{width:50px!important}
.ad-glass .h3-split-group .h3-unit button{min-height:26px;height:26px;padding:0 5px}
.ad-glass [data-apply-split]{height:26px!important;padding:0 8px!important}
.ad-glass .h3-inspector-meta{justify-content:flex-start!important;text-align:left;gap:16px!important;flex-wrap:nowrap!important;box-sizing:border-box;max-width:100%;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin}
.ad-glass .h3-inspector-name,.ad-glass .h3-inspector-selection,.ad-glass .h3-inspector-frames{flex:0 0 auto;min-width:0;width:auto;max-width:none;text-align:left}
.ad-glass .h3-inspector-name,.ad-glass .h3-inspector-selection .h3-selection-summary{overflow:visible!important;text-overflow:clip!important;white-space:nowrap!important;font-size:12px!important;text-align:left!important;line-height:1.6}
.ad-glass .h3-frame-summary{display:flex;flex-wrap:nowrap;gap:16px;font-size:12px;line-height:1.6;font-variant-numeric:tabular-nums;color:#dceaff;white-space:nowrap}
.ad-glass .h3-frame-summary span{white-space:nowrap}
.ad-glass .h3-inspector-meta .h3-recipe-meta{display:none!important}
.ad-glass .h3-recipe-meta{justify-content:center!important;text-align:center}
.ad-h3-tabs .ad-h3-preview-tools{position:static!important;right:auto!important;bottom:auto!important;margin-left:auto!important;z-index:auto;background:transparent!important;border:0!important;border-radius:0!important;padding:0!important;backdrop-filter:none!important;display:flex;gap:8px;align-items:center;flex:0 0 auto}
.ad-h3-tabs .ad-h3-preview-tools button{min-height:26px;height:26px;font-size:12px;padding:0 10px}
.ad-h3-preview-tools .ad-h3-ab-slot{font-weight:700;min-width:28px;padding:0 8px}
.ad-h3-preview-tools .ad-h3-ab-slot.active{background:#376496!important;border-color:#a6c9ff!important;color:#f0f7ff!important}
.ad-h3-screen{position:relative!important}
.ad-h3-ab-overlay{position:absolute!important;inset:0!important;top:0!important;left:0!important;right:0!important;bottom:0!important;
 width:100%!important;height:100%!important;aspect-ratio:auto!important;max-height:none!important;margin:0!important;z-index:25;background:#202c41;
 border:0;border-radius:8px;overflow:hidden;touch-action:none;pointer-events:auto!important;cursor:ew-resize}
.ad-h3-ab-overlay *{pointer-events:auto}
.ad-h3-ab-overlay img,.ad-h3-ab-overlay .ad-h3-ab-tag{pointer-events:none!important}
.ad-h3-ab-layer{position:absolute;inset:0;overflow:hidden}
.ad-h3-ab-layer img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none}
.ad-h3-ab-divider{position:absolute;top:0;bottom:0;width:1px;left:50%;background:rgba(255,255,255,.5);box-shadow:none;pointer-events:none;transform:translateX(-0.5px);will-change:left}
.ad-h3-preview-layout.is-comparing .ad-h3-screen > *:not(.ad-h3-ab-overlay){visibility:hidden!important}
.ad-h3-preview-layout.is-comparing .ad-h3-player-controls,.ad-h3-preview-layout.is-comparing .ad-h3-preview-title{display:none!important}
.ad-h3-preview-layout.is-comparing .ad-h3-screen > .ad-h3-ab-overlay{visibility:visible!important}
.ad-h3-ab-exit{position:absolute;top:auto!important;bottom:8px;right:8px;z-index:8;min-height:26px;height:26px;padding:0 12px;font-size:12px;border-radius:6px;background:#23354fe6!important;border:1px solid #98a6ba!important;color:#f0f6ff!important;cursor:pointer;transform:scale(.65);transform-origin:right bottom}
.ad-h3-ab-exit:hover{background:#324969!important}
.ad-h3-ab-fullscreen{position:absolute;top:auto!important;bottom:8px;right:104px;z-index:8;min-height:26px;height:26px;padding:0 12px;font-size:12px;border-radius:6px;background:#23354fe6!important;border:1px solid #98a6ba!important;color:#f0f6ff!important;cursor:pointer;transform:scale(.65);transform-origin:right bottom}
.ad-h3-ab-fullscreen:hover{background:#324969!important}
.ad-h3-ab-overlay:fullscreen{width:100vw!important;height:100vh!important;aspect-ratio:auto!important;max-height:none!important;border-radius:0!important;background:#101826!important}
.ad-h3-ab-overlay:fullscreen .ad-h3-ab-layer img{object-fit:contain}
.ad-h3-ab-overlay:fullscreen .ad-h3-ab-fullscreen,.ad-h3-ab-overlay:fullscreen .ad-h3-ab-exit{transform:none;bottom:16px;font-size:13px;height:32px;min-height:32px}
.ad-h3-ab-overlay:fullscreen .ad-h3-ab-fullscreen{right:150px}
.ad-h3-ab-overlay:fullscreen .ad-h3-ab-exit{right:24px}
.ad-h3-ab-tag{position:absolute;top:8px;max-width:40%;padding:1px 6px;border-radius:4px;background:#000a;color:#fff;font:9px system-ui;z-index:4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center}
.ad-h3-ab-tag.on{background:#376496!important;border:1px solid #a6c9ff;color:#fff}
.ad-h3-ab-videos{position:absolute;inset:0 0 46px 0;display:grid;grid-template-columns:1fr 1fr;gap:6px;background:#202c41;z-index:1}
.ad-h3-ab-audios{position:absolute;inset:0;display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:12px;background:#202c41;z-index:2;box-sizing:border-box}
.ad-h3-ab-acell{display:flex;align-items:center;justify-content:center;background:#202d41;border:1px solid #3a4c65;border-radius:8px;padding:10px}
.ad-h3-ab-acell audio{width:100%}
.ad-h3-ab-mode{position:absolute;top:auto!important;left:auto!important;bottom:8px;right:76px;z-index:8;min-height:26px;height:26px;padding:0 10px;font-size:12px;border-radius:6px;background:#23354fe6!important;border:1px solid #98a6ba!important;color:#f0f6ff!important;cursor:pointer;transform:scale(.65)!important;transform-origin:right bottom}
.ad-h3-ab-vcell{position:relative;background:#1b2b46;overflow:hidden;border:1px solid #3a4c65;border-radius:6px}
.ad-h3-ab-vcell video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#1b2b46}
.ad-h3-ab-controls{position:absolute;left:0;right:0;bottom:0;height:44px;display:flex;align-items:center;gap:6px;padding:6px 100px 6px 8px;background:#202d41e6;border-top:1px solid #3a4c65;z-index:3;box-sizing:border-box}
.ad-h3-ab-controls input[type=range]{flex:1;min-width:80px}
.ad-h3-ab-controls button{min-height:26px;height:26px;font-size:12px;padding:0 8px}
.ad-h3-ab-controls [data-ab-time]{font-size:11px;color:#dfe9f6;white-space:nowrap}
.ad-h3-ab-controls [data-ab-audio]{font-weight:700}
.ad-h3-ab-controls [data-ab-audio].on{background:#376496!important;border-color:#a6c9ff!important;color:#f0f7ff!important}
.ad-glass .h3-load-row{flex-wrap:nowrap!important;align-items:center!important;gap:6px!important;overflow-x:auto!important;overflow-y:hidden!important}
.ad-glass .h3-load-row>button{flex:0 0 auto}
.ad-glass .h3-folder-btn{background:#3B7DC4!important;border:1px solid #5b8cc5!important;color:#fff!important;font-weight:600!important}
.ad-glass .h3-folder-btn:hover{background:#326BA8!important}
.ad-glass .h3-load-row .h3-path-field{flex:1 1 240px!important;min-width:200px!important;margin:0!important}
.ad-glass .h3-load-row .h3-path-field input{min-width:110px}
.ad-glass .h3-tlbar .h3-group button{height:30px;min-height:30px;display:inline-flex;align-items:center;justify-content:center;font-size:13px}
.ad-glass .h3-split-group{height:30px;align-items:center!important;font-size:13px!important}
.ad-glass .h3-split-group label{font-size:13px!important;color:#ebf0f6!important;gap:6px!important;height:30px;align-items:center}
.ad-glass .h3-split-group input{height:30px!important;min-height:30px!important;background:#28364c!important;border:1px solid #446590!important;border-radius:7px!important;color:#f2f6fd!important;font-size:13px!important;padding:0 8px!important;width:54px!important;box-sizing:border-box}
.ad-glass .h3-split-group [data-count]{width:46px!important}
.ad-glass .h3-split-group .h3-unit{display:inline-flex;align-items:center;gap:2px;height:30px;background:#28364c;border:1px solid #446590;border-radius:7px;padding:1px}
.ad-glass .h3-split-group .h3-unit button{height:26px!important;min-height:26px!important;border-radius:5px!important;border:0!important;background:transparent!important;color:#bac8da!important;font-size:12px!important;padding:0 8px!important}
.ad-glass .h3-split-group .h3-unit button.on{background:#376496!important;color:#f0f7ff!important}
.ad-glass .h3-split-group .h3-max-frames-input{width:54px!important}
.ad-glass .h3-split-group [data-apply-split]{height:30px!important;min-height:30px!important}
.ad-h3-save-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px;margin-top:8px;border:1px solid #415675;border-radius:8px;background:#222f42}
.ad-h3-save-row input[type=text]{flex:1 1 170px;min-width:120px;height:28px;background:#28364c;border:1px solid #446590;border-radius:6px;color:#f2f6fd;padding:0 8px;font-size:12px}
.ad-h3-save-label{font-size:12px;color:#c4d0e1;white-space:nowrap}
.ad-h3-save-auto{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#e5eef7;white-space:nowrap}
.ad-h3-save-row button{height:28px;min-height:28px;padding:0 10px;font-size:12px}

/* 外观整理：仅调整绘制属性；保留所有布局、按钮坐标、尺寸与交互。 */
.aiden-h3{
 --ad-bg:#262e3b;--ad-surface:#2f3a4c;--ad-surface-deep:#212e40;--ad-surface-dark:#222e3f;
 --ad-border:#43536b;--ad-border-soft:#37485c;--ad-text:#f4f7fb;
 --ad-text-dim:#c3cddb;--ad-text-mute:#9cabbd;
 --ad-accent:#397bce;--ad-accent-hover:#67a1e3;--ad-accent-border:#7eace3;--ad-accent-soft:#cfe2fc;
 background:linear-gradient(160deg,#293445 0%,#262e3b 70%)!important;
 border-color:#43536b!important;border-radius:12px!important;
 box-shadow:0 4px 16px #0003,inset 0 1px 0 #f7f7f705;
}
.aiden-h3 button,.aiden-h3 select,.aiden-h3 input:not([type=range]),.aiden-h3 textarea{
 border-color:#495a73;border-radius:6px;color:#f4f7fb;
 box-shadow:inset 0 1px 0 #f7f7f704;
 transition:background-color .12s ease,border-color .12s ease,box-shadow .12s ease;
}
.aiden-h3 button:hover:not(:disabled){border-color:#637c9e;background-color:#3e5069}
.aiden-h3 button:disabled{opacity:.5}
.aiden-h3 input:not([type=range]),.aiden-h3 textarea,.aiden-h3 select{background-color:#243349}
.aiden-h3 input::placeholder,.aiden-h3 textarea::placeholder{color:#99a7bb}
.aiden-h3 button:focus-visible,.aiden-h3 input:focus-visible,.aiden-h3 select:focus-visible,.aiden-h3 textarea:focus-visible{
 outline:2px solid #9dc2f3;outline-offset:2px;
}
.aiden-h3 input[type=checkbox],.aiden-h3 input[type=range]{accent-color:#80b1eb}
.aiden-h3 button.primary,.aiden-h3 .ad-h3-loop-actions button{
 background:#397bce!important;border-color:#7caae1!important;
 border-radius:6px!important;box-shadow:inset 0 1px 0 #f7f7f712!important;
}
.aiden-h3 button.primary:hover:not(:disabled),.aiden-h3 .ad-h3-loop-actions button:hover:not(:disabled){background:#67a1e3!important}
.ad-glass.aiden-h3 .h3-path-field,.ad-glass.aiden-h3 .h3-inspector-meta,.ad-glass.aiden-h3 .h3-smart-row,.ad-h3-video.aiden-h3 .ad-h3-save-row{
 background:#2a374b!important;border-color:#41536e!important;border-radius:8px;
 box-shadow:inset 0 1px 0 #f7f7f703;
}
.ad-glass.aiden-h3 .h3-path-field input{background:#1e2e44!important;color:#f3f7fc!important}
.ad-glass.aiden-h3 .h3-path-field button,.ad-glass.aiden-h3 .h3-tlbar button{
 background:#34445d!important;border-color:#4a5e7b!important;color:#e9eff7!important;border-radius:6px;
}
.ad-glass.aiden-h3 .h3-tlbar button.on,.ad-glass.aiden-h3 .h3-unit button.on{
 background:#315b8d!important;color:#f0f7ff!important;border-color:#789dc9!important;
}
.ad-glass.aiden-h3 .h3-player,.ad-glass.aiden-h3 .h3-glass-library{
 background:#263447!important;border-color:#42546e!important;border-radius:10px!important;
 box-shadow:inset 0 1px 0 #f7f7f704;
}
.ad-glass.aiden-h3 h3{color:#f3f7fc}
.ad-glass.aiden-h3 .h3-player-head{color:#e7edf6}
.ad-glass.aiden-h3 .h3-player-head [data-player-meta]{color:#aebcd1}
.ad-glass.aiden-h3 .h3-player .screen,.ad-glass.aiden-h3 .h3-player.is-empty .screen{
 background:#1d2d44;border-color:#3e506a;border-radius:7px;
}
.ad-glass.aiden-h3 .h3-split-group input,.ad-glass.aiden-h3 .h3-smart-row input,.ad-glass.aiden-h3 .h3-smart-row select{
 background:#20314c!important;border-color:#465d7d!important;color:#f2f6fd!important;
}
/* 保持时间线原有尺寸，避免通用 .aiden-h3 样式晚加载时把按钮向右挤。 */
.ad-glass.aiden-h3 .h3-smart-row input[type=number]{width:58px}
.ad-glass.aiden-h3 .h3-smart-row select,.ad-glass.aiden-h3 .h3-smart-row button{padding:0 8px}
.ad-glass.aiden-h3 .h3-view{background:#1f2d42!important;border-color:#425673!important}
.ad-glass.aiden-h3 .h3-ruler,.ad-glass.aiden-h3 .h3-head{background:#2d3f58!important;border-color:#435978!important}
.ad-glass.aiden-h3 .h3-lane{border-color:#364d6c!important}
.ad-glass.aiden-h3 .h3-tick span{color:#beccdf!important}
.ad-glass.aiden-h3 .h3-clip.selected,.ad-glass.aiden-h3 .h3-generation-clip.selected{
 border-color:#b3d2f9!important;box-shadow:0 0 0 1px #8bb1e1!important;
}
.ad-glass.aiden-h3 .h3-shot{background:#2d3f57!important;border-color:#465b78!important;border-radius:7px!important}
.ad-glass.aiden-h3 .h3-shot.is-preview{background:#355072!important;outline-color:#b1d2f8}
.ad-glass.aiden-h3 .h3-shot img{background:#1d2c44;border-radius:4px}
.ad-glass.aiden-h3 .h3-shot select{background:#20324f!important;border-color:#435978!important;color:#e8eff8!important;border-radius:5px!important}
.ad-glass.aiden-h3 .h3-shot-name{color:#c8d4e5}
.ad-glass.aiden-h3 .h3-image-add{background:#23354f!important;border-color:#4f719d!important;color:#ccdef4!important;border-radius:7px}
.ad-glass.aiden-h3 .h3-source-remove{background:#1c2f46e6!important;border-color:#7189a8!important}
.ad-glass.aiden-h3 .h3-source-remove:hover{background:#853d4b!important;border-color:#c27482!important}
.ad-h3-video.aiden-h3 .ad-h3-screen{background:#1d2d44;border-color:#42546e;border-radius:8px}
.ad-h3-video.aiden-h3 .ad-h3-media-list{background:#1d2e45;border-color:#415675;box-shadow:inset 0 1px 4px #0003;border-radius:8px}
.ad-h3-video.aiden-h3 .ad-h3-panel .ad-h3-media-list{height:auto!important;min-height:0!important;max-height:none!important;overflow-y:auto!important}
.ad-h3-video.aiden-h3 .ad-h3-screen>.ad-h3-player-controls{position:absolute;left:0;right:0;bottom:0;z-index:7;display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin:0;padding:7px;background:#1b283ceF;border-top:1px solid #465b78;box-sizing:border-box}
.ad-h3-video.aiden-h3 .ad-h3-player-controls [data-seek]{flex:1 1 90px;min-width:50px;width:auto}
.ad-h3-video.aiden-h3.ad-h3-audio-mode [data-frame]{display:none}
.ad-h3-video.aiden-h3 .ad-h3-strip-row{min-height:0!important;height:auto!important;padding:8px}
.ad-h3-video.aiden-h3 .ad-h3-segments,.ad-h3-video.aiden-h3 .ad-h3-merges{min-height:0!important;align-items:flex-start;display:flex;flex-wrap:wrap!important;align-content:flex-start;overflow-x:hidden!important;overflow-y:visible!important}
.ad-h3-video.aiden-h3 button.ad-h3-preview-thumb{box-sizing:content-box!important;flex:0 0 160px!important;width:160px!important;min-width:160px!important;max-width:160px!important;height:auto!important;min-height:0!important;max-height:none!important;padding:4px!important;display:flex!important;flex-direction:column;overflow:hidden}
.ad-h3-video.aiden-h3 button.ad-h3-preview-thumb>img,.ad-h3-video.aiden-h3 button.ad-h3-preview-thumb>video{width:160px!important;height:90px!important;min-height:90px!important;max-height:90px!important;aspect-ratio:16/9;object-fit:contain;flex:0 0 90px}
.ad-h3-video.aiden-h3 button.ad-h3-preview-thumb>span{display:block;line-height:22px;min-height:22px;width:100%;text-align:center}
.ad-h3-video.aiden-h3 .ad-h3-media-card{background:#2d3f57;border-color:#465b78;border-radius:7px}
.ad-h3-video.aiden-h3 .ad-h3-media-serial{background:#253954;color:#e2ebf6;border-top-color:#415c82}
.ad-h3-video.aiden-h3 .ad-h3-media-card.selected{
 outline:2px solid #9dc3f4!important;outline-offset:-2px!important;
 border-color:#9dc3f4!important;background:#315178!important;box-shadow:0 0 0 1px #7ba1d0!important;
}
.ad-h3-video.aiden-h3 .ad-h3-tabs button{background:#293a53;border-color:#435c79;color:#ccd7e6;border-radius:6px}
.ad-h3-video.aiden-h3 .ad-h3-tabs button.active{background:#325d8e;border-color:#7a9dc7;color:#f0f6ff}
.ad-h3-video.aiden-h3 .ad-h3-preview-tool,.ad-h3-video.aiden-h3 .ad-h3-preview-download{border-radius:6px;box-shadow:inset 0 1px 0 #f7f7f712}
.ad-h3-video.aiden-h3 .ad-h3-tab-hint{color:#c6d5e8;background:#253c5b;border-color:#435f84;border-radius:6px}
.ad-h3-video.aiden-h3 .ad-h3-tab-hint.error{color:#ffb3bd;background:#39202b;border-color:#84495a}
.ad-h3-prompt-area,.ad-h3-prefix-editor input{
 background:#1f2f44!important;color:#f3f7fc!important;border-color:#495e7d!important;border-radius:7px!important;
 box-shadow:inset 0 1px 3px #0002;
}
.ad-h3-prompt-area::placeholder,.ad-h3-prefix-editor input::placeholder{color:#9babc2!important}
.ad-h3-resolution-badge{color:#e7f0fc}
.ad-h3-prompt-hint{color:#acbdd5}
/* 禁用操作保持可辨识，同时提高灰字可读性。 */
.ad-glass.aiden-h3.h3-tl .h3-tlbar button[data-apply-split]{background:#3f83cf!important;border-color:#82b5ef!important;color:#fff!important}
.ad-glass.aiden-h3.h3-tl .h3-tlbar button[data-apply-split]:hover{background:#4b91dd!important}
.ad-glass .h3-storage-panel{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:10px;padding:12px;border:1px solid #465b78;border-radius:7px;background:#26364b;color:#e6edf8;font-size:14px;box-sizing:border-box;flex:0 0 auto;width:100%}
.ad-glass .h3-storage-summary{display:flex;flex:1 1 350px;flex-wrap:wrap;gap:8px 16px;min-width:0;line-height:1.6}
.ad-glass .h3-storage-panel [data-storage-used]{font-weight:600}
.ad-glass .h3-storage-panel [data-storage-message]{flex-basis:100%;line-height:1.5;overflow-wrap:anywhere}
.ad-glass .h3-storage-panel [data-storage-clean]{flex:0 0 auto;white-space:nowrap}
.ad-glass .h3-storage-panel [data-storage-clear-sources]{flex:0 0 auto;white-space:nowrap;border-color:#b4544f!important;color:#ffd9d6!important}
.ad-glass .h3-storage-panel.is-warning,.ad-glass.h3-storage-over-limit .h3-load-row{background:#574624!important;border-color:#c6a04f!important;color:#fff0c5!important}
.aiden-h3.h3-tl button:disabled,.aiden-h3.ad-h3-video button:disabled{opacity:.62}
@media(prefers-reduced-motion:reduce){.aiden-h3 button,.aiden-h3 input,.aiden-h3 select,.aiden-h3 textarea{transition:none}}
`;
export function installGlassTheme() {
    if (document.getElementById(ID)) return;
    const style = document.createElement("style"); style.id=ID; style.textContent=CSS; document.head.append(style);
}
export function mountGlassTimeline(root) {
    installGlassTheme(); root.classList.add("ad-glass"); root.dataset.glassKind="image";
    const inner=root.querySelector(".h3-tl-inner"),load=inner.querySelector(".h3-load-row");
    const header=document.createElement("div"); header.className="h3-glass-top";
    header.hidden=true;
    header.innerHTML='<div><div class="h3-glass-title">素材清单库</div><div class="h3-glass-subtitle">AD-Segmented-Loop</div></div><span class="h3-glass-badge">分段清单 · <b data-glass-count>0</b> 段</span>';
    inner.prepend(header);
    const pathRow=root.querySelector("[data-path-field]");
    pathRow.querySelectorAll("[data-library]").forEach(button=>button.remove());
    for(const [mode,label] of [["input","素材目录"],["generated","生成目录"]]){
        const button=document.createElement("button");
        button.type="button";button.textContent=label;button.dataset.libraryPick=mode;
        button.className="h3-folder-btn";button.title="打开文件选择框，选完直接载入（可多选）";
        load.append(button);
    }
    load.append(pathRow);  // 路径输入并在同一排
    const workspace=document.createElement("div"); workspace.className="h3-glass-workspace";
    const library=document.createElement("section"); library.className="h3-glass-library"; library.setAttribute("aria-label","参考图片");
    library.innerHTML='<div class="h3-library-title"><h3>参考图片</h3><button type="button" class="h3-image-clear" data-clear-images title="清空参考图片内的全部素材（不影响视频/音频轨）">清空</button></div>';
    library.append(root.querySelector("[data-pool]"));
    const player=root.querySelector("[data-player]");
    const inspector=document.createElement("aside"); inspector.className="h3-glass-inspector"; inspector.setAttribute("aria-label","素材与分段设置");
    inspector.innerHTML='<div class="h3-inline-segment-controls"></div><div class="h3-inspector-meta"><div class="h3-inspector-name" data-glass-source>素材信息：未选择素材</div><div class="h3-inspector-selection"></div><div class="h3-inspector-frames"></div></div>';
    inspector.querySelector(".h3-inspector-selection").append(root.querySelector("[data-selection-summary]"));
    inspector.querySelector(".h3-inspector-frames").append(root.querySelector("[data-frame-summary]"));
    inspector.querySelector(".h3-inline-segment-controls").append(root.querySelector(".h3-split-group"));
    inspector.querySelector(".h3-inspector-meta").append(root.querySelector(".h3-recipe-meta"));
    workspace.append(player); load.after(workspace);
    root.querySelector(".h3-tlbar").append(inspector);
    root.querySelector(".h3-transport").after(inspector.querySelector(".h3-inspector-meta"));
    root.querySelector("[data-frame-plan]").before(library);
    const screen=player.querySelector(".screen");
    const image=document.createElement("img");image.dataset.glassImage="";image.alt="素材预览";image.hidden=true;
    const audio=document.createElement("audio");audio.dataset.glassAudio="";audio.controls=true;audio.preload="metadata";audio.muted=true;audio.hidden=true;screen.append(image,audio);
}
export function matchesSource(root, source) {
    const kind=root.dataset.glassKind||"all", query=root.querySelector(".h3-library-search")?.value.trim().toLowerCase()||"";
    return (kind==="all"||source.kind===kind)&&String(source.name||"").toLowerCase().includes(query);
}
export function clearSourcePreview(root) {
    if(!root._adSourcePreviewId) return;
    const image=root.querySelector("[data-glass-image]"),audio=root.querySelector("[data-glass-audio]");
    if(image){image.hidden=true;image.removeAttribute("src");}
    if(audio){audio.pause();audio.hidden=true;audio.removeAttribute("src");audio.load();}
    const video=root.querySelector("[data-player-video]");if(video){video.hidden=false;video.controls=false;}
    delete root._adSourcePreviewId;
}
export function previewSource(root,source,url) {
    clearSourcePreview(root);root._adSourcePreviewId=source.id;
    const video=root.querySelector("[data-player-video]"),image=root.querySelector("[data-glass-image]"),audio=root.querySelector("[data-glass-audio]");
    video.pause();video.hidden=true;
    root.querySelector(".ph").style.display="none";
    root.querySelector("[data-player-title]").textContent=source.name||"素材预览";
    root.querySelector("[data-player-meta]").textContent={image:"原图预览",video:"原视频预览",audio:"音频预览"}[source.kind]||"";
    const sourceInfo=root.querySelector("[data-glass-source]");
    sourceInfo.textContent=source.name||"未命名素材";sourceInfo.title=source.name||"未命名素材";
    if(source.kind==="image"){image.src=url;image.hidden=false;}
    else if(source.kind==="audio"){audio.src=url;audio.hidden=false;}
    else{video.hidden=false;video.controls=true;video.muted=true;video.src=url;video.dataset.src=url;}
}
