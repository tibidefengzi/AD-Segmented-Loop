import {resultAliases,sameResult,mergeResult,redrawKey,redrawBase,recordLabel,nextRedrawCount,normalizeRedrawCounts,identityFields} from "./preview_identity.js";
import { bindWidgetWidth, fitPreviewContent, MIN_NODE_WIDTH } from "./dom_layout.js";
import { retainedHistoryRounds, previewHistoryGroups } from "./preview_history.js";
import { interruptOwnedPrompt } from "./queue_runner.js";
// 0.4.53：分段预览播放框与「AD-最终保存」一致（原生播放条），仅额外保留上一帧/下一帧。
// 样式只注入一次（模块被重复求值时不会插入多份）。
const frameStylesId = "ad-h3-native-player-frame-styles";
if (!document.getElementById(frameStylesId)) {
const frameStyles=document.createElement("style");
frameStyles.id=frameStylesId;
frameStyles.textContent=`
.ad-h3-video.aiden-h3 .ad-h3-screen>.ad-h3-player-controls.ad-h3-native-controls-hidden{display:none!important}
.ad-h3-video.aiden-h3 .ad-h3-screen video{width:100%;height:100%;object-fit:contain;background:#08090c}
.ad-h3-video.aiden-h3 .ad-h3-screen audio{width:88%}
.ad-h3-video.aiden-h3 .ad-h3-frame-step{position:absolute;top:8px;right:8px;bottom:auto;z-index:8;display:flex;gap:6px}
.ad-h3-video.aiden-h3 .ad-h3-frame-step button{min-height:26px;height:26px;padding:0 10px;font-size:12px;border-radius:6px;border:1px solid #4a658a;background:#1b283ce6;color:#e8f0fb;cursor:pointer}
.ad-h3-video.aiden-h3 .ad-h3-frame-step button:hover{background:#2d405b}
.ad-h3-video.aiden-h3.ad-h3-audio-mode .ad-h3-frame-step,.ad-h3-preview-layout.is-comparing .ad-h3-frame-step,.ad-h3-video.aiden-h3 .ad-h3-frame-step[hidden]{display:none!important}
`;
document.head.append(frameStyles);
}
// 所有入口共用进入/退出切换；状态以浏览器fullscreenElement为准（包括Esc退出）。
export async function togglePlayerFullscreen(target) {
    const doc = target.ownerDocument || document;
    if (doc.fullscreenElement) {
        await doc.exitFullscreen();
    } else if (target.requestFullscreen) {
        await target.requestFullscreen();
    } else {
        throw new Error("当前浏览器不支持全屏预览");
    }
}
// 独立播放器。播放选择只存在于此处，绝不修改生成游标或分段选用版本。
export function createNativePlayer(node, root, mediaUrl, thumbnailUrl, api = {fetchApi:(...args)=>fetch(...args)}) {
    root.classList.add("aiden-h3", "ad-h3-video");
    root.innerHTML = `<div class="ad-h3-preview-layout"><section class="ad-h3-screen"><div class="ad-h3-preview-title">生成完成后在这里预览素材</div><video playsinline preload="metadata" aria-label="时间线预览"></video><img data-media-image hidden alt="图像预览"><audio data-media-audio controls hidden></audio><div class="ad-h3-idle-hint"><span>留白，亦是序章</span><span>下一帧，属于你的想象</span></div></section><div class="ad-h3-tabs"><button type="button" data-tab="image">图片</button><button type="button" data-tab="audio">音频</button><button type="button" data-tab="video" class="active">视频</button><span class="ad-h3-tab-hint" data-tab-hint hidden></span></div><div class="ad-h3-loop-actions"><button type="button" data-loop="start">开始</button><button type="button" data-loop="pause">停止</button><button type="button" data-loop="auto">分段循环</button><button type="button" data-loop="redo" title="选中下方任意预览后只重抽该段；运行中请先点「停止」">本段抽卡</button><button type="button" data-clear-preview title="只清空本轮生成进度与选用记录（预览内容与文件保留）">清空重置</button><button type="button" data-clear-media-preview title="只清空当前分类的预览内容（不删除文件、不影响进度）">清空预览</button><div class="ad-h3-loop-hint" data-loop-hint hidden></div><span class="ad-h3-video-actions ad-h3-merge-wrap"><button type="button" data-loop="merge" title="点击选择 A合并或 B合并；Ctrl 点选片段可只合并选中内容">合并片段</button><span class="ad-h3-merge-menu" data-merge-menu hidden><button type="button" data-merge-source="A">A合并</button><button type="button" data-merge-source="B">B合并</button></span></span><button type="button" class="ad-h3-playlist-toggle" data-loop-playlist aria-pressed="false" title="按分段编号循环播放预览视频">循环播放</button></div><div class="ad-h3-panel" data-panel="video"><div class="ad-h3-media-list" data-video-list></div><div class="ad-h3-player-controls"><button data-play aria-label="播放或暂停">播放</button><button data-frame="-1" title="后退一帧" aria-label="后退一帧">上一帧</button><button data-frame="1" title="前进一帧" aria-label="前进一帧">下一帧</button><input data-seek type="range" min="0" max="0" step="1" value="0" aria-label="播放位置"><span data-time>0.00 / 0.00 秒</span><button data-mute aria-label="切换静音">取消静音</button><select data-rate aria-label="播放速度"><option value="0.5">0.5 倍速</option><option value="1" selected>1 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select><button data-fullscreen aria-label="全屏预览">全屏</button></div><aside class="ad-h3-strips"><div class="ad-h3-strip-row"><span>分段生成A</span><div class="ad-h3-segments"></div></div><div class="ad-h3-strip-row"><span>分段生成B</span><div class="ad-h3-segments-b"></div></div><div class="ad-h3-strip-row"><span>合并生成</span><div class="ad-h3-merges"></div></div></aside></div><div class="ad-h3-panel" data-panel="image" hidden><div class="ad-h3-media-list" data-image-list></div></div><div class="ad-h3-panel" data-panel="audio" hidden><div class="ad-h3-media-list" data-audio-list></div></div></div>`;
    const video = root.querySelector("video"), title = root.querySelector(".ad-h3-preview-title");
    const imageView = root.querySelector("[data-media-image]"), audioView = root.querySelector("[data-media-audio]");
    const layout = root.querySelector(".ad-h3-preview-layout");
    const lightboxClosers = new Set();
    const releaseMedia = media => {
        media.pause();
        if (media.hasAttribute("src")) { media.removeAttribute("src"); media.load(); }
    };
    const transport = root.querySelector(".ad-h3-player-controls");
    root.querySelector(".ad-h3-screen").append(transport);
    audioView.controls = true;
    audioView.muted = true;
    video.controls = true;
    transport.classList.add("ad-h3-native-controls-hidden");
    const frameStep = document.createElement("div");
    frameStep.className = "ad-h3-frame-step";
    for (const button of transport.querySelectorAll("[data-frame]")) frameStep.append(button);
    root.querySelector(".ad-h3-screen").append(frameStep);
    const segments = root.querySelector(".ad-h3-segments"), segmentsB = root.querySelector(".ad-h3-segments-b"), merges = root.querySelector(".ad-h3-merges");
    // 分段生成/合并生成放在“视频”分类内（缩略图只显示视频），预览仍使用上方大窗口。
    const stripsBlock = root.querySelector(".ad-h3-strips");
    const videoPanel = root.querySelector('[data-panel="video"]');
    if (stripsBlock && videoPanel) {
        const controls = videoPanel.querySelector(".ad-h3-player-controls");
        videoPanel.insertBefore(stripsBlock, controls || null);
        const videoList = videoPanel.querySelector("[data-video-list]");
        if (videoList) videoList.hidden = true;
    }
    const seek = root.querySelector("[data-seek]"), time = root.querySelector("[data-time]");
    const play = root.querySelector("[data-play]"), mute = root.querySelector("[data-mute]");
    // 没有可播放内容时收成小占位，避免一大块黑屏和多余高度。
    const previewTools = document.createElement("div");
    previewTools.className = "ad-h3-preview-tools"; previewTools.hidden = false;
    const enlargeButton = document.createElement("button");
    enlargeButton.type = "button"; enlargeButton.className = "ad-h3-preview-tool";
    enlargeButton.textContent = "放大"; enlargeButton.title = "放大预览（滚轮缩放）";
    const previewDownload = document.createElement("button");
    previewDownload.type = "button"; previewDownload.className = "ad-h3-preview-tool";
    previewDownload.textContent = "下载"; previewDownload.title = "点击选择文件夹后保存当前预览";
    // 单个 A/B 按钮：点一下进入选择，再点下方两个素材自动对比
    const abButton = document.createElement("button");
    abButton.type = "button"; abButton.className = "ad-h3-preview-tool ad-h3-ab-slot";
    abButton.dataset.abPick = "1"; abButton.textContent = "A/B";
    abButton.title = "点一下，再依次点下方两个素材做 A/B 对比";
    previewTools.append(abButton, enlargeButton, previewDownload);
    (root.querySelector(".ad-h3-tabs") || layout).append(previewTools);
    // 保存设置：目录 + 文件名模板（随工作流保存）
    const savedConfig = (node.properties && node.properties.ad_h3_save) || {};
    const cleanSaveDir = value => String(value || "").trim().replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1").trim();
    const saveState = {dir: cleanSaveDir(savedConfig.dir), template: String(savedConfig.template || ""), onlyB: savedConfig.onlyB === undefined ? true : !!savedConfig.onlyB};
    const saveRow = document.createElement("div");
    saveRow.className = "ad-h3-save-row";
    saveRow.innerHTML = '<span class="ad-h3-save-label">保存路径</span>'
        + '<input type="text" data-save-dir placeholder="留空：ComfyUI/output/AD-Segmented-Loop-output/（按类型归档）" title="留空时按图片/音频/视频分别保存到 ComfyUI/output/AD-Segmented-Loop-output 对应子目录">'
        + '<button type="button" data-save-pick>选择目录</button>'
        + '<span class="ad-h3-save-label">文件名</span>'
        + '<input type="text" data-save-name placeholder="默认：AD-{index}-{date}">'
        + '<label class="ad-h3-save-auto"><input type="checkbox" data-save-only-b> 只保存B</label>';
    layout.insertBefore(saveRow, layout.querySelector(".ad-h3-panel"));
    const dirInput = saveRow.querySelector("[data-save-dir]");
    const nameInput = saveRow.querySelector("[data-save-name]");
    const onlyBInput = saveRow.querySelector("[data-save-only-b]");
    dirInput.value = saveState.dir; nameInput.value = saveState.template;
    if (onlyBInput) onlyBInput.checked = saveState.onlyB;
    const syncSaveConfig = () => {
        const cleanedDir = cleanSaveDir(dirInput.value);
        if (cleanedDir !== dirInput.value.trim()) dirInput.value = cleanedDir;
        saveState.dir = cleanedDir;
        saveState.template = nameInput.value.trim();
        saveState.onlyB = !!onlyBInput?.checked;
        node.properties ||= {};
        node.properties.ad_h3_save = {dir: saveState.dir, template: saveState.template, onlyB: saveState.onlyB};
        const widget = (node.widgets || []).find(item => item.name === "save_config");
        if (widget) { widget.value = JSON.stringify({dir: saveState.dir, template: saveState.template, only_b: saveState.onlyB}); try { widget.callback?.(widget.value); } catch {} }
    };
    dirInput.addEventListener("input", syncSaveConfig);
    nameInput.addEventListener("input", syncSaveConfig);
    onlyBInput?.addEventListener("change", syncSaveConfig);
    saveRow.querySelector("[data-save-pick]").addEventListener("click", async event => {
        event.stopPropagation();
        await chooseSaveFolder();
    });
    // 下载前弹出系统文件夹选择框；选中后同步到保存路径输入框。留空时按类型从生成目录起始。
    async function chooseSaveFolder() {
        try {
            const response = await api.fetchApi("/aiden_h3/pick_folder", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({initial_dir: saveState.dir, kind: active?.kind || ""})});
            const result = await response.json();
            const path = String(result.path || "").trim();
            if (path) { dirInput.value = path; syncSaveConfig(); }
            return path;
        } catch { return ""; }
    }
    const saveIndexFor = (info) => {
        for (const kind of ["image", "audio", "video"]) {
            const list = mediaRecords[kind] || [];
            const index = list.findIndex(item => item.file === info.file && (item.origin || "media") === (info.origin || "media"));
            if (index >= 0) return index + 1;
        }
        return 1;
    };
    // 下载优先弹标准 Windows「另存为」框（同截图里的打开框样式）；旧后台不支持时回退到选文件夹。
    async function saveMediaToChosenPath(info, index) {
        const defaultName = String(info?.file || "").split("/").pop() || "AD-H3-export";
        try {
            const response = await api.fetchApi("/aiden_h3/pick_save_path", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({default_name: defaultName, initial_dir: saveState.dir, kind: info?.kind || active?.kind || "", title: "选择保存位置"}),
            });
            if (response.ok) {
                const picked = await response.json();
                const target = String(picked.path || "").trim();
                if (!target) return {cancelled: true};
                const saved = await api.fetchApi("/aiden_h3/save_media", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({origin: info.origin || "media", file: info.file, target}),
                });
                const result = await saved.json();
                if (!saved.ok) throw new Error(result.message || "保存失败");
                return {path: result.path};
            }
        } catch (error) { throw error; }
        const dir = await chooseSaveFolder();
        if (!dir) return {cancelled: true};
        const response = await api.fetchApi("/aiden_h3/save_media", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({origin: info.origin || "media", file: info.file,
                dir, template: saveState.template, index: index || 1}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || "保存失败");
        return {path: result.path};
    }
    const adoptSaveConfig = () => {
        const widget = (node.widgets || []).find(item => item.name === "save_config");
        try {
            const config = JSON.parse(widget?.value || "{}");
            if (config && typeof config === "object") {
                if ("dir" in config) saveState.dir = cleanSaveDir(config.dir);
                if ("template" in config) saveState.template = String(config.template || "");
                if ("only_b" in config) saveState.onlyB = !!config.only_b;
                dirInput.value = saveState.dir;
                nameInput.value = saveState.template;
               
                if (onlyBInput) onlyBInput.checked = saveState.onlyB;
            }
        } catch {}
        syncSaveConfig();
    };
    const previousConfigure = node.onConfigure;
    node.onConfigure = function (...args) {
        const result = previousConfigure?.apply(this, args);
        adoptSaveConfig();
        setTimeout(adoptSaveConfig, 0);
        setTimeout(adoptSaveConfig, 500);
        return result;
    };
    const recordIndex = () => {
        const kind = active?.kind || "image";
        const list = mediaRecords[kind] || [];
        const index = list.findIndex(item => item.file === active?.file && (item.origin || "media") === (active?.origin || "media"));
        return index >= 0 ? index + 1 : 1;
    };
    syncSaveConfig();
    const fileFromUrl = url => {
        try {
            const parsed = new URL(url, location.origin);
            const name = parsed.searchParams.get("filename");
            if (!name) return null;
            const subfolder = parsed.searchParams.get("subfolder") || "";
            return {file: subfolder ? `${subfolder}/${name}` : name, origin: parsed.searchParams.get("type") || "output"};
        } catch { return null; }
    };
    const currentMedia = () => {
        if (active?.file) return {...active, url: active.url || imageView.src || video.src || audioView.src};
        const visible = !imageView.hidden ? imageView : (!video.hidden ? video : audioView);
        const url = visible?.src || "";
        return url ? {...(fileFromUrl(url) || {}), url} : null;
    };
    // ── A/B 对比 ──────────────────────────────────────────────
    // 图片：沿用原有「独立左右 / 叠加滑杆」两套（本次不改）。
    // 视频：改为 TE MAN 式「叠加重合 + 鼠标移动分界线擦除」，按钮全部放在视频底部控制条。
    const abOverlay = document.createElement("div");
    abOverlay.className = "ad-h3-ab-overlay"; abOverlay.hidden = true;
    abOverlay.innerHTML = '<div class="ad-h3-ab-layer"><img data-ab-a alt="A"></div>'
        + '<div class="ad-h3-ab-layer ad-h3-ab-top"><img data-ab-b alt="B"></div>'
        + '<div class="ad-h3-ab-divider"></div>'
        + '<div class="ad-h3-ab-stage" hidden>'
        +   '<div class="ad-h3-ab-vpane" data-ab-vpane-a><video data-ab-va muted playsinline preload="metadata"></video></div>'
        +   '<div class="ad-h3-ab-vpane ad-h3-ab-vpane-b" data-ab-vpane-b><video data-ab-vb muted playsinline preload="metadata"></video></div>'
        +   '<div class="ad-h3-ab-vdivider" data-ab-vdivider></div>'
        + '</div>'
        + '<span class="ad-h3-ab-tag" style="left:8px">A</span><span class="ad-h3-ab-tag" style="right:8px">B</span>'
        + '<div class="ad-h3-ab-audios" hidden><div class="ad-h3-ab-acell"><audio data-ab-aa controls preload="metadata"></audio></div>'
        + '<div class="ad-h3-ab-acell"><audio data-ab-ab controls preload="metadata"></audio></div></div>'
        + '<button type="button" class="ad-h3-ab-mode" data-ab-mode>叠加滑杆</button>'
        + '<button type="button" class="ad-h3-ab-fullscreen" data-ab-fullscreen>全屏</button>'
        + '<button type="button" class="ad-h3-ab-exit" data-ab-exit>退出对比</button>'
        + '<div class="ad-h3-ab-bar" hidden>'
        +   '<button type="button" data-ab-syncplay title="从头同步播放两个视频">同步播放</button>'
        +   '<button type="button" data-ab-speed title="选择播放速度">播放速度 1x</button>'
        +   '<button type="button" data-ab-audio-choice title="选择静音、A 音频或 B 音频">静音</button>'
        +   '<button type="button" data-ab-framesync title="总帧数相同时按帧编号同步">同步帧</button>'
        +   '<button type="button" data-ab-fullscreen>全屏</button>'
        +   '<button type="button" data-ab-vmode title="切换叠加擦除 / 独立左右">独立左右</button>'
        +   '<button type="button" data-ab-step="-1">上一帧</button>'
        +   '<button type="button" data-ab-step="1">下一帧</button>'
        +   '<button type="button" data-ab-exit-video>退出对比</button>'
        +   '<span class="ad-h3-ab-track"><input type="range" data-ab-seek min="0" max="0" step="0.001" value="0" aria-label="对比播放位置">'
        +   '<span data-ab-time>0:00.00 / 0:00.00</span></span>'
        + '</div>';
    (root.querySelector(".ad-h3-screen") || layout).append(abOverlay);
    const abA = abOverlay.querySelector("[data-ab-a]"), abB = abOverlay.querySelector("[data-ab-b]");
    const abDivider = abOverlay.querySelector(".ad-h3-ab-divider");
    const abStage = abOverlay.querySelector(".ad-h3-ab-stage");
    const abPaneA = abOverlay.querySelector("[data-ab-vpane-a]"), abPaneB = abOverlay.querySelector("[data-ab-vpane-b]");
    const abVDivider = abOverlay.querySelector("[data-ab-vdivider]");
    const abSlots = {A: null, B: null};
    let abRatio = 0.5;
    let abPicking = false;
    const abVideoA = abOverlay.querySelector("[data-ab-va]"), abVideoB = abOverlay.querySelector("[data-ab-vb]");
    const abBar = abOverlay.querySelector(".ad-h3-ab-bar");
    const abSyncPlayButton = abOverlay.querySelector("[data-ab-syncplay]");
    const abSpeedButton = abOverlay.querySelector("[data-ab-speed]");
    const abAudioButton = abOverlay.querySelector("[data-ab-audio-choice]");
    const abFrameSyncButton = abOverlay.querySelector("[data-ab-framesync]");
    const abVideoModeButton = abOverlay.querySelector("[data-ab-vmode]");
    const abSeek = abOverlay.querySelector("[data-ab-seek]");
    const abTime = abOverlay.querySelector("[data-ab-time]");
    const abStepButtons = [...abOverlay.querySelectorAll("[data-ab-step]")];
    const abFullscreenButtons = [...abOverlay.querySelectorAll("[data-ab-fullscreen]")];
    const abAudios = abOverlay.querySelector(".ad-h3-ab-audios");
    const abAudioA = abOverlay.querySelector("[data-ab-aa]"), abAudioB = abOverlay.querySelector("[data-ab-ab]");
    const abImageModeButton = abOverlay.querySelector("[data-ab-mode]");
    const abFloatingFullscreen = abOverlay.querySelector(".ad-h3-ab-fullscreen");
    const abFloatingExit = abOverlay.querySelector(".ad-h3-ab-exit");
    const abVideoExit = abOverlay.querySelector("[data-ab-exit-video]");
    let abMode = "split";        // 图片：split=独立左右；slider=叠加滑杆
    let abVideoMode = "wipe";    // 视频：wipe=叠加擦除；side=独立左右
    let abFocus = "B";
    const abFps = {A: 24, B: 24};
    const abInfo = {A: {fps: 24, frames: 0, duration: 0}, B: {fps: 24, frames: 0, duration: 0}};
    let abSpeed = 1;
    const AB_SPEEDS = [0.25, 0.5, 0.75, 1, 1.2, 1.5, 2];
    let abAudioMode = "muted";    // muted | a | b
    let abFrameSync = false, abFrameSyncing = false, abSeeking = false;
    let abSyncRaf = 0, abSyncToken = 0;
    let abOpenMenu = null, abOpenMenuFor = null;
    const isABVideo = () => !!(abSlots.A && abSlots.B && abSlots.A.kind === "video" && abSlots.B.kind === "video");
    const isABImage = () => !!(abSlots.A && abSlots.B && abSlots.A.kind === "image" && abSlots.B.kind === "image");
    const isABAudio = () => !!(abSlots.A && abSlots.B && abSlots.A.kind === "audio" && abSlots.B.kind === "audio");
    const markABMode = () => { abOverlay.dataset.zoom = isABVideo() ? "canvas" : (abMode === "split" ? "image" : "canvas"); };
    const abTimeline = () => {
        const durations = [Number(abVideoA.duration) || Number(abInfo.A.duration) || 0,
            Number(abVideoB.duration) || Number(abInfo.B.duration) || 0].filter(value => value > 0);
        return durations.length ? Math.min(...durations) : 0;
    };
    const abStepSeconds = () => 1 / Math.max(abFps.A || 24, abFps.B || 24, 1);
    const formatABTime = value => {
        const total = Math.max(0, Number(value) || 0);
        const minutes = Math.floor(total / 60);
        return `${minutes}:${(total - minutes * 60).toFixed(2).padStart(5, "0")}`;
    };
    // 媒体信息缓存：同时提供 fps / frames / duration；失败结果不缓存，下次重试。
    const MEDIA_INFO_CACHE_LIMIT = 64;
    const mediaInfoCache = new Map();
    const rememberMediaInfo = (key, info) => {
        if (mediaInfoCache.has(key)) mediaInfoCache.delete(key);
        mediaInfoCache.set(key, info);
        while (mediaInfoCache.size > MEDIA_INFO_CACHE_LIMIT) mediaInfoCache.delete(mediaInfoCache.keys().next().value);
    };
    const fetchMediaInfo = async (record) => {
        const origin = record?.origin || "media";
        const file = record?.file;
        if (!file) return {fps: 24, frames: 0, duration: 0};
        const key = `${origin}/${file}`;
        if (mediaInfoCache.has(key)) return mediaInfoCache.get(key);
        try {
            const response = await api.fetchApi(`/aiden_h3/media_info/${encodeURIComponent(origin)}/${String(file).split("/").map(encodeURIComponent).join("/")}`);
            const data = await response.json();
            const info = {
                fps: Number(data?.fps) > 0 ? Number(data.fps) : 24,
                frames: Number(data?.frames) > 0 ? Number(data.frames) : 0,
                duration: Number(data?.duration) > 0 ? Number(data.duration) : 0,
            };
            if (response.ok && !data?.failed) rememberMediaInfo(key, info);
            return info;
        } catch {
            return {fps: 24, frames: 0, duration: 0};
        }
    };
    const fetchMediaFps = async (record) => (await fetchMediaInfo(record)).fps;
    const updateABButtons = () => {
        const video = isABVideo();
        if (abSyncPlayButton) abSyncPlayButton.textContent = (video && (!abVideoA.paused || !abVideoB.paused)) ? "重新播放" : "同步播放";
        if (abSpeedButton) abSpeedButton.textContent = `播放速度 ${abSpeed}x`;
        if (abAudioButton) abAudioButton.textContent = abAudioMode === "a" ? "音频 A" : abAudioMode === "b" ? "音频 B" : "静音";
        if (abFrameSyncButton) {
            const ready = !!(abInfo.A.frames > 0 && abInfo.A.frames === abInfo.B.frames && abInfo.A.fps > 0 && abInfo.B.fps > 0);
            abFrameSyncButton.disabled = !ready || abFrameSyncing;
            abFrameSyncButton.title = ready ? "总帧数相同时按帧编号同步" : "两个视频总帧数不一致，无法按帧同步";
            abFrameSyncButton.classList.toggle("on", abFrameSync);
        }
        if (abVideoModeButton) abVideoModeButton.textContent = abVideoMode === "wipe" ? "独立左右" : "叠加擦除";
        for (const button of abStepButtons) button.disabled = !video;
    };
    const updateABProgress = (time, total = abTimeline()) => {
        const duration = Math.max(0, Number(total) || 0);
        const current = Number.isFinite(Number(time)) ? Number(time) : (abVideoA.currentTime || 0);
        if (abSeek) {
            abSeek.max = duration > 0 ? String(duration) : "0";
            abSeek.value = duration > 0 ? String(Math.min(current, duration)) : "0";
        }
        if (abTime) abTime.textContent = `${formatABTime(current)} / ${formatABTime(duration)}`;
    };
    const applyABAudio = () => {
        if (!isABVideo()) return;
        abVideoA.muted = abAudioMode !== "a";
        abVideoB.muted = abAudioMode !== "b";
    };
    const applyABRates = () => {
        abVideoA.playbackRate = abSpeed;
        abVideoB.playbackRate = abSpeed;
    };
    const applyVideoLayout = () => {
        const side = abVideoMode === "side";
        abPaneA.style.left = "0";
        abPaneA.style.width = side ? "50%" : "100%";
        abPaneB.style.left = side ? "50%" : "0";
        abPaneB.style.width = side ? "50%" : "100%";
        if (side) {
            abPaneB.style.clipPath = "none";
            abVDivider.style.left = "50%";
            abVDivider.style.cursor = "default";
            abStage.style.cursor = "default";
        } else {
            const percent = Math.round(abRatio * 10000) / 100;
            abPaneB.style.clipPath = `inset(0 0 0 ${percent}%)`;
            abVDivider.style.left = `${percent}%`;
            abVDivider.style.cursor = "ew-resize";
            abStage.style.cursor = "ew-resize";
        }
    };
    const closeABMenus = () => {
        if (!abOpenMenu) { abOpenMenuFor = null; return; }
        abOpenMenu.remove();
        abOpenMenu = null;
        abOpenMenuFor = null;
    };
    const openABMenu = (button, items, current, onPick) => {
        closeABMenus();
        if (!button) return;
        const menu = document.createElement("div");
        menu.className = "ad-h3-ab-menu";
        menu.style.visibility = "hidden";
        for (const item of items) {
            const option = document.createElement("button");
            option.type = "button";
            option.textContent = item.label;
            if (item.value === current) option.classList.add("on");
            option.addEventListener("click", event => {
                event.preventDefault(); event.stopPropagation();
                onPick(item.value);
                closeABMenus();
            });
            menu.append(option);
        }
        menu.addEventListener("pointerdown", event => event.stopPropagation());
        const host = document.fullscreenElement || document.body;
        host.append(menu);
        const buttonRect = button.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();
        const left = Math.max(4, Math.min(buttonRect.left + (buttonRect.width - menuRect.width) / 2, window.innerWidth - menuRect.width - 4));
        let top = buttonRect.top - menuRect.height - 6;
        if (top < 4) top = Math.min(buttonRect.bottom + 6, window.innerHeight - menuRect.height - 4);
        menu.style.left = `${Math.round(left)}px`;
        menu.style.top = `${Math.round(Math.max(4, top))}px`;
        menu.style.visibility = "visible";
        abOpenMenu = menu;
        abOpenMenuFor = button;
    };
    const onABDocPointerDown = event => {
        if (!abOpenMenu) return;
        if (abOpenMenu.contains(event.target)) return;
        if (abOpenMenuFor && abOpenMenuFor.contains(event.target)) return;
        closeABMenus();
    };
    document.addEventListener("pointerdown", onABDocPointerDown, true);
    // 两路播放：暂停时对齐 B；播放中不做持续 seek 纠偏（避免卡顿），只按 TE 方式一起播放。
    const stopABSyncLoop = () => {
        if (abSyncRaf) cancelAnimationFrame(abSyncRaf);
        abSyncRaf = 0;
    };
    const alignBToTime = () => {
        if (!isABVideo()) return;
        const target = Math.min(abVideoA.currentTime || 0, abVideoB.duration || Infinity);
        if (Number.isFinite(target)) abVideoB.currentTime = target;
    };
    const alignBToFrame = () => {
        const infoA = abInfo.A, infoB = abInfo.B;
        if (!(infoA.frames > 0 && infoA.frames === infoB.frames) || !(infoA.fps > 0 && infoB.fps > 0)) return false;
        const frame = Math.max(0, Math.min(infoA.frames - 1, Math.round((abVideoA.currentTime || 0) * infoA.fps)));
        abVideoB.currentTime = Math.min(frame / infoB.fps, abVideoB.duration || Infinity);
        return true;
    };
    const pauseABVideos = () => {
        abSyncToken += 1;
        stopABSyncLoop();
        abVideoA.pause();
        abVideoB.pause();
        updateABButtons();
    };
    const runABSyncLoop = token => {
        const tick = () => {
            if (disposed || token !== abSyncToken) return;
            const total = abTimeline();
            const current = Math.min(abVideoA.currentTime || 0, total || Infinity);
            if (!abSeeking) updateABProgress(current, total);
            const finished = total > 0 && (
                abVideoA.ended || abVideoB.ended
                || (abVideoA.currentTime || 0) >= total - 0.03
                || (abVideoB.currentTime || 0) >= total - 0.03);
            if (finished) { void startABPlayback(true); return; }
            abSyncRaf = requestAnimationFrame(tick);
        };
        abSyncRaf = requestAnimationFrame(tick);
    };
    const startABPlayback = async (restart = false) => {
        if (!isABVideo()) return;
        const token = ++abSyncToken;
        stopABSyncLoop();
        const total = abTimeline();
        const atEnd = total > 0 && ((abVideoA.currentTime || 0) >= total - 0.03 || abVideoB.ended);
        if (restart || atEnd || abVideoA.ended) {
            abVideoA.currentTime = 0;
            abVideoB.currentTime = 0;
        } else if (!abFrameSync) {
            alignBToTime();
        }
        applyABRates();
        applyABAudio();
        updateABButtons();
        await Promise.all([abVideoA.play().catch(() => {}), abVideoB.play().catch(() => {})]);
        if (disposed || token !== abSyncToken) return;
        if (abVideoA.paused || abVideoB.paused) {
            pauseABVideos();
            updateABProgress(abVideoA.currentTime || 0, abTimeline());
            return;
        }
        runABSyncLoop(token);
    };
    const toggleABPlayback = () => {
        if (!isABVideo()) return;
        if (!abVideoA.paused || !abVideoB.paused) {
            pauseABVideos();
            updateABProgress(abVideoA.currentTime || 0, abTimeline());
        } else {
            void startABPlayback(false);
        }
    };
    const seekABVideo = (media, time) => new Promise(resolve => {
        let done = false;
        let timer = 0;
        const finish = ok => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            media.removeEventListener("seeked", onSeeked);
            media.removeEventListener("error", onError);
            resolve(ok);
        };
        const onSeeked = () => finish(true);
        const onError = () => finish(false);
        timer = setTimeout(() => finish(false), 2000);
        const target = Math.max(0, Number(time) || 0);
        if (Math.abs((media.currentTime || 0) - target) < 0.001) { finish(true); return; }
        media.addEventListener("seeked", onSeeked, {once: true});
        media.addEventListener("error", onError, {once: true});
        try { media.currentTime = target; } catch { finish(false); }
    });
    const syncABFrames = async () => {
        if (!isABVideo() || abFrameSyncing) return;
        const infoA = abInfo.A, infoB = abInfo.B;
        if (!(infoA.frames > 0 && infoA.frames === infoB.frames) || !(infoA.fps > 0 && infoB.fps > 0)) {
            setABHint("两个视频总帧数不一致，无法按帧同步");
            return;
        }
        const wasPlaying = !abVideoA.paused || !abVideoB.paused;
        abFrameSyncing = true;
        updateABButtons();
        pauseABVideos();
        const frame = Math.max(0, Math.min(infoA.frames - 1, Math.round((abVideoA.currentTime || 0) * infoA.fps)));
        const ok = await Promise.all([
            seekABVideo(abVideoA, frame / infoA.fps),
            seekABVideo(abVideoB, frame / infoB.fps),
        ]);
        abFrameSyncing = false;
        if (ok[0] && ok[1]) {
            abFrameSync = true;
            abFrameSyncButton.textContent = "已同步";
            setTimeout(() => {
                if (!disposed && isABVideo()) abFrameSyncButton.textContent = "同步帧";
            }, 1200);
            setABHint(`已按第 ${frame + 1} 帧对齐 A / B`);
        } else {
            setABHint("按帧对齐失败：视频暂未定位成功");
        }
        updateABButtons();
        updateABProgress(abVideoA.currentTime || 0, abTimeline());
        if (wasPlaying && ok[0] && ok[1]) void startABPlayback(false);
    };
    const applyABSeek = value => {
        if (!isABVideo()) return;
        const total = abTimeline();
        const target = Math.max(0, Math.min(total || Number(value) || 0, Number(value) || 0));
        abVideoA.currentTime = Math.min(target, abVideoA.duration || target);
        if (abFrameSync) alignBToFrame();
        else abVideoB.currentTime = Math.min(target, abVideoB.duration || target);
        if (abTime) abTime.textContent = `${formatABTime(target)} / ${formatABTime(total)}`;
    };
    abSeek.addEventListener("pointerdown", () => { abSeeking = true; });
    abSeek.addEventListener("input", () => { abSeeking = true; applyABSeek(Number(abSeek.value)); });
    abSeek.addEventListener("change", () => {
        applyABSeek(Number(abSeek.value));
        abSeeking = false;
        updateABProgress(abVideoA.currentTime || 0, abTimeline());
    });
    abSyncPlayButton?.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        void startABPlayback(true);
    });
    abSpeedButton?.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        if (abOpenMenuFor === abSpeedButton) { closeABMenus(); return; }
        openABMenu(abSpeedButton, AB_SPEEDS.map(speed => ({label: `${speed}x`, value: speed})), abSpeed, value => {
            abSpeed = Number(value) || 1;
            applyABRates();
            updateABButtons();
        });
    });
    abAudioButton?.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        if (abOpenMenuFor === abAudioButton) { closeABMenus(); return; }
        openABMenu(abAudioButton, [
            {label: "静音", value: "muted"},
            {label: "音频 A", value: "a"},
            {label: "音频 B", value: "b"},
        ], abAudioMode, value => {
            abAudioMode = value;
            applyABAudio();
            updateABButtons();
        });
    });
    abFrameSyncButton?.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        void syncABFrames();
    });
    abVideoModeButton?.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        abVideoMode = abVideoMode === "wipe" ? "side" : "wipe";
        applyVideoLayout();
        updateABButtons();
    });
    for (const button of abStepButtons) button.addEventListener("click", event => {
        event.preventDefault(); event.stopPropagation();
        if (!isABVideo()) return;
        pauseABVideos();
        const step = Number(button.dataset.abStep) * abStepSeconds();
        const total = abTimeline();
        for (const media of [abVideoA, abVideoB]) {
            media.pause();
            const target = Math.max(0, Math.min(total || media.duration || 0, (media.currentTime || 0) + step));
            media.currentTime = Math.min(target, media.duration || target);
        }
        if (abFrameSync) alignBToFrame();
        updateABProgress(abVideoA.currentTime || 0, abTimeline());
    });
    for (const button of abFullscreenButtons) button.addEventListener("click", event => {
        event.stopPropagation();
        void toggleABFullscreen();
    });
    abImageModeButton.addEventListener("click", event => {
        event.stopPropagation();
        abMode = abMode === "split" ? "slider" : "split";
        markABMode();
        abImageModeButton.textContent = abMode === "split" ? "叠加滑杆" : "独立左右";
        resetABView();
        applyAB();
    });
    abFloatingExit.addEventListener("click", event => {
        event.stopPropagation(); exitAB();
    });
    abVideoExit.addEventListener("click", event => {
        event.stopPropagation(); exitAB();
    });
    const exitAB = () => {
        if (document.fullscreenElement === abOverlay && document.exitFullscreen) { void document.exitFullscreen().catch(() => {}); }
        abSyncToken += 1;
        stopABSyncLoop();
        closeABMenus();
        abSlots.A = null; abSlots.B = null; abPicking = false;
        abFrameSync = false; abFrameSyncing = false; abSeeking = false;
        resetABView();
        for (const media of [abVideoA, abVideoB, abAudioA, abAudioB]) releaseMedia(media);
        abA.removeAttribute("src"); abB.removeAttribute("src");
        paintAB();
        const hint = root.querySelector("[data-tab-hint]");
        if (hint) { hint.hidden = false; hint.textContent = "已退出 A/B 对比"; }
    };
    let fullscreenBusy = false;
    async function toggleFullscreen(target) {
        if (fullscreenBusy) return;
        fullscreenBusy = true;
        try { await togglePlayerFullscreen(target); }
        catch (error) { node._adH3Hint?.("全屏切换失败：" + error.message, true); }
        finally { fullscreenBusy = false; onFullscreenChange(); }
    }
    function toggleABFullscreen() { void toggleFullscreen(abOverlay); }
    abOverlay.addEventListener("click", event => {
        if (event.target.closest("button,input,select,a,.ad-h3-ab-bar,.ad-h3-ab-audios")) return;
        if (!abSlots.A || !abSlots.B) return;
        const rect = abOverlay.getBoundingClientRect();
        const ratio = (event.clientX - rect.left) / (rect.width || 1);
        abFocus = ratio < 0.5 ? "A" : "B";
        const tags = abOverlay.querySelectorAll(".ad-h3-ab-tag");
        if (tags[0]) tags[0].classList.toggle("on", abFocus === "A");
        if (tags[1]) tags[1].classList.toggle("on", abFocus === "B");
        if (isABVideo()) {
            event.preventDefault(); event.stopPropagation();
            toggleABPlayback();
        }
    });
    const onPreviewEscape = event => {
        if (event.key !== "Escape") return;
        closeABMenus();
        const current = document.fullscreenElement;
        if (current && root.contains(current)) {
            event.preventDefault(); event.stopPropagation();
            void document.exitFullscreen().then(onFullscreenChange).catch(() => {});
            return;
        }
        if (!abOverlay.hidden) exitAB();
    };
    window.addEventListener("keydown", onPreviewEscape, true);
    const updateABWipe = clientX => {
        const rect = abStage.getBoundingClientRect();
        if (!rect.width) return;
        abRatio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        applyVideoLayout();
    };
    abStage.addEventListener("pointerdown", event => {
        if (!isABVideo() || abVideoMode !== "wipe") return;
        updateABWipe(event.clientX);
    });
    abStage.addEventListener("pointermove", event => {
        if (!isABVideo() || abVideoMode !== "wipe") return;
        updateABWipe(event.clientX);
    });
    const paintAB = () => {
        const both = abSlots.A && abSlots.B;
        abOverlay.hidden = !both;
        layout.classList.toggle("is-comparing", !!both);
        paintABButton();
        if (!both) {
            abSyncToken += 1;
            stopABSyncLoop();
            closeABMenus();
            abVideoA.pause(); abVideoB.pause();
            return;
        }
        video.pause(); audioView.pause();
        const isVideo = isABVideo();
        const isAudio = isABAudio();
        const isImage = isABImage();
        abStage.hidden = !isVideo;
        abBar.hidden = !isVideo;
        abAudios.hidden = !isAudio;
        markABMode();
        abImageModeButton.hidden = !isImage;
        abImageModeButton.textContent = abMode === "split" ? "叠加滑杆" : "独立左右";
        abFloatingFullscreen.hidden = isVideo;
        abFloatingExit.hidden = isVideo;
        abDivider.style.display = isImage ? "" : "none";
        const tags = abOverlay.querySelectorAll(".ad-h3-ab-tag");
        const tagLabel = (slot, side) => `${side}-${Number(slot?.serial) > 0 ? String(Number(slot.serial)).padStart(2, "0") : "-"}`;
        if (tags[0]) { tags[0].textContent = tagLabel(abSlots.A, "A"); tags[0].title = String(abSlots.A.file || ""); }
        if (tags[1]) { tags[1].textContent = tagLabel(abSlots.B, "B"); tags[1].title = String(abSlots.B.file || ""); }
        abA.parentElement.style.display = (isVideo || isAudio) ? "none" : "";
        abB.parentElement.style.display = (isVideo || isAudio) ? "none" : "";
        if (isVideo) {
            const slotA = abSlots.A, slotB = abSlots.B;
            abVideoA.src = slotA.url;
            abVideoB.src = slotB.url;
            abVideoA.muted = true; abVideoB.muted = true;
            abSpeed = 1; abAudioMode = "muted"; abFrameSync = false; abFrameSyncing = false;
            abRatio = 0.5; abVideoMode = "wipe"; abSeeking = false;
            abInfo.A = {fps: 24, frames: 0, duration: 0};
            abInfo.B = {fps: 24, frames: 0, duration: 0};
            abFps.A = 24; abFps.B = 24;
            applyABRates();
            applyABAudio();
            if (abFrameSyncButton) abFrameSyncButton.textContent = "同步帧";
            updateABButtons();
            updateABProgress(0, 0);
            applyVideoLayout();
            Promise.all([fetchMediaInfo(slotA), fetchMediaInfo(slotB)]).then(([infoA, infoB]) => {
                if (abSlots.A !== slotA || abSlots.B !== slotB) return;
                abInfo.A = infoA; abInfo.B = infoB;
                abFps.A = infoA.fps; abFps.B = infoB.fps;
                updateABButtons();
                updateABProgress(abVideoA.currentTime || 0, abTimeline());
            }).catch(() => {});
        } else if (isAudio) {
            abAudioA.src = abSlots.A.url; abAudioB.src = abSlots.B.url;
        } else {
            abA.src = abSlots.A.url; abB.src = abSlots.B.url;
            resetABView();
            applyAB();
        }
    };
    for (const media of [abVideoA, abVideoB]) {
        media.addEventListener("loadedmetadata", () => {
            if (!isABVideo()) return;
            updateABButtons();
            updateABProgress(abVideoA.currentTime || 0, abTimeline());
        });
        media.addEventListener("durationchange", () => {
            if (!isABVideo()) return;
            updateABProgress(abVideoA.currentTime || 0, abTimeline());
        });
    }
    const applyABView = () => {
        const transform = `translate(${Math.round(abView.x)}px, ${Math.round(abView.y)}px) scale(${abView.zoom.toFixed(3)})`;
        for (const image of [abA, abB]) {
            image.style.transformOrigin = "50% 50%";
            image.style.transform = transform;
        }
    };
    const resetABView = () => { abView.zoom = 1; abView.x = 0; abView.y = 0; applyABView(); };
    const applyAB = () => {
        if (isABVideo()) { applyVideoLayout(); return; }
        const width = abOverlay.clientWidth || root.querySelector(".ad-h3-screen")?.clientWidth || 0;
        if (!width) return;
        const layerA = abA.parentElement, layerB = abB.parentElement;
        if (abMode === "split") {
            layerA.style.left = "0px"; layerA.style.width = "50%"; layerA.style.right = "auto";
            layerB.style.left = "50%"; layerB.style.width = "50%"; layerB.style.right = "auto";
            layerB.style.clipPath = "none";
            abDivider.style.left = "50%";
            abDivider.style.cursor = "default";
        } else {
            const x = Math.round(abRatio * width);
            layerA.style.left = "0px"; layerA.style.width = ""; layerA.style.right = "0px";
            layerB.style.left = "0px"; layerB.style.width = ""; layerB.style.right = "0px";
            layerB.style.clipPath = `inset(0 0 0 ${x}px)`;
            abDivider.style.left = `${x}px`;
            abDivider.style.cursor = "ew-resize";
        }
    };
    const setABHint = text => {
        const hint = root.querySelector("[data-tab-hint]");
        if (hint) { hint.hidden = false; hint.textContent = text; }
    };
    const paintABButton = () => {
        abButton.classList.toggle("active", abPicking || !abOverlay.hidden);
    };
    const startABPick = () => {
        if (!abOverlay.hidden) { exitAB(); return; }
        abSlots.A = null; abSlots.B = null; abPicking = true;
        paintABButton();
        setABHint("A/B 选择：先点下方一个素材作为 A，再点另一个作为 B");
    };
    const pickAB = (record, kind) => {
        if (!abPicking) return false;
        if (kind === "audio") { setABHint("A/B 对比支持图片或视频；音频请用试听"); return true; }
        const info = {url: mediaUrl(record.origin || "media", record.file), file: record.file, kind, origin: record.origin || "media", serial: record.serial};
        if (!abSlots.A) {
            abSlots.A = info;
            setABHint("已选 A，请再点一个素材作为 B");
        } else if (!abSlots.B) {
            if (abSlots.A.kind !== kind) { setABHint("A/B 需同类型：图片-图片或视频-视频"); return true; }
            abSlots.B = info;
            abPicking = false;
            paintAB();
            setABHint(kind === "video" ? "A/B 视频对比已开启：移动鼠标擦除分界，点击画面播放/暂停" : "A/B 图片对比已开启：拖动分界线对比");
        }
        paintABButton();
        return true;
    };
    abButton.addEventListener("click", event => { event.stopPropagation(); startABPick(); });
    let abDragging = false;
    let abPanning = false, abPanStart = null;
    const abView = {zoom: 1, x: 0, y: 0};
    const abLocalX = clientX => {
        const rect = abOverlay.getBoundingClientRect();
        return (clientX - rect.left) / (rect.width / (abOverlay.offsetWidth || 1));
    };
    abOverlay.addEventListener("wheel", event => {
        if (abOverlay.hidden || isABVideo()) return;
        if (abMode !== "split") return;
        if (abSlots.A?.kind !== "image" || abSlots.B?.kind !== "image") return;
        event.preventDefault();
        event.stopPropagation();
        const rect = abOverlay.getBoundingClientRect();
        const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
        const factor = Math.exp(-event.deltaY * 0.0015);
        const nextZoom = Math.min(8, Math.max(0.4, abView.zoom * factor));
        const applied = nextZoom / abView.zoom;
        abView.x = (abView.x - (event.clientX - cx)) * applied + (event.clientX - cx);
        abView.y = (abView.y - (event.clientY - cy)) * applied + (event.clientY - cy);
        abView.zoom = nextZoom;
        applyABView();
    }, {passive: false});

    abOverlay.addEventListener("pointerdown", event => {
        if (event.target.closest("button,input,select,a,.ad-h3-ab-bar,.ad-h3-ab-stage")) return;
        if ((abSlots.A?.kind === "image" && abSlots.B?.kind === "image") && event.button === 0) {
            const rect = abOverlay.getBoundingClientRect();
            const localX = event.clientX - rect.left;
            const nearDivider = abMode === "slider" && Math.abs(localX - abRatio * rect.width) < 14;
            if (!nearDivider) {
                abPanning = true;
                abPanStart = {x: event.clientX - abView.x, y: event.clientY - abView.y};
                abOverlay.setPointerCapture?.(event.pointerId);
                abOverlay.style.cursor = "grabbing";
                event.preventDefault();
                event.stopPropagation();
                return;
            }
        }
        if (abMode !== "slider" || abSlots.A?.kind !== "image" || abSlots.B?.kind !== "image") return;
        if (event.button !== 0) return;
        abDragging = true; abOverlay.setPointerCapture?.(event.pointerId);
        const width = abOverlay.clientWidth || 1;
        abRatio = Math.min(1, Math.max(0, abLocalX(event.clientX) / width));
        applyAB(); event.preventDefault(); event.stopPropagation();
    });
    abOverlay.addEventListener("pointermove", event => {
        if (abPanning && abPanStart) {
            abView.x = event.clientX - abPanStart.x;
            abView.y = event.clientY - abPanStart.y;
            applyABView();
            event.preventDefault();
            return;
        }
        if (!abDragging) return;
        const width = abOverlay.clientWidth || 1;
        abRatio = Math.min(1, Math.max(0, abLocalX(event.clientX) / width));
        applyAB(); event.preventDefault();
    });
    abOverlay.addEventListener("pointerup", event => {
        if (abPanning) { abPanning = false; abPanStart = null; abOverlay.style.cursor = ""; }
        abDragging = false; try { abOverlay.releasePointerCapture?.(event.pointerId); } catch {}
    });

    enlargeButton.addEventListener("click", event => {
        event.stopPropagation();
        // A/B 对比中：放大 = 整块对比画面全屏，而不是只放大单边。
        if (!abOverlay.hidden && (abSlots.A || abSlots.B)) {
            toggleABFullscreen();
            return;
        }
        const info = currentMedia();
        if (info?.url && info.url !== location.href) openLightbox({...info, kind: imageView.hidden ? "video" : "image"});
    });
    previewDownload.addEventListener("click", async event => {
        event.stopPropagation();
        const focused = (!abOverlay.hidden && abSlots[abFocus]) ? abSlots[abFocus] : null;
        const info = focused
            ? {file: focused.file, origin: focused.origin || "media", kind: focused.kind}
            : currentMedia();
        if (!info?.file) { previewDownload.textContent = "无文件"; setTimeout(() => { previewDownload.textContent = "下载"; }, 1600); return; }
        try {
            const saved = await saveMediaToChosenPath(info, recordIndex());
            if (saved.cancelled) {
                previewDownload.textContent = "未选择";
                setTimeout(() => { previewDownload.textContent = "下载"; }, 1600);
                return;
            }
            previewDownload.textContent = "已保存";
            previewDownload.title = `已保存到：${saved.path}`;
            node._adH3Hint?.(`已保存到：${saved.path}`, false);
            setTimeout(() => { previewDownload.textContent = "下载"; }, 1600);
        } catch (error) {
            previewDownload.textContent = "失败";
            node._adH3Hint?.(`保存失败：${error.message || "未知错误"}`, true);
            setTimeout(() => { previewDownload.textContent = "下载"; }, 1600);
        }
    });

    // 走 ComfyUI 原生“运行”按钮，保证顶部队列/活动任务状态同步显示
    node._adH3Run = () => {
        if (node._adH3AffectedRunning) { node._adH3Hint?.("正在运行所选段，请等待完成或先点停止。", true); return false; }
        node._adH3FollowCurrent?.();
        previewPinned = false;
        if (typeof node._adH3QueueOwned === "function") return node._adH3QueueOwned().catch(error=>{node._adH3Hint?.(error.message,true);return false;});
        const buttons = [...document.querySelectorAll("button")];
        const native = buttons.find(button => /^(运行|Queue)$/.test((button.textContent || "").trim()))
            || buttons.find(button => (button.getAttribute("data-testid") || "") === "queue-button");
        if (native) { native.click(); return true; }
        const fallback = window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app);
        try { void fallback?.queuePrompt?.(0, 1); return true; } catch { return false; }
    };
    node._adH3OpenLightbox = openLightbox;
    let initialIdle = true;
    const setIdle = idle => { layout.classList.toggle("is-idle", !!idle); layout.classList.toggle("is-initial", !!idle && initialIdle); };
    setIdle(true);
    video.muted = true; video.autoplay = false; video.loop = false;
    let active = null, disposed = false, revision = 0, currentSession = "", generationKey = "";
    let playlistEnabled = false, playlistRevision = 0, playlistKey = "";
    const playlistButton = root.querySelector("[data-loop-playlist]");
    // 打开工作流后的第一次进度同步只恢复列表，不自动显示上次的旧结果；用户需手动点缩略图查看。
    let hydratedSession = false;
    const hintEl = root.querySelector("[data-loop-hint]");
    const tabHint = root.querySelector("[data-tab-hint]");
    node._adH3Hint = (text, error = false) => {
        // 提示统一显示在分类按钮同一行（醒目区域），底部旧提示不再显示
        if (hintEl) hintEl.hidden = true;
        if (!tabHint) return;
        tabHint.hidden = !text;
        tabHint.textContent = text || "";
        tabHint.classList.toggle("error", !!error);
    };
    const cards = new Map();
    const tabButtons = [...root.querySelectorAll("[data-tab]")];
    const panels = {image: root.querySelector('[data-panel="image"]'), audio: root.querySelector('[data-panel="audio"]'), video: root.querySelector('[data-panel="video"]')};
    const mediaLists = {image: root.querySelector("[data-image-list]"), audio: root.querySelector("[data-audio-list]"), video: root.querySelector("[data-video-list]")};
    const mediaRecords = {image: [], audio: [], video: []};
    // 预览分类卡片按 (kind, origin, file) 复用 DOM：新结果只追加/更新一张卡，不重建整表。
    const mediaCards = {image: new Map(), audio: new Map(), video: new Map()};
    // 每页最多绘制200条；完整历史按最近10组保留，显示限额不截断历史。
    const MEDIA_DISPLAY_LIMIT = 200;
    const mediaCardKey = (kind, record) => `${kind}|${record?.role||"A"}|${record?.origin || "media"}|${record?.file || ""}`;
    const trimMediaRecords = kind => {
        enforceHistoryRetention();
    };
    // 渲染后只合并一次重绘通知；节点高度统一由 dom_layout.fitPreviewContent 的 ResizeObserver 收敛，
    // 这里不再直接 setSize，避免与用户拖动/其它高度来源互相抢高度。
    let mediaResizeToken = 0;
    function scheduleMediaResize() {
        const token = ++mediaResizeToken;
        setTimeout(() => {
            if (token !== mediaResizeToken) return;
            try { node.setDirtyCanvas?.(true, true); } catch {}
        }, 0);
    }
    // 分段生成/合并生成缩略图的本地隐藏记录（X 只移除预览，不删文件/进度）；持久化到节点属性。
    const hiddenStrips = new Set(
        (Array.isArray(node.properties?.ad_h3_hidden_strips) ? node.properties.ad_h3_hidden_strips : [])
            .filter(key => typeof key === "string")
    );
    const persistHiddenStrips = () => {
        node.properties ||= {};
        node.properties.ad_h3_hidden_strips = [...hiddenStrips];
    };
    const stripRecords = {segment: [], segmentB: [], merged: []};
    // 「合并片段」Ctrl 多选：只合并选中的同来源片段；不选则按合并来源合并全部段最新结果。
    const mergeSelection = new Map();
    let processingRecord = null;  // 单选仅跟随手动点击，自动到达的新结果不抢目标。
    // 轮次：后端 state.round_no 是“逻辑轮次”（只有完成后再开始/清空重置才 +1；
    // 续跑、运行此段、抽卡都不变）。roundMap 仅用于兼容没有 round_no 的旧会话。
    const roundMap = new Map((node.properties?.ad_h3_round_map || [])
        .filter(item => item && item.id).map(item => [String(item.id), Number(item.no) || 0]));
    let showHistoryRounds = !!node.properties?.ad_h3_show_history;
    const expandedHistoryRounds = new Set(node.properties?.ad_h3_expanded_rounds || []);
    const historyPages = new Map(), mediaPages = {image:0,audio:0,video:0};
    const persistRoundMap = () => {
        node.properties ||= {};
        node.properties.ad_h3_round_map = [...roundMap.entries()].map(([id, no]) => ({id, no}));
    };
    const roundNoOf = id => (id ? (roundMap.get(String(id)) || 0) : 0);
    const activeRoundId = () => String(node.properties?.ad_execution_round?.id
        || latestSessionState?.execution_round?.id || "");
    const activeRoundNo = () => {
        // 服务端 round_no 是逻辑轮次权威；本地保存值只作离线/旧会话回退。
        const logical = Number(latestSessionState?.round_no) || 0;
        if (logical) return logical;
        const stored = Number(node.properties?.ad_h3_round_no) || 0;
        if (stored) return stored;
        return roundNoOf(activeRoundId());  // 旧会话兜底
    };
    const rememberRound = state => {
        const logical = Number(state?.round_no) || 0;
        if (logical) {
            node.properties ||= {};
            node.properties.ad_h3_round_no = logical;
            return logical;
        }
        const id = String(state?.execution_round?.id || "");
        if (!id) return activeRoundNo();
        if (!roundMap.has(id)) {
            const next = Math.max(0, ...[...roundMap.values()]) + 1;
            roundMap.set(id, next);
            persistRoundMap();
        }
        return roundMap.get(id);
    };
    const recordRoundNo = record => Number(record?.round_no) || 0;
    // 分段A/分段B/合并缩略图持久化：后台会话切换或重开后仍展示之前生成的缩略图。
    const stripStore = {segment: [], merged: []};
    // 完整合并索引每段每端只留最新视频；与限量显示的缩略图历史分开。
    const mergeIndex = new Map();
    const mergeIndexKey = record => `${record.role === "B" ? "B" : "A"}:${recordRoundNo(record)}:${Number(record.segment_index) || Number(record.serial) || 0}`;
    const rememberMergeRecord = (record, replace = true) => {
        if (!record?.file || inferMediaKind(record.file, record.kind) !== "video") return;
        const number = Number(record.segment_index) || Number(record.serial) || 0;
        if (number <= 0) return;
        const key = mergeIndexKey(record);
        if (replace || !mergeIndex.has(key)) mergeIndex.set(key, {
            ...identityFields(record), file:record.file, origin:record.origin || "media", kind:"video", segment_index:number,
            role:record.role === "B" ? "B" : "A", label:record.label,
            redraw:!!record.redraw, redraw_base:Number(record.redraw_base)||0,
            redraw_count:Number(record.redraw_count)||0, serial:Number(record.serial)||0,
            round_id: record.round_id || "", round_no: recordRoundNo(record)
        });
    };
    const persistMergeIndex = () => {node.properties.ad_h3_merge_index = [...mergeIndex.values()];};
    const savedStrip = node.properties?.ad_h3_strip_previews;
    if (savedStrip && typeof savedStrip === "object") {
        for (const kind of ["segment", "merged"]) {
            if (Array.isArray(savedStrip[kind]))
                stripStore[kind] = savedStrip[kind].filter(item => item && item.file);
        }
    }
    const persistStripPreviews = () => {
        node.properties ||= {};
        const clean = list => list.map(item => {
            const {thumb_b64, thumb_mime, url, ...record} = item;
            return {...record,origin:record.origin || "session",round_no:recordRoundNo(record)};
        });
        node.properties.ad_h3_strip_previews = {segment: clean(stripStore.segment), merged: clean(stripStore.merged)};
    };
    // 「分段生成A/B」按生成历史展示：A/B 输入端口各一排，所有结果都保留；合并生成单独一排。
    const syncStripFromMedia = () => {
        enforceHistoryRetention();
        const history = [];
        for (const kind of ["image", "audio", "video"]) {
            for (const record of mediaRecords[kind]) {
                if (!record?.file) continue;
                history.push({...identityFields(record), file: record.file, origin: record.origin || "media",
                    label: recordLabel(record, 0), kind: record.kind || kind,
                    role: record.role === "B" ? "B" : "A",
                    segment_index: Number(record.segment_index) || 0,
                    session_id: record.session_id, segment_key: record.segment_key,
                    serial: Number(record.serial) || 0,
                    round_id: record.round_id || "", round_no: recordRoundNo(record),
                    redraw: !!record.redraw,
                    redraw_base: Number(record.redraw_base) || 0,
                    redraw_count: Number(record.redraw_count) || 0});
            }
        }
        history.sort((a, b) => {
            const left = numberOrder(a), right = numberOrder(b);
            for (let i = 0; i < Math.max(left.length, right.length); i++) {
                const diff = (left[i] || 0) - (right[i] || 0);
                if (diff) return diff;
            }
            return 0;
        });
        // 兼容旧存档：已有完整索引不被较早的可见历史覆盖。
        const legacyLatest = new Map();
        for (const record of history) legacyLatest.set(mergeIndexKey(record), record);
        for (const record of legacyLatest.values()) rememberMergeRecord(record, false);
        persistMergeIndex();
        stripStore.segment = history;
        for (const key of [...mergeSelection.keys()])
            if (!stripStore.segment.some(record => keyOf(record) === key)) mergeSelection.delete(key);
        persistStripPreviews();
        renderRecords(segments, stripStore.segment.filter(record => record.role !== "B"), "segment");
        renderRecords(segmentsB, stripStore.segment.filter(record => record.role === "B"), "segmentB");
    };
    const stripKey = (kind, record) => `${kind}:${record?.origin || "session"}/${record?.file || ""}`;
    let redoTarget = null;
    let resumeTarget = null;
    // 本段抽卡（2026-10-28 用户要求）：
    // - 任意时候（含分段循环已完成）选中任意预览卡片即可指定段重抽；
    // - 运行中必须先点「停止」让流程待命，再点「本段抽卡」；
    // - 重抽结果用黄色编号显示；抽完点「开始」继续分段流程。
    let latestSessionState = null;
    let redrawPending = null;
    let redrawReview = null;
    // 点「停止」时如果分段循环正在运行，记住它；抽卡只是预览备选，点「开始」要续接原来的分段循环位置。
    let batchResume = false, redrawRequest=0;
    // 停止请求进行中：本地已先标 paused，但后台可能仍在中断；此期间拒绝抽卡，避免和停止竞态。
    let stopPending = false;
    // 分类与主预览联动（2026-10-28 用户要求）：
    // - 图片分类只显示图片，绝不显示播放控制栏；音频/视频分类各自显示对应内容。
    // - 用户手动选中缩略图后，新结果自动到达时不抢当前预览（避免“删除预览后自己跳到下一张”）。
    const categoryOf = kind => (kind === "image" || kind === "audio") ? kind : "video";
    const lastShown = {image: null, audio: null, video: null};
    const kindPatterns = [[/\.(png|jpe?g|webp|bmp|gif|avif)$/i, "image"],
        [/\.(wav|mp3|flac|m4a|aac|ogg|opus)$/i, "audio"]];
    const inferMediaKind = (file, declared) => {
        if (declared === "image" || declared === "audio" || declared === "video") return declared;
        const name = String(file || "");
        for (const [pattern, kind] of kindPatterns) if (pattern.test(name)) return kind;
        return "video";
    };
    // 卡片编号：R{轮次}-{段号}，本段抽卡追加「-第几次」（如 R2-05、R2-05-1）。
    // 排序按 [轮次, 段号, 抽卡次数, 流水号]；没有轮次信息的旧记录按第 0 轮处理。
    const recordSegmentNo = record => Number(record?.segment_index) || Number(record?.serial) || 0;
    const numberOrder = record => [
        recordRoundNo(record),
        recordSegmentNo(record),
        record?.redraw ? (Number(record.redraw_count) || 1) : 0,
        Number(record?.serial) || 0,
    ];
    const displayedBase = record => redrawBase(record);
    function placeRedrawRecord(kind, item) {
        const list = mediaRecords[kind];
        const at = list.indexOf(item);
        if (at >= 0) list.splice(at, 1);
        const base = Number(item.redraw_base) || 0;
        const round = recordRoundNo(item);
        let insert = -1;
        if (base) list.forEach((entry, index) => {
            if (recordRoundNo(entry) === round && recordSegmentNo(entry) === base) insert = index;
        });
        if (insert >= 0) list.splice(insert + 1, 0, item);
        else list.push(item);
    }
    let previewPinned = false;
    // 记录持久化到节点属性：重新打开工作流/刷新页面后仍能在分类里看到缩略图
    const savedMedia = node.properties?.ad_h3_media_records;
    if (savedMedia && typeof savedMedia === "object") {
        for (const kind of ["image", "audio", "video"]) {
            if (Array.isArray(savedMedia[kind]))
                mediaRecords[kind] = savedMedia[kind].filter(item => item && item.file)
                    .map((item, index) => ({...item, serial: item.redraw ? 0 : (Number(item.serial) > 0 ? Number(item.serial) : index + 1)}));
        }
    }
    // 编号高水位单独持久化，不随删卡、清空预览或存档裁剪回收。
    // 键格式：kind:A/B端:组号:段号；旧版（含 reset_id 或只有三段）统一并入 A 端高水位。
    function rememberRedrawCounts() {
        node.properties ||= {};
        const saved = node.properties.ad_h3_redraw_counts;
        const counts = {};
        const absorb = (key, value) => {
            const count = Number(value) || 0;
            if (count <= 0) return;
            const parts = String(key).split(":");
            if (parts.length === 4 && (parts[1] === "A" || parts[1] === "B")) {
                counts[key] = Math.max(Number(counts[key]) || 0, count);
                return;
            }
            // 旧格式 kind:组:段[:reset_id]：不再按 reset_id 分批，只保留最大次数。
            if (parts.length === 3 || parts.length === 4) {
                const target = `${parts[0]}:A:${parts[1]}:${parts[2]}`;
                counts[target] = Math.max(Number(counts[target]) || 0, count);
            }
        };
        if (saved && typeof saved === "object" && !Array.isArray(saved))
            for (const [key, value] of Object.entries(saved)) absorb(key, value);
        for (const kind of ["image", "audio", "video"]) {
            for (const entry of mediaRecords[kind]) {
                if (!entry.redraw) continue;
                const key = redrawKey(kind,entry,redrawBase(entry));
                counts[key] = Math.max(0, Number(counts[key]) || 0, Number(entry.redraw_count) || 0);
            }
        }
        node.properties.ad_h3_redraw_counts = counts;
        return counts;
    }
    rememberRedrawCounts();
    // 抽卡编号归一化：同组同段同类型同 A/B 端内「第几次」必须唯一。
    // 已经唯一的编号保持原样（旧卡不重排），重复编号顺延到该段最大编号之后；
    // 这样重新打开工作流/刷新页面也会把历史重复编号修正为 R1-01-1、R1-01-2。
    function normalizeRedrawNumbers() {
        const entries = [];
        for (const kind of ["image", "audio", "video"])
            for (const record of mediaRecords[kind]) if (record?.redraw) entries.push({kind, record});
        const {changed} = normalizeRedrawCounts(entries);
        rememberRedrawCounts();
        for (const {record} of entries) record.label = recordLabel(record, 0);
        if (changed) persistMedia();
        return changed;
    }
    const persistMedia = () => {
        node.properties ||= {};
        rememberRedrawCounts();
        const strip = list => list.map(item => {
            const {thumb_b64, thumb_mime, ...rest} = item;
            return rest;
        });
        node.properties.ad_h3_media_records = {image: strip(mediaRecords.image), audio: strip(mediaRecords.audio), video: strip(mediaRecords.video)};
    };
    function enforceHistoryRetention() {
        const all = [...Object.values(mediaRecords).flat(), ...stripStore.segment, ...stripStore.merged,
            ...mergeIndex.values(), ...(node.properties.ad_h3_merged_previews || [])];
        const keep = retainedHistoryRounds(all, activeRoundNo());
        for (const kind of ["image","audio","video"]) mediaRecords[kind] = mediaRecords[kind].filter(record => keep.has(recordRoundNo(record)));
        stripStore.segment = stripStore.segment.filter(record => keep.has(recordRoundNo(record)));
        stripStore.merged = stripStore.merged.filter(record => keep.has(recordRoundNo(record)));
        for (const [key,record] of mergeIndex) if (!keep.has(recordRoundNo(record))) mergeIndex.delete(key);
        node.properties.ad_h3_merged_previews = (node.properties.ad_h3_merged_previews || []).filter(record => keep.has(recordRoundNo(record)));
        for (const round of expandedHistoryRounds) if (!keep.has(round)) expandedHistoryRounds.delete(round);
        node.properties.ad_h3_expanded_rounds = [...expandedHistoryRounds];
    }
    function restoreHistoryRecords() {
        // 只用仍保存的精确文件索引兜底；不扫描磁盘猜轮号，不复活已删除项。
        const known = new Set(Object.values(mediaRecords).flat().map(record => `${record.origin || "media"}/${record.file}`));
        for (const record of [...stripStore.segment,...mergeIndex.values()]) {
            const origin = record.origin || "session", key = `${origin}/${record.file}`;
            if (!record.file || known.has(key) || hiddenStrips.has(`segment:${key}`) || hiddenStrips.has(`segmentB:${key}`)) continue;
            const kind = inferMediaKind(record.file,record.kind);
            mediaRecords[kind].push({...record,origin,kind,serial:record.redraw?0:Number(record.serial)||Number(record.segment_index)||0});
            known.add(key);
        }
        if (showHistoryRounds) {
            for (const record of Object.values(mediaRecords).flat()) if (recordRoundNo(record)!==activeRoundNo()) expandedHistoryRounds.add(recordRoundNo(record));
            showHistoryRounds = false;
            node.properties.ad_h3_show_history = false;
        }
        enforceHistoryRetention();
        persistMedia();
    }
    function appendHistoryPager(target,group,refresh) {
        if (group.all.length <= MEDIA_DISPLAY_LIMIT || !group.open) return;
        const pager = document.createElement("div");
        pager.className = "ad-h3-history-control ad-h3-history-page";
        pager.style.cssText = "display:flex;gap:8px;align-items:center;flex-basis:100%;grid-column:1/-1;width:100%;padding:4px 0";
        for (const [text,delta] of [["上一页",-1],["下一页",1]]) {
            const button = document.createElement("button");button.textContent=text;
            button.disabled = delta<0 ? group.page===0 : (group.page+1)*MEDIA_DISPLAY_LIMIT>=group.all.length;
            button.onclick = event => {event.stopPropagation();historyPages.set(group.key,group.page+delta);refresh();};pager.append(button);
        }
        const text=document.createElement("span");text.textContent=`第 ${group.page+1} / ${Math.ceil(group.all.length/MEDIA_DISPLAY_LIMIT)} 页`;pager.append(text);target.append(pager);
    }
    // 工作流没存过分段缩略图时，用已保存的媒体记录兜底，保证重新打开后预览区仍有历史缩略图。
    if (!stripStore.segment.length && !stripStore.merged.length) {
        const fallback = [];
        for (const kind of ["image", "audio", "video"]) {
            for (const record of mediaRecords[kind]) {
                if (!record?.file) continue;
                fallback.push({file: record.file, origin: record.origin || "media",
                    label: record.label || (Number(record.serial) > 0 ? `第 ${record.serial} 段` : "历史生成"),
                    kind: record.kind || kind,
                    role: record.role === "B" ? "B" : "A",
                    round_id: record.round_id || "", round_no: recordRoundNo(record),
                    segment_index: Number(record.segment_index) || Number(record.serial) || 0});
            }
        }
        stripStore.segment = fallback.sort((a, b) => (a.segment_index || 0) - (b.segment_index || 0));
    }
    let activeTab = node.properties?.ad_h3_active_tab || "image";
    function setTab(kind, options = {}) {
        activeTab = kind;
        node._adH3TabKind = kind;
        try { node.properties ||= {}; node.properties.ad_h3_active_tab = kind; } catch {}
        for (const button of tabButtons) button.classList.toggle("active", button.dataset.tab === kind);
        for (const [name, panel] of Object.entries(panels)) if (panel) panel.hidden = name !== kind;
        for (const [name, record] of Object.entries(mediaRecords)) if (mediaLists[name]) mediaLists[name].parentElement && (panels[name].hidden = name !== kind);
        markCards();
        const videoActions = root.querySelector(".ad-h3-video-actions");
        if (videoActions) videoActions.style.display = kind === "video" ? "flex" : "none";
        node._adH3OnTab?.(kind);
        if (options.sync !== false) syncPreviewToTab(kind);
        // 切分类后的高度由 dom_layout.fitPreviewContent 的 ResizeObserver 按内容统一收敛，这里只通知重绘。
        node.setDirtyCanvas?.(true, true);
    }
    function showLastForTab(kind) {
        const saved = lastShown[kind];
        const records = mediaRecords[kind] || [];
        if (saved?.url) return show(saved, {auto: true, tabSynced: true});
        const record = records.at(-1);
        if (record?.file) return show({...record, kind, label: recordLabel(record, 0),
            url: mediaUrl(record.origin || "media", record.file)}, {auto: true, tabSynced: true});
        // 本分类没有可显示内容：收起画面与播放控件，避免其它分类的画面串进来。
        video.pause(); audioView.pause();
        video.removeAttribute("src"); video.load();
        audioView.removeAttribute("src"); audioView.load();
        imageView.removeAttribute("src");
        active = null; generationKey = "";
        title.textContent = kind === "image" ? "生成完成后在这里预览图片"
            : kind === "audio" ? "生成完成后在这里预览音频" : "生成完成后在这里预览素材";
        setMode("image");
        setIdle(true);
        return false;
    }
    function syncPreviewToTab(kind) {
        // 图片分类永远不显示播放控制栏；音频/视频分类只显示各自画面。
        if (active && categoryOf(active.kind) === kind) {
            setMode(active.kind === "audio" ? "audio" : active.kind === "image" ? "image" : "video");
            return true;
        }
        return showLastForTab(kind);
    }
    function setRedoTarget(record, button) {
        const resolved = record ? (Number(record.segment_index) || Number(record.serial) || 0) : 0;
        redoTarget = record ? {...record, segment_index: resolved} : null;
        resumeTarget = redoTarget ? {...redoTarget} : null;
        // 选中哪张预览只作为本段抽卡的内部目标，不再给缩略图加黄色高亮（用户 2026-10-28 要求去掉）。
        void button;
        const redoButton = root.querySelector('[data-loop="redo"]');
        if (redoButton) {
            redoButton.title = resolved
                ? `重新抽第 ${resolved} 段（已选中预览）；运行中请先点「停止」`
                : "重抽当前段；点下方预览可指定要重抽的段（运行中请先停止）";
        }
        if (record) {
            node._adH3ReviewStatus?.(resolved ? `已选第 ${recordRoundNo(record)||"旧版"} 组第 ${resolved} 段：「分段循环」从该组下一段续跑；「本段抽卡」仍归原组`
                                              : "该预览缺少段号，本段抽卡将重抽当前段");
        }
    }
    function renderMedia(kind) {
        const list = mediaLists[kind];
        if (!list) return;
        const owned = mediaCards[kind];
        const current = activeRoundNo();
        const all = mediaRecords[kind].filter(record => !current || recordRoundNo(record)===current);
        const page = Math.max(0,Math.min(mediaPages[kind],Math.ceil(all.length/MEDIA_DISPLAY_LIMIT)-1));
        const records = all.slice(page*MEDIA_DISPLAY_LIMIT,(page+1)*MEDIA_DISPLAY_LIMIT);
        const wanted = new Set();
        records.forEach((record, index) => {
            const key = mediaCardKey(kind, record);
            wanted.add(key);
            let button = owned.get(key);
            if (!button) {
                button = document.createElement("button");
                button.type = "button"; button.className = "ad-h3-media-card";
                const media = document.createElement(kind === "audio" ? "span" : "img");
                if (kind === "audio") {
                    media.className = "ad-h3-audio-icon"; media.textContent = "♪";
                } else {
                    media.loading = "lazy"; media.decoding = "async"; media.setAttribute("aria-hidden", "true");
                }
                const label = document.createElement("span"); label.className = "ad-h3-media-serial";
                const remove = document.createElement("span");
                remove.className = "ad-h3-media-remove"; remove.textContent = "×"; remove.title = "删除这条缩略图记录";
                button.append(media, label, remove);
                owned.set(key, button);
            }
            button.dataset.file = record.file || ""; button.dataset.origin = record.origin || "media";
            // 重抽结果：编号标黄，方便和普通结果区分。
            button.classList.toggle("redraw", !!record.redraw);
            button.title = record.redraw ? "本段抽卡结果（黄色编号）" : "";
            const labelText = recordLabel(record, index);
            const media = button.firstElementChild;
            let thumbURL = "";
            if (kind === "video") {
                // 使用后端首帧 webp 缩略图（24h 缓存）；不再为每张卡片创建 video 解码器/媒体请求。
                thumbURL = thumbnailUrl(record.origin || "media", record.file);
            } else if (kind === "image") {
                thumbURL = record.thumb_b64
                    ? `data:${record.thumb_mime || "image/webp"};base64,${record.thumb_b64}`
                    : thumbnailUrl(record.origin || "media", record.file);
            }
            if (thumbURL && media.dataset.thumbUrl !== thumbURL) {
                media.dataset.thumbUrl = thumbURL;
                media.src = thumbURL;
            }
            if (thumbURL) media.alt = String(record.file || "").split("/").pop() || labelText;
            button.children[1].textContent = labelText;
            const remove = button.lastElementChild;
            remove.onclick = event => {
                event.stopPropagation();
                clearRecordPreview(record, null);
            };
            button.classList.toggle("merge-selected", mergeSelection.has(keyOf(record)));
            button.onclick = event => {
                if (event?.ctrlKey || event?.metaKey) {
                    event.preventDefault(); event.stopPropagation();
                    toggleMergeSelection(record, kind, record.role === "B" ? "B" : "A");
                    return;
                }
                const pickedForAB = pickAB(record, kind);
                if (!pickedForAB && !abOverlay.hidden) exitAB();
                show({...record, kind, label: recordLabel(record, index), url: mediaUrl(record.origin || "media", record.file)});
                markActiveCard(record);
                setRedoTarget(record, button);
                // 点选只切换素材与本段抽卡目标；试听由播放器的播放按钮触发。
            };
            list.append(button);
        });
        // 只移除本轮不再需要的卡片；已有卡片不重建、不重新加载缩略图。
        for (const [key, button] of [...owned]) {
            if (wanted.has(key)) continue;
            button.remove(); owned.delete(key);
        }
        let empty = list.querySelector(".ad-h3-strip-empty");
        if (!records.length) {
            if (!empty) {
                empty = document.createElement("span");
                empty.className = "ad-h3-strip-empty";
            }
            empty.textContent = kind === "image" ? "暂无图像" : kind === "video" ? "暂无视频" : "暂无音频";
            list.append(empty);
        } else if (empty) empty.remove();
        for (const stale of list.querySelectorAll(".ad-h3-history-control")) stale.remove();
        const pageKey=`media:${kind}`;historyPages.set(pageKey,page);
        appendHistoryPager(list,{all,page,key:pageKey,open:true},()=>{mediaPages[kind]=historyPages.get(pageKey);renderMedia(kind);});
        const visible = mediaLists[kind]?.parentElement && !mediaLists[kind].parentElement.hidden;
        scheduleMediaResize(visible ? 200 : 240);
    }
    for (const button of tabButtons) button.onclick = () => setTab(button.dataset.tab);
    const clearButton = root.querySelector("[data-clear-preview]");
    if (clearButton) clearButton.addEventListener("click", async () => {
        // 只清空本轮进度与选用记录；预览内容、素材、分段、提示词和文件都保留。
        if (!window.confirm("归档当前轮并开始新一轮？（预览内容、素材、分段、提示词与已生成文件都保留，旧轮可在历史组回抽）")) return;
        try { if(await node._adH3Act?.("clear", {skipConfirm:true})===false)return; } catch { return; }
        previewPinned = false;
        if (node._adH3ReviewStatus) node._adH3ReviewStatus("已归档当前轮并进入下一轮；旧轮可在历史组回抽，预览内容与已生成文件保留。");
        node.setDirtyCanvas?.(true, true);
    });
    const clearMediaButton = root.querySelector("[data-clear-media-preview]");
    if (clearMediaButton) clearMediaButton.addEventListener("click", () => {
        // 只清当前分类（图片/音频/视频）的预览记录，不删文件、不动进度；清空后主预览回到黑屏空闲态。
        const kind = activeTab || "image";
        const names = {image: "图片", audio: "音频", video: "视频"};
        const sameKind = record => inferMediaKind(record?.file, record?.kind) === kind;
        if (processingRecord && sameKind(processingRecord)) processingRecord = null;
        const stripMatches = ["segment", "segmentB", "merged"].flatMap(key => {
            const source = key === "segment" ? [...stripStore.segment.filter(record => record.role !== "B"), ...stripRecords.segment]
                : key === "segmentB" ? [...stripStore.segment.filter(record => record.role === "B"), ...stripRecords.segmentB]
                : [...stripStore.merged, ...stripRecords.merged];
            return source.filter(sameKind).map(record => ({key, record}));
        });
        if (!(mediaRecords[kind] || []).length && !stripMatches.length && !(kind === "video" && mergeIndex.size)) {
            lastShown[kind] = null;
            active = null; generationKey = "";
            showLastForTab(kind);
            previewPinned = false;
            node.setDirtyCanvas?.(true, true);
            if (node._adH3ReviewStatus) node._adH3ReviewStatus(`「${names[kind]}」预览已经是空的。`);
            return;
        }
        if (!window.confirm(`清空「${names[kind]}」分类的预览内容？（只清预览记录，不删除文件，不影响生成进度）`)) return;
        const hadRedoTarget = !!redoTarget && (mediaRecords[kind] || []).some(item =>
            item.file === redoTarget.file && (item.origin || "media") === (redoTarget.origin || "media"));
        // 清除的分段/合并记录也持久化隐藏，防止重新打开后被会话进度补回。
        for (const {key, record} of stripMatches) hiddenStrips.add(stripKey(key, record));
        for (const record of mediaRecords[kind] || []) {
            hiddenStrips.add(stripKey("segment", record));
            hiddenStrips.add(stripKey("segmentB", record));
        }
        for (const row of latestSessionState?.segments || []) for (const version of row.versions || []) {
            const record = {origin:"session", file:`${latestSessionState.id}/${version.file}`, kind:version.media_kind};
            if (sameKind(record)) {
                hiddenStrips.add(stripKey("segment", record));
                hiddenStrips.add(stripKey("segmentB", record));
            }
        }
        persistHiddenStrips();
        node.graph?.change?.(); // 通知工作流记录这次清空，刷新时使用新的预览状态。
        stripStore.segment = stripStore.segment.filter(record => !sameKind(record));
        stripStore.merged = stripStore.merged.filter(record => !sameKind(record));
        if (kind === "video") {mergeIndex.clear();persistMergeIndex();}
        persistStripPreviews();
        mediaRecords[kind] = [];
        lastShown[kind] = null;
        if (hadRedoTarget) setRedoTarget(null, null);
        persistMedia();
        renderMedia(kind);
        syncStripFromMedia();
        renderRecords(merges, stripStore.merged, "merged");        // 清空预览后主预览统一回到大黑屏空闲态（即使当前显示的是其它分类/分段预览）。
        active = null; generationKey = "";
        showLastForTab(kind);
        previewPinned = false;
        node.setDirtyCanvas?.(true, true);
        if (node._adH3ReviewStatus) node._adH3ReviewStatus(`已清空「${names[kind]}」预览，文件与进度保留。`);
    });
    const runningStatuses = ["queued", "running", "merging"];
    const sessionIsRunning = () => !!latestSessionState && runningStatuses.includes(latestSessionState.status);
    const interruptNow = () => { void interruptOwnedPrompt(api,node).catch(error=>node._adH3Hint?.(error.message,true)); };
    let resolvingStart = false;
    for (const button of root.querySelectorAll("[data-loop]")) button.addEventListener("click", async () => {
        const command = button.dataset.loop;
        if (command === "start" && node.properties?.ad_execution_round?.id && node._adH3RefreshState) {
            if(resolvingStart) return;
            resolvingStart=true;
            try {await node._adH3RefreshState();}
            catch(error) {node._adH3Hint?.(error.message,true);return;}
            finally {resolvingStart=false;}
        }
        if (node._adH3AffectedRunning && command !== "pause") {
            node._adH3Hint?.("正在运行所选段，请等待完成或先点停止。", true); return;
        }
        // 用户主动开始生成/分段循环/批准/合并时解除预览固定，新结果照常自动显示。
        if (["start", "auto", "approve", "merge"].includes(command)) previewPinned = false;
        if (command === "start") {
            if (latestSessionState?.draft_trial) {
                node._adH3Hint?.("本段抽卡尚未结束，完成后再继续原循环。",false); return;
            }
            // 普通开始：当前段仍在运行/排队时不再提交，避免同段生成两次（2026-11-02 用户反馈）。
            if (!batchResume && sessionIsRunning()) {
                node._adH3Hint?.("当前段正在运行，请等待完成或先点「停止」再开始。", true);
                return;
            }
            // 点「停止」时如果分段循环正在运行：抽卡只是预览备选，点「开始」续接原来的分段循环位置。
            if (batchResume || latestSessionState?.execution_round?.batch && latestSessionState.status !== "completed") {
                // 抽卡还在生成（预览结果尚未到达）时点开始：提示稍后再点，不抢跑。
                if (sessionIsRunning()) {
                    node._adH3Hint?.("本段抽卡的生成还未结束，完成后点「开始」续接分段循环", false);
                    return;
                }
                batchResume = false; redrawPending = null; redrawReview = null;
                previewPinned = false;
                node._adH3Hint?.("已续接分段循环，从停止处继续…", false);
                if (typeof node._adH3Batch === "function") { void node._adH3Batch({merge:false,resumeSelection:false}); return; }
            }
            // 分段循环已完成：点「开始」归档本轮并进入下一轮（保留素材/分段/提示词/已生成文件），从第 1 段重新开始。
            if (latestSessionState && latestSessionState.status === "completed") {
                redrawPending = null; redrawReview = null; batchResume = false;
                node._adH3Hint?.("本轮已完成，正在归档本轮并开始新一轮（文件与预览保留）…", false);
                if (typeof node._adH3Act === "function") {
                    Promise.resolve(node._adH3Act("clear", {skipConfirm:true}))
                        .then(result => {
                            if (result !== false && typeof node._adH3Run === "function") node._adH3Run();
                        })
                        .catch(() => {});
                    return;
                }
            }
            // 兼容旧会话：当前段有效但尚未自动接受时，点「开始」先接受再继续；新会话生成即通过。
            if (latestSessionState && (latestSessionState.status === "review" || latestSessionState.status === "paused" && latestSessionState.segments?.[latestSessionState.cursor]?.valid)) {
                const currentRow = latestSessionState.segments?.[latestSessionState.cursor];
                if (currentRow?.valid && typeof node._adH3Act === "function") {
                    // 停止后点开始：一律用当前全部设置重跑当前段（改种子/提示词/参数/参考都生效），
                    // 不再把停止前的旧结果当成已接受而跳过。待审核状态才按“接受继续”处理。
                    const resumeCommand = latestSessionState.status === "paused" ? "redo" : "approve";
                    node._adH3Hint?.(latestSessionState.status === "paused"
                        ? "已停止：正在用当前设置重新生成当前段…" : "正在继续已生成的上一段…", false);
                    Promise.resolve(node._adH3Act(resumeCommand))
                        .catch(error => {
                            node._adH3Hint?.("继续上一段失败：" + (error?.message || String(error)), true);
                        });
                    return;
                }
                node._adH3Hint?.("当前段已有结果：点「开始」会接受该结果并继续下一段。", false);
                return;
            }
            if (typeof node._adH3Act === "function") { void node._adH3Act("start"); return; }
            const app = window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app);
            if (typeof node._adH3Run === "function") node._adH3Run();
            else try { void app?.queuePrompt?.(0, 1); } catch {}
            return;
        }
        if (command === "auto" && typeof node._adH3Batch === "function") {
            redrawPending = null; redrawReview = null; batchResume = false;
            void node._adH3Batch({merge:false});
            return;
        }
        if (command === "pause") {
            // 停止：中断当前生成，并让分段循环整体停下来待命（后台状态置为暂停）。
            // 记住停止时前端分段循环是否在运行，供「开始」续接。
            // 本机状态先行标记为 paused，用户点完「停止」可以立刻点预览再「本段抽卡」，不用等轮询刷新。
            batchResume = batchResume || !!node._adH3BatchRunning;
            const stopping=++redrawRequest;
            redrawPending = null; previewPinned = false;
            stopPending = true;
            if (latestSessionState) latestSessionState = {...latestSessionState, status: "paused"};
            if (typeof node._adH3Act === "function") {
                Promise.resolve(node._adH3Act("stop"))
                    .then(result => { stopPending = false; if (stopping===redrawRequest && result === false) interruptNow(); })
                    .catch(() => {stopPending = false; if(stopping===redrawRequest)interruptNow();});
            } else { stopPending = false; interruptNow(); }
            return;
        }
        if (command === "redo") {
            // 停止请求还没返回时先不发抽卡，避免和中断竞态。
            if (stopPending) {
                node._adH3Hint?.("正在停止，请稍候…", true);
                return;
            }
            // 运行中需要先点「停止」：流程待命后再抽卡。
            if (sessionIsRunning()) {
                node._adH3Hint?.("分段循环运行中：请先点「停止」，流程待命后再点「本段抽卡」", true);
                return;
            }
            // 抽卡防连点：15 秒内重复点击不重复排队。
            if (node._adH3RedoPending && Date.now() - (node._adH3RedoAt || 0) < 15000) {
                node._adH3Hint?.("本段抽卡正在进行，请等结果出来再抽。", true);
                return;
            }
            node._adH3RedoPending = true; node._adH3RedoAt = Date.now();
            const targetIndex = redoTarget && Number(redoTarget.segment_index) > 0
                ? Number(redoTarget.segment_index) - 1 : null;
            const pendingIndex = targetIndex !== null ? targetIndex + 1
                : Number(latestSessionState?.cursor ?? 0) + 1;
            // 抽卡结果编号以被抽段的原始序号为底（如 #02-1/#02-2），不占用正常生成的整数序号。
            const base = redoTarget
                ? (Number(redoTarget.redraw_base) || displayedBase(redoTarget) || Number(redoTarget.segment_index) || pendingIndex)
                : pendingIndex;
            const request=++redrawRequest;
            node._adH3ActionCancelled=false;
            redrawPending = {index: pendingIndex, base: Number(base), exact: targetIndex !== null, round_no:redoTarget ? recordRoundNo(redoTarget) : activeRoundNo()};
            redrawReview = null;
            previewPinned = false;  // 抽卡结果要自动显示
            if (typeof node._adH3Act === "function") {
                node._adH3Hint?.(`正在本段抽卡（第 ${base} 段，新结果将作为该段当前选用）…`, false);
                Promise.resolve(node._adH3Act("redo_preview", targetIndex === null ? undefined : {index: targetIndex,preview:{...redoTarget},group_previews:[...Object.values(mediaRecords).flat(),...stripStore.segment].filter(r=>recordRoundNo(r)===recordRoundNo(redoTarget))}))
                    .then(result => { if(request!==redrawRequest)return; if (result === false && !node._adH3ActionCancelled) {
                        redrawPending = null; node._adH3RedoPending = false;
                        node._adH3Hint?.("本段抽卡未完成："+(node._adH3LastActionError||"本次任务没有成功加入队列或执行失败。请查看 ComfyUI 运行记录后重试。"),true);
                    } })
                    .catch(error => { if(request!==redrawRequest)return; redrawPending = null; node._adH3Hint?.("本段抽卡未成功加入队列："+(error?.message||String(error)),true); });
            } else if (node._adH3ReviewStatus) node._adH3ReviewStatus("请先连接「当前段上下文」再使用", true);
            return;
        }
        if (command === "approve" && sessionIsRunning()) {node._adH3Hint?.("当前任务尚未结束，请等待完成或先点「停止」。",true);return;}
        if (command === "approve" || command === "clear" || command === "new_group") {redrawRequest++;redrawPending=null;redrawReview=null;batchResume=false;}
        if (command === "merge") { const menu=root.querySelector("[data-merge-menu]"); if(menu) menu.hidden=!menu.hidden; return; }
        if (typeof node._adH3Act === "function") void node._adH3Act(command, command === "pause" ? { skipConfirm: true } : undefined);
        else if (node._adH3ReviewStatus) node._adH3ReviewStatus("请先连接「当前段上下文」再使用", true);
    });
    const keyOf = record => `${record.role||"A"}|${record.origin || "session"}/${record.file}`;
    function playlistVideos() {
        // 两轮结果共存时只循环播放当前轮；没有轮号（旧数据/未记录）时保留旧行为，全部参与。
        const currentRound = activeRoundNo();
        return stripStore.segment.filter(record => inferMediaKind(record.file,record.kind)==="video"
            && (!currentRound || recordRoundNo(record)===currentRound)
            && !hiddenStrips.has(stripKey("segment",record)))
            .slice().sort((a,b)=>{
                const left=numberOrder(a),right=numberOrder(b);
                for(let i=0;i<Math.max(left.length,right.length);i++){
                    const diff=(left[i]||0)-(right[i]||0);
                    if(diff) return diff;
                }
                return 0;
            });
    }
    function setPlaylistEnabled(enabled) {
        playlistEnabled=!!enabled;
        if (!playlistEnabled) { playlistRevision++; playlistKey=""; }
        playlistButton?.classList.toggle("active",playlistEnabled);
        playlistButton?.setAttribute("aria-pressed",String(playlistEnabled));
    }
    function playPlaylistRecord(record) {
        if (!record || disposed || !playlistEnabled) return;
        const pending=++playlistRevision;
        playlistKey=keyOf(record);
        previewPinned=true;
        show({...record,kind:"video",key:playlistKey,url:mediaUrl(record.origin||"session",record.file)},{auto:true});
        markActiveCard(record);
        void video.play().catch(error=>{
            if (pending!==playlistRevision || disposed || !playlistEnabled) return;
            setPlaylistEnabled(false);
            node._adH3Hint?.("该视频暂时无法播放，循环播放已停止："+(error?.message||"请重新选择片段"),true);
        });
    }
    playlistButton?.addEventListener("click",()=>{
        if (playlistEnabled) {setPlaylistEnabled(false);return;}
        const records=playlistVideos();
        if (!records.length) {node._adH3Hint?.("分段生成中暂无可循环播放的视频。",true);return;}
        setPlaylistEnabled(true);
        playPlaylistRecord(records.find(record=>active?.key===keyOf(record))||records[0]);
    });
    function markCards() {
        for (const [key, button] of cards) button.classList.toggle("selected", button._recordKey === active?.key);
    }
    function setMode(kind) {
        const isVideo = !kind || kind === "video" || kind === "segment";
        video.hidden = !isVideo;
        imageView.hidden = kind !== "image";
        audioView.hidden = kind !== "audio";
        transport.style.display = isVideo || kind === "audio" ? "" : "none";
        const frameStepBox = root.querySelector(".ad-h3-frame-step");
        if (frameStepBox) frameStepBox.hidden = !isVideo;
        root.classList.toggle("ad-h3-audio-mode", kind === "audio");
        updateTime();
    }
    function openLightbox(info) {
        const src = info?.url;
        if (!src) return;
        const isVideo = info?.kind === "video" || info?.kind === "segment";
        const overlay = document.createElement("div");
        overlay.className = "ad-h3-lightbox";
        const mediaEl = document.createElement(isVideo ? "video" : "img");
        if (isVideo) {
            mediaEl.className = "ad-h3-lightbox-video";
            mediaEl.controls = true; mediaEl.autoplay = true; mediaEl.playsInline = true; mediaEl.loop = false;
            mediaEl.src = src;
            mediaEl.addEventListener("error", () => { hint.textContent = "视频暂时无法播放：请重试播放或下载文件检查；仍无法播放再考虑重新生成。"; });
        } else {
            mediaEl.className = "ad-h3-lightbox-img";
            mediaEl.src = src;
        }
        let scale = 1, offsetX = 0, offsetY = 0, dragging = false, startX = 0, startY = 0;
        const apply = () => { if (!isVideo) mediaEl.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`; };
        const close = () => { cleanup(); overlay.remove(); };
        overlay.onclick = event => { if (event.target === overlay) close(); };
        if (!isVideo) {
            overlay.addEventListener("wheel", event => {
                event.preventDefault();
                scale = Math.min(8, Math.max(0.2, scale * (event.deltaY < 0 ? 1.12 : 0.9)));
                apply();
            }, { passive: false });
            mediaEl.addEventListener("mousedown", event => {
                dragging = true; startX = event.clientX - offsetX; startY = event.clientY - offsetY;
                mediaEl.style.cursor = "grabbing"; event.preventDefault();
            });
        }
        const move = event => {
            if (isVideo || !dragging) return;
            offsetX = event.clientX - startX; offsetY = event.clientY - startY; apply();
        };
        const up = () => { if (!isVideo) { dragging = false; mediaEl.style.cursor = "grab"; } };
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", up);
        const keyHandler = event => { if (event.key === "Escape") close(); };
        window.addEventListener("keydown", keyHandler);
        const hint = document.createElement("div");
        hint.className = "ad-h3-lightbox-hint";
        hint.textContent = isVideo ? "点全屏按钮可全屏播放 · Esc 关闭" : "滚轮缩放 · 按住拖动平移 · 点全屏按钮可全屏 · Esc 关闭";
        const download = document.createElement("button");
        download.type = "button"; download.className = "ad-h3-lightbox-download"; download.textContent = "选择文件夹下载";
        download.onclick = async event => {
            event.stopPropagation();
            try {
                const saved = await saveMediaToChosenPath(info, saveIndexFor(info));
                hint.textContent = saved.cancelled ? "未选择保存位置，已取消保存" : `已保存：${saved.path}`;
            } catch (error) { hint.textContent = "保存失败：" + error.message; }
        };
        const closeButton = document.createElement("button");
        closeButton.type = "button"; closeButton.className = "ad-h3-lightbox-close"; closeButton.textContent = "×";
        closeButton.onclick = event => { event.stopPropagation(); close(); };
        const fullscreen = document.createElement("button");
        fullscreen.type = "button"; fullscreen.className = "ad-h3-lightbox-fullscreen"; fullscreen.textContent = "全屏";
        fullscreen.onclick = event => {
            event.stopPropagation();
            void toggleFullscreen(overlay).then(syncLightboxFullscreen);
        };
        const syncLightboxFullscreen = () => { fullscreen.textContent = document.fullscreenElement === overlay ? "退出全屏" : "全屏"; };
        document.addEventListener("fullscreenchange", syncLightboxFullscreen);
        overlay.append(mediaEl, download, fullscreen, closeButton, hint);
        overlay.dataset.role = "lightbox";
        document.body.append(overlay);
        const cleanup = () => {
            if (isVideo) releaseMedia(mediaEl); else mediaEl.removeAttribute("src");
            lightboxClosers.delete(close);
            document.removeEventListener("fullscreenchange", syncLightboxFullscreen);
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
            window.removeEventListener("keydown", keyHandler);
        };
        const observer = new MutationObserver(() => { if (!overlay.isConnected) { cleanup(); observer.disconnect(); } });
        observer.observe(document.body, {childList: true});
        lightboxClosers.add(close);
    }
    const originalShow = show;
    imageView.addEventListener("click", () => {
        if (imageView.hidden || !(imageView.getAttribute("src") || "")) return;
        openLightbox(active || {url: imageView.src, origin: "media", file: ""});
    });

    let videoWatchdog = 0, videoRetriedOnce = false;
    const armVideoWatchdog = () => {
        if (videoWatchdog) clearTimeout(videoWatchdog);
        videoWatchdog = setTimeout(() => {
            if (disposed || !active || active.kind !== "video") return;
            if (video.readyState >= 1 || !video.getAttribute("src")) return;
            if (!videoRetriedOnce) {
                videoRetriedOnce = true;
                try {
                    const retryUrl = new URL(video.getAttribute("src"), location.href);
                    retryUrl.searchParams.set("retry", String(Date.now()));
                    video.src = retryUrl.href;
                    video.load();
                } catch (error) { /* 保持原地址 */ }
                armVideoWatchdog();
                return;
            }
            title.textContent = `${active.label || "预览"} · 预览视频加载失败：可能是预览服务暂时繁忙或文件路径不可用。请先确认没有正在运行的任务，再刷新页面重试`;
            node._adH3Hint?.("预览视频加载失败：请确认任务已结束且文件仍在，再刷新页面重试；工作流未保存时不要刷新。", true);
        }, 4000);
    };
    video.addEventListener("loadedmetadata", () => { videoRetriedOnce = false; if (videoWatchdog) { clearTimeout(videoWatchdog); videoWatchdog = 0; } });
    video.addEventListener("error", () => { if (videoWatchdog) { clearTimeout(videoWatchdog); videoWatchdog = 0; } });
    function show(info, options = {}) {
        if (disposed || !info?.url) return false;
        const kind = info.kind === "image" ? "image" : info.kind === "audio" ? "audio" : "video";
        const category = categoryOf(kind);
        if (playlistEnabled && (kind!=="video" || !playlistVideos().some(record=>keyOf(record)===keyOf(info)))) setPlaylistEnabled(false);
        // 显示内容与分类保持一致：显示视频/音频时切到对应分类，图片分类只显示图片。
        if (options.tabSynced !== true && activeTab !== category) setTab(category, {sync: false});
        // 只有用户手动点选预览才固定画面；自动结果默认不改变用户正在查看的内容（除非未被固定）。
        if (options.auto !== true) {
            previewPinned = true;
            mergeSelection.clear();
            processingRecord = {...info, kind, label: info.label || recordLabel(info, 0)};
            paintMergeSelection();
        }
        video.pause(); audioView.pause(); revision++;
        active = {...info, kind, key: info.key || `${info.origin || "session"}/${info.file || info.url}`};
        lastShown[category] = {...active};
        title.textContent = info.label || "素材预览";
        previewTools.hidden = false;
        initialIdle = false;
        setIdle(false);
        markCards();
        if (kind === "image") {
            setMode("image");
            if (imageView.getAttribute("src") !== info.url) imageView.src = info.url;
        } else if (kind === "audio") {
            setMode("audio");
            mute.textContent = audioView.muted ? "取消静音" : "静音";
            if (audioView.getAttribute("src") !== info.url) { audioView.src = info.url; audioView.load(); }
        } else {
            setMode("video");
            // 切换素材不自动解除静音；用户的静音选择保持不变。
            mute.textContent = video.muted ? "取消静音" : "静音";
            if (video.getAttribute("src") !== info.url) { video.src = info.url; video.load(); }
            else video.currentTime = 0;
            armVideoWatchdog();
            const mediaRef = {origin: info.origin || "media", file: info.file || ""};
            fetchMediaFps(mediaRef).then(fps => { activeFps = fps > 0 ? fps : 24; updateTime(); });
        }
        return true;
    }
    const playingMedia = () => active?.kind === "audio" ? audioView : video;
    const duration = () => Number.isFinite(playingMedia().duration) ? playingMedia().duration : 0;
    let activeFps = 24;
    function updateTime() {
        const media = playingMedia(), rate = active?.kind === "audio" ? 1000 : activeFps;
        seek.max = String(Math.max(0, Math.round(duration()*rate)-1));
        seek.value = String(Math.round((media.currentTime || 0)*rate));
        time.textContent = `${(media.currentTime || 0).toFixed(2)} / ${duration().toFixed(2)} 秒`;
    }
    video.onloadedmetadata = updateTime; video.ontimeupdate = updateTime;
    video.onplay = () => { play.textContent = "暂停"; };
    video.onpause = () => { play.textContent = "播放"; };
    video.onended = () => {
        play.textContent = "播放";
        if (!playlistEnabled || disposed || active?.key!==playlistKey) return;
        const records=playlistVideos();
        if (!records.length) {setPlaylistEnabled(false);return;}
        const index=records.findIndex(record=>keyOf(record)===playlistKey);
        playPlaylistRecord(records[(index+1)%records.length]);
    };
    audioView.onloadedmetadata = audioView.ontimeupdate = updateTime;
    audioView.onplay = () => { play.textContent = "暂停"; };
    audioView.onpause = audioView.onended = () => { play.textContent = "播放"; };
    video.onerror = () => { title.textContent = `${active?.label || "预览"} · 文件暂时无法播放：请重试播放或下载文件检查；仍无法播放再考虑重新生成。`; };
    play.onclick = () => {
        if (!active) return;
        const media = playingMedia();
        if (media.paused) void media.play().catch(() => { title.textContent = "暂时无法播放，请重新选择片段"; });
        else media.pause();
    };
    // 整个视频画面（含留黑区域）可点按；原生控制条仍由浏览器处理，避免一次点击反复切换。
    root.querySelector(".ad-h3-screen").addEventListener("click", event => {
        if (event.defaultPrevented || event.button !== 0 || !active || active.kind !== "video" || video.hidden || !abOverlay.hidden) return;
        if (event.target.closest("button,input,select,a,.ad-h3-player-controls,.ad-h3-frame-step,.ad-h3-ab-overlay")) return;
        if (event.target === video && video.controls) {
            const rect = video.getBoundingClientRect();
            const scale = rect.height / (video.offsetHeight || rect.height || 1);
            if (event.clientY >= rect.bottom - 44 * scale) return;
        }
        event.preventDefault(); event.stopPropagation();
        play.onclick();
    });
    video.style.cursor = "pointer";
    imageView.style.cursor = "default";
    root.querySelectorAll("[data-frame]").forEach(button => button.onclick = () => {
        const fps = activeFps > 0 ? activeFps : 24;
        video.pause();
        video.currentTime = Math.min(Math.max(0, duration() - 1 / fps), Math.max(0, (Math.round(video.currentTime * fps) + Number(button.dataset.frame)) / fps));
    });
    seek.oninput = () => { const media=playingMedia();media.pause();media.currentTime = Number(seek.value) / (active?.kind === "audio" ? 1000 : activeFps > 0 ? activeFps : 24); };
    mute.onclick = () => { const media=playingMedia();media.muted = !media.muted; mute.textContent = media.muted ? "取消静音" : "静音"; };
    root.querySelector("[data-rate]").onchange = event => { playingMedia().playbackRate = Number(event.target.value); };
    root.querySelector("[data-fullscreen]").onclick = event => { event.stopPropagation(); void toggleFullscreen(root.querySelector(".ad-h3-screen")); };
    function renderRecords(target, records, kind) {
        const stripKind = kind === "merged" ? "merged" : kind === "segmentB" ? "segmentB" : "segment";
        stripRecords[stripKind] = records;
        if (stripKind==="segment" && playlistEnabled && !playlistVideos().length) setPlaylistEnabled(false);
        const allVisible = records.filter(record => !hiddenStrips.has(stripKey(stripKind, record)));
        const currentRound = activeRoundNo();
        const groups = stripKind === "merged"
            ? previewHistoryGroups(allVisible.map(record=>({...record,round_no:-1})), -1, new Set(), historyPages, stripKind)
                .map(group=>({...group,visible:allVisible.slice(group.page*MEDIA_DISPLAY_LIMIT,(group.page+1)*MEDIA_DISPLAY_LIMIT),all:allVisible,current:true}))
            : previewHistoryGroups(allVisible,currentRound,expandedHistoryRounds,historyPages,stripKind);
        const visible = groups.flatMap(group=>group.visible);
        const cardKey = record => `${stripKind}|${keyOf(record)}`;
        const wanted = new Set(visible.map(cardKey));
        for (const [key, button] of cards) if (button.parentElement === target && !wanted.has(key)) {button.remove(); cards.delete(key);}
        // 没有记录时给一行提示，避免只显示空行。
        let placeholder = target.querySelector(".ad-h3-strip-empty");
        if (!visible.length) {
            if (!placeholder) {
                placeholder = document.createElement("span");
                placeholder.className = "ad-h3-strip-empty";
                target.append(placeholder);
            }
            placeholder.textContent = kind === "merged" ? "暂无合并结果" : kind === "segmentB" ? "暂无B输入内容" : "暂无分段";
        } else if (placeholder) {
            placeholder.remove();
        }
        for (const record of visible) {
            const key = cardKey(record);
            const itemKind = inferMediaKind(record.file, record.kind);
            const wantsImage = itemKind === "image";
            let button = cards.get(key);
            if (button && ((button.dataset.thumbKind === "image") !== wantsImage)) {
                button.remove(); cards.delete(key); button = null;
            }
            if (!button) {
                button = document.createElement("button"); button.type = "button"; button.className = "ad-h3-preview-thumb";
                if (!button.style.position) button.style.position = "relative";
                button.dataset.thumbKind = wantsImage ? "image" : "video";
                const thumb = document.createElement("img");
                thumb.alt = ""; thumb.loading = "lazy"; thumb.decoding = "async";
                thumb.setAttribute("aria-hidden", "true");
                const label = document.createElement("span");
                const order = document.createElement("span");
                order.className = "ad-h3-merge-order";
                order.style.cssText = "position:absolute;left:2px;top:2px;min-width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;font-weight:700;border-radius:8px;background:#ffcf4d;color:#111;padding:0 3px;display:none;pointer-events:none;z-index:2;";
                const remove = document.createElement("span");
                remove.className = "ad-h3-preview-remove"; remove.textContent = "×"; remove.title = "删除这条预览记录（不删除文件）";
                remove.onclick = event => { event.stopPropagation(); hideStripRecord(stripKind, record); };
                button.append(thumb, remove, order, label); cards.set(key, button);
            }
            const thumbURL = record.thumb_b64
                ? `data:${record.thumb_mime || "image/webp"};base64,${record.thumb_b64}`
                : thumbnailUrl(record.origin || "session", record.file);
            if (button.firstElementChild.dataset.thumbUrl !== thumbURL) {
                button.firstElementChild.dataset.thumbUrl = thumbURL;
                button.firstElementChild.src = thumbURL;
            }
            button.lastElementChild.textContent = record.label; button.title = record.label; button.setAttribute("aria-label", record.label);
            button._mergeKey = keyOf(record); button._recordKey = keyOf(record);
            button.classList.toggle("redraw", !!record.redraw);
            button.classList.toggle("merge-selected", mergeSelection.has(keyOf(record)));
            button.onclick = event => {
                // Ctrl/Cmd + 点击：多选要合并的视频片段（外框高亮），不改变当前预览。
                if (event?.ctrlKey || event?.metaKey) {
                    event.preventDefault(); event.stopPropagation();
                    toggleMergeSelection(record, itemKind, stripKind === "segmentB" ? "B" : stripKind === "segment" ? "A" : "");
                    return;
                }
                const pickedForAB = pickAB({...record, kind: itemKind}, itemKind);
                if (!pickedForAB && !abOverlay.hidden) exitAB();
                mergeSelection.clear();
                processingRecord = {...record, kind: itemKind, label: record.label || recordLabel(record, 0)};
                paintMergeSelection();
                if (!pickedForAB && stripKind==="segment" && itemKind==="video" && playlistEnabled) playPlaylistRecord(record);
                else show({...record, key: keyOf(record), kind: itemKind, url: mediaUrl(record.origin || "session",record.file)});
                // 分段生成A/B缩略图：点选即成为「本段抽卡」目标（合并片段无段号，只预览）。
                if (stripKind === "segment" || stripKind === "segmentB") {
                    setRedoTarget({...record, kind: itemKind}, button);
                    const current = activeRoundNo(), cardRound = recordRoundNo(record);
                    if (current > 0 && cardRound > 0 && cardRound !== current) {
                        node._adH3Hint?.(`已选第 ${cardRound} 组第 ${recordSegmentNo(record)} 段；「分段循环」从该组下一段续跑，「本段抽卡」仍归第 ${cardRound} 组`, false);
                    }
                }
            };
            target.append(button);
        }
        for (const stale of target.querySelectorAll(".ad-h3-history-control,.ad-h3-round-toggle")) stale.remove();
        for (const group of groups) {
            if (!group.current && stripKind!=="merged") {
                const toggle=document.createElement("button");
                toggle.className="ad-h3-round-toggle ad-h3-history-control";toggle.dataset.round=String(group.round);
                toggle.setAttribute("aria-expanded",String(group.open));
                toggle.style.cssText="flex-basis:100%;width:100%;text-align:left;margin:4px 0 2px;padding:3px 8px;font-size:12px;background:transparent;border:0;border-top:1px solid rgba(255,255,255,.16);cursor:pointer;color:inherit";
                toggle.textContent=`${group.open?"▾":"▸"} ${group.round>0?`第 ${group.round} 组历史`:'旧版历史（轮号未记录）'} · ${group.all.length} 条`;
                toggle.onclick=event=>{event.preventDefault();event.stopPropagation();if(expandedHistoryRounds.has(group.round))expandedHistoryRounds.delete(group.round);else expandedHistoryRounds.add(group.round);node.properties.ad_h3_expanded_rounds=[...expandedHistoryRounds];renderRecords(segments,stripStore.segment.filter(r=>r.role!=="B"),"segment");renderRecords(segmentsB,stripStore.segment.filter(r=>r.role==="B"),"segmentB");node.graph?.change?.();};
                target.append(toggle);
            }
            for (const record of group.visible) {
                const button=cards.get(cardKey(record));if(button)target.append(button);
            }
            appendHistoryPager(target,group,()=>renderRecords(target,records,kind));
        }
        markCards();
    }
    // 各类卡片 X 都清除该条预览的全部显示记录；不弹确认、不删除文件或进度。
    function clearRecordPreview(record, stripKind = null) {
        const origin = record.origin || (stripKind ? "session" : "media");
        const same = item => sameResult(item,{...record,origin,role:item?.role});
        const kind = inferMediaKind(record.file, record.kind);
        const all = stripKind ? (stripRecords[stripKind] || []).filter(item => !hiddenStrips.has(stripKey(stripKind,item))) : mediaRecords[kind];
        const position = all.findIndex(same);
        const remaining = all.filter(item => !same(item));
        const removedActive = same(active);
        const canonical = {...record, origin};
        for(const alias of resultAliases(canonical))for(const kind of ["segment","segmentB","merged"])hiddenStrips.add(`${kind}:${alias}`);
        persistHiddenStrips();
        for (const mediaKind of ["image","audio","video"]) {
            mediaRecords[mediaKind] = mediaRecords[mediaKind].filter(item => !same(item));
            if (same(lastShown[mediaKind])) lastShown[mediaKind] = null;
            renderMedia(mediaKind);
        }
        if (same(redoTarget)) setRedoTarget(null,null);
        if (same(processingRecord)) processingRecord = null;
        for(const [key,item] of mergeSelection)if(same(item))mergeSelection.delete(key);
        paintMergeSelection();
        stripStore.segment = stripStore.segment.filter(item => !same(item));
        stripStore.merged = stripStore.merged.filter(item => !same(item));
        for (const [key,item] of mergeIndex) if (same(item)) {
            mergeIndex.delete(key);
            const alternatives = mediaRecords.video.filter(value=>mergeIndexKey(value)===key);
            if (alternatives.length) rememberMergeRecord(alternatives.at(-1));
        }
        persistMergeIndex();
        persistMedia(); persistStripPreviews();
        renderRecords(segments,stripStore.segment.filter(record => record.role !== "B"),"segment");
        renderRecords(segmentsB,stripStore.segment.filter(record => record.role === "B"),"segmentB");
        renderRecords(merges,stripStore.merged,"merged");
        // 清除当前条时主预览自动跳到下一张；末尾退回剩余最后一张。
        if (removedActive) {
            const next = remaining[Math.min(Math.max(0,position),remaining.length-1)];
            if (next) {
                previewPinned = true;
                const itemKind = inferMediaKind(next.file,next.kind);
                if (playlistEnabled && itemKind==="video" && playlistVideos().some(item=>keyOf(item)===keyOf(next))) playPlaylistRecord(next);
                else show({...next,kind:itemKind,key:keyOf(next),url:mediaUrl(next.origin||"media",next.file)}, {auto:true});
                markActiveCard(next);
            } else {
                previewPinned = false; active = null; generationKey = "";
                showLastForTab(kind);
            }
        }
        node.graph?.change?.();
        node.setDirtyCanvas?.(true,true);
    }
    function hideStripRecord(kind,record) { clearRecordPreview(record,kind); }
    // 合并多选：只允许视频，且必须同一轮；Ctrl 点击的先后顺序就是合并顺序。
    const mergeOrderKey = record => [recordRoundNo(record), recordSegmentNo(record),
        record?.redraw ? (Number(record.redraw_count) || 1) : 0, Number(record?.serial) || 0];
    function paintMergeSelection() {
        const order = new Map();
        let index = 0;
        for (const key of mergeSelection.keys()) order.set(key, ++index);
        for (const button of cards.values()) {
            const selected = mergeSelection.has(button._mergeKey);
            button.classList.toggle("merge-selected", selected);
            const badge = button.querySelector(".ad-h3-merge-order");
            if (!badge) continue;
            const number = order.get(button._mergeKey);
            badge.textContent = number ? String(number) : "";
            badge.style.display = number ? "block" : "none";
        }
        for (const kind of ["image", "audio", "video"]) {
            for (const button of mediaCards[kind].values()) {
                button.classList.toggle("merge-selected", mergeSelection.has(`${button.dataset.origin || "media"}/${button.dataset.file}`));
            }
        }
    }
    function toggleMergeSelection(record, kind, role = "") {
        // 普通单选后Ctrl追加时，把原单选纳入同一选择集合；取消最后一项则为空。
        if (!mergeSelection.size && processingRecord) {
            const single = processingRecord;
            mergeSelection.set(keyOf(single), {...single, kind: inferMediaKind(single.file,single.kind), merge_source: single.role === "B" ? "B" : "A"});
            processingRecord = null;
        }
        const key = keyOf(record);
        if (mergeSelection.has(key)) {
            mergeSelection.delete(key);
        } else {
            const round = recordRoundNo(record);
            const other = [...mergeSelection.values()].find(item => recordRoundNo(item) !== round);
            if (other) {
                node._adH3Hint?.("不同轮次的片段不能一起合并，请只选同一轮的结果", true);
                return;
            }
            mergeSelection.set(key, {...record, kind, label: recordLabel(record, 0), merge_source: role || (record.role === "B" ? "B" : "A")});
        }
        paintMergeSelection();
        node._adH3Hint?.(mergeSelection.size
            ? `已按点击顺序选中 ${mergeSelection.size} 个片段（${[...mergeSelection.values()].map(item => recordLabel(item, 0)).join(" → ")}），点「合并片段」按此顺序合并`
            : "已取消合并选中", false);
    }
    function numberMergedHistory(items) {
        let serial = 0;
        return items.map(item => {
            if (item.origin !== "media" || !/^选中合并(?:\s*#?\d+)?$/.test(item.label || "选中合并")) return item;
            const match = /#(\d+)$/.exec(item.label || "");
            serial = Math.max(serial+1, Number(item.merge_serial)||Number(match?.[1])||0);
            return {...item,merge_serial:serial,label:`选中合并 #${String(serial).padStart(2,"0")}`};
        });
    }
    function addMergedPreview(source, sourceTag = "") {
        if (!source?.file) return;
        node.properties ||= {};
        const list = numberMergedHistory(Array.isArray(node.properties.ad_h3_merged_previews) ? node.properties.ad_h3_merged_previews : []);
        const serial = Math.max(Number(node.properties.ad_h3_merge_serial)||0, ...list.map(item=>Number(item.merge_serial)||0))+1;
        const roundNo = activeRoundNo();
        const label = `${roundNo > 0 ? `R${roundNo} ` : ""}${sourceTag || ""}合并 #${String(serial).padStart(2,"0")}`;
        node.properties.ad_h3_merge_serial = serial;
        list.push({session:currentSession||"media",file:source.file,origin:"media",label,merge_serial:serial,round_no:activeRoundNo(),round_id:activeRoundId()});
        node.properties.ad_h3_merged_previews = list;
        const records = list.map((item, index) => item.origin === "media"
            ? {file: item.file, origin: "media", label: item.label || `合并片段 ${index+1}`, kind: inferMediaKind(item.file)}
            : {file: `${item.session}/${item.file}`, origin: "session", label: item.label || `合并片段 ${index+1}`, kind: inferMediaKind(item.file)});
        stripStore.merged = records;
        persistStripPreviews();
        renderRecords(merges, records, "merged");
        previewPinned = true;
        show({...source, kind: "video", label, url: mediaUrl(source.origin || "media", source.file)}, {auto: true});
    }
    async function mergeSelectedVideos(chosenSource) {
        // 按 Ctrl 点击的先后顺序合并（Map 的插入顺序），不再按编号重排。
        let selected = [...mergeSelection.values()];
        if (selected.some(record => inferMediaKind(record.file,record.kind) !== "video")) {
            node._adH3Hint?.("原预览的合并片段仅支持视频；图片或音频可送入最终保存逐个加工。",true);
            return;
        }
        let sourceTag = "";
        if (selected.length) {
            if (selected.length < 2) {
                node._adH3Hint?.("Ctrl 已选 1 个片段；再选一个，或取消选中后点「合并片段」合并当前轮全部段最新结果。", true);
                return;
            }
            const sources = new Set(selected.map(item => item.merge_source === "B" ? "B" : "A"));
            if (sources.size > 1) {
                node._adH3Hint?.("A、B 请分别合并：Ctrl 选中的片段只能来自同一边。", true);
                return;
            }
            const rounds = new Set(selected.map(item => recordRoundNo(item)));
            if (rounds.size > 1) {
                node._adH3Hint?.("不同轮次的片段不能一起合并，请只选同一轮的结果", true);
                return;
            }
            sourceTag = [...sources][0];
            if (chosenSource && chosenSource !== sourceTag) {
                node._adH3Hint?.(`已选中的是 ${sourceTag} 端片段，请点「${sourceTag}合并」。`, true);
                return;
            }
        } else {
            // 没点选片段：按点击的 A合并/B合并，合并该端当前轮全部段的最新结果（按段号顺序）。
            sourceTag = chosenSource === "B" ? "B" : "A";
            const currentRound = activeRoundNo();
            const latest = new Map();
            for (const record of mergeIndex.values()) {
                if ((record.role === "B" ? "B" : "A") !== sourceTag) continue;
                if (currentRound > 0 && recordRoundNo(record) !== currentRound) continue;
                const number = Number(record.segment_index) || 0;
                if (number > 0) latest.set(number, record);
            }
            const total = Math.max(Number(latestSessionState?.segments?.length) || 0, ...latest.keys(), 0);
            const missing = [];
            selected = [];
            for (let number = 1; number <= total; number++) {
                const record = latest.get(number);
                if (!record) missing.push(number);
                else selected.push(record);
            }
            if (missing.length) {
                node._adH3Hint?.(`${sourceTag} 端第 ${missing.slice(0, 6).join("、")} 段还没有生成结果，无法整体合并。`, true);
                return;
            }
            if (selected.length < 2) {
                node._adH3Hint?.(`${sourceTag} 端还没有足够的视频片段可以合并。`, true);
                return;
            }
        }
        const orderText = mergeSelection.size
            ? `按点击顺序（${selected.map(item => recordLabel(item, 0)).join(" → ")}）`
            : "按段号顺序";
        node._adH3Hint?.(`正在${orderText}合并 ${sourceTag} 端 ${selected.length} 个视频…`, false);
        try {
            const response = await api.fetchApi("/aiden_h3/merge_clips", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({kind: "video", clips: selected.map(item => ({origin: item.origin || "media", file: item.file}))}),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "合并失败");
            addMergedPreview(data.source || {}, sourceTag);
            mergeSelection.clear();
            paintMergeSelection();
            node._adH3Hint?.(`已按序号合并 ${sourceTag} 端 ${selected.length} 个视频。`, false);
        } catch (error) {
            node._adH3Hint?.(error.message || "合并失败", true);
        }
    }
    for (const sourceButton of root.querySelectorAll("[data-merge-menu] [data-merge-source]")) {
        sourceButton.addEventListener("click", event => {
            event.stopPropagation();
            const menu = root.querySelector("[data-merge-menu]");
            if (menu) menu.hidden = true;
            void mergeSelectedVideos(sourceButton.dataset.mergeSource);
        });
    }
    const resultMetadata=new Map();
    function hiddenResult(record) {
        return resultAliases(record).some(alias=>["segment","segmentB","merged"].some(kind=>hiddenStrips.has(`${kind}:${alias}`)));
    }
    function canonicalResult(raw,kind) {
        let record={...raw,kind:raw.kind||kind};
        for(const alias of resultAliases(record)) {
            const meta=resultMetadata.get(alias);if(meta&&(meta.role||"A")===(record.role||"A"))record={...meta,...record,result_aliases:[...new Set([...resultAliases(meta),...resultAliases(record)])]};
        }
        const matches=mediaRecords[kind].filter(item=>sameResult(item,record));
        if(!matches.length)return record;
        let merged=matches.slice(1).reduce((value,item)=>mergeResult(value,item),matches[0]);
        merged=mergeResult(merged,record);
        merged.label=recordLabel(merged,0);
        const first=mediaRecords[kind].indexOf(matches[0]);
        mediaRecords[kind]=mediaRecords[kind].filter(item=>!matches.includes(item));mediaRecords[kind].splice(first,0,merged);
        const update=value=>sameResult(value,merged)?{...value,...merged}:value;
        processingRecord=update(processingRecord);redoTarget=update(redoTarget);resumeTarget=update(resumeTarget);active=update(active);
        for(const key of Object.keys(lastShown))lastShown[key]=update(lastShown[key]);
        const selection=[...mergeSelection.values()].map(update);mergeSelection.clear();for(const item of selection)mergeSelection.set(keyOf(item),item);
        for(const [key,item] of mergeIndex)if(sameResult(item,merged))mergeIndex.set(key,{...item,...merged});
        return merged;
    }
    let versionSignature = "";
    // FNV-1a 32 位短哈希：只用于比较版本是否变化，不参与数据存取。
    const shortHash = value => {
        const text = String(value ?? "");
        let hash = 0x811c9dc5;
        for (let index = 0; index < text.length; index++) {
            hash ^= text.charCodeAt(index);
            hash = Math.imul(hash, 0x01000193) >>> 0;
        }
        return hash.toString(36);
    };
    function reconcileSessionRecords(state) {
        if (!state?.id || !Array.isArray(state?.segments)) return false;
        // 版本签名：包含选用版本，以及每个版本 id/file/updated_at/reset_id 的短哈希；
        // “删一增一”等数量相同的替换也能触发列表刷新，同时避免对整段状态做大对象序列化。
        const versionSignatureOf = row => `${row?.selected || ""}:${(row?.versions || []).map(version =>
            shortHash(`${version?.id || ""}|${version?.file || ""}|${version?.updated_at ?? ""}|${version?.reset_id || ""}`)).join(".")}`;
        const signature = `${state.id}|${state.round_no || 0}|${state.reset_id||"legacy"}|${state.epoch||0}|` + state.segments.map(versionSignatureOf).join(",");
        if (signature === versionSignature) return false;
        versionSignature = signature;
        const changed = new Set();
        for (const [rowIndex, row] of state.segments.entries()) {
            for (const version of row.versions || []) {
                if (!version?.file) continue;
                const kind = version.media_kind || inferMediaKind(version.file, "video");
                const segmentIndex = Number(version.segment_index) || rowIndex + 1;
                const legacyPreview=String(version.trial_file||version.file).match(/segment_\d+_(preview_[0-9a-f]+\.(?:png|wav))$/);
                let record={origin:"session",file:`${state.id}/${version.file}`,kind,session_id:state.id,
                    segment_key:row.segment_key||"",segment_index:segmentIndex,round_no:Number(version.round_no)||Number(state.round_no)||0,
                    version_id:version.id,trial_request:version.trial_request,trial_session:version.trial_session,trial_file:version.trial_file,
                    preview_origin:version.preview_origin||(legacyPreview?"media":undefined),preview_file:version.preview_file||legacyPreview?.[1],
                    reset_id:version.reset_id||"legacy",official_redraw_count:Number(version.redraw_count)||0,
                    redraw:!!version.preview_only,redraw_base:version.preview_only?segmentIndex:0,redraw_count:Number(version.redraw_count)||0,round_id:"",serial:0};
                for(const alias of resultAliases(record))resultMetadata.set(alias,record);
                record=canonicalResult(record,kind);
                if(hiddenResult(record)){
                    mediaRecords[kind]=mediaRecords[kind].filter(item=>!sameResult(item,record));
                    for(const [key,item] of mergeIndex)if(sameResult(item,record))mergeIndex.delete(key);
                    for(const alias of resultAliases(record))for(const strip of ["segment","segmentB","merged"])hiddenStrips.add(`${strip}:${alias}`);
                    persistHiddenStrips();changed.add(kind);continue;
                }
                if(record.redraw&&!record.redraw_count)record.redraw_count=nextRedrawCount(record,redrawBase(record),mediaRecords[kind],
                    record.official_redraw_count,Number(rememberRedrawCounts()[redrawKey(kind,record,redrawBase(record))])||0);
                if(!record.serial&&!record.redraw)record.serial=Math.max(0,...mediaRecords[kind].map(item=>Number(item.serial)||0))+1;
                record.label=recordLabel(record,0);
                const index=mediaRecords[kind].findIndex(item=>sameResult(item,record));
                if(index<0)mediaRecords[kind].push(record);else mediaRecords[kind][index]=record;
                changed.add(kind);
            }
        }
        if (!changed.size) return false;
        normalizeRedrawNumbers();
        enforceHistoryRetention();
        for (const kind of changed) renderMedia(kind);
        syncStripFromMedia();persistMedia();persistMergeIndex();
        return true;
    }
    function updateState(state) {
        if (disposed || !state?.id) return;
        const previousRound = activeRoundNo();
        latestSessionState = {...state};
        if (currentSession && currentSession !== state.id) {
            video.pause(); video.removeAttribute("src"); video.load(); active = null; generationKey = "";
            segments.replaceChildren(); segmentsB.replaceChildren(); merges.replaceChildren(); cards.clear();
            title.textContent = "生成完成后在这里预览视频";
            setIdle(true);
            renderRecords(segments, stripStore.segment.filter(record => record.role !== "B"), "segment");
            renderRecords(segmentsB, stripStore.segment.filter(record => record.role === "B"), "segmentB");
            renderRecords(merges, stripStore.merged, "merged");
        }
        currentSession = state.id;
        node.properties ||= {};
        rememberRound(state);
        if (previousRound !== activeRoundNo()) {
            expandedHistoryRounds.clear();historyPages.clear();
            for (const kind of ["image","audio","video"]) mediaPages[kind]=0;
            enforceHistoryRetention();persistMedia();persistMergeIndex();
            syncStripFromMedia();
            for (const kind of ["image","audio","video"]) renderMedia(kind);
        }
        reconcileSessionRecords(state);
        let merged = numberMergedHistory(Array.isArray(node.properties.ad_h3_merged_previews) ? node.properties.ad_h3_merged_previews : []);
        node.properties.ad_h3_merged_previews = merged;
        if (state.merged && !merged.some(item => item.session === state.id && item.file === state.merged)) {
            merged = [...merged, {session: state.id, file: state.merged,round_no:activeRoundNo(),round_id:activeRoundId()}];
            node.properties.ad_h3_merged_previews = merged;
        }
        const liveMergedRecords = merged.map((item,index) => item.origin === "media"
            ? {...item,file:item.file,origin:"media",label:item.label||`合并片段 ${index+1}`,kind:inferMediaKind(item.file)}
            : {...item,file:`${item.session}/${item.file}`,origin:"session",label:item.label||`合并片段 ${index+1}`,kind:inferMediaKind(item.file)});
        if (liveMergedRecords.length) {
            stripStore.merged = liveMergedRecords;
            persistStripPreviews();
        }
        // 「分段生成」缩略图由生成历史（mediaRecords）维护，轮询/审批/抽卡都不会替换或清空已有缩略图。
        renderRecords(merges, stripStore.merged, "merged");
        const records = stripStore.segment;
        // 仅新结果到达时自动切换；轮询、试听、手动选片、删除缩略图均不会改变当前预览位置。
        const selected = state.segments[state.cursor]?.selected;
        const nextKey = `${state.id}/${state.cursor}/${selected || ""}`;
        if (nextKey !== generationKey) {
            generationKey = nextKey;
            const firstHydration = !hydratedSession;
            hydratedSession = true;
            const row = state.segments[state.cursor];
            const version = row?.versions.find(item => item.id === selected);
            const record = version ? {file:`${state.id}/${version.file}`,label:`第 ${state.cursor+1} 段`,
                kind: inferMediaKind(version.file, version.media_kind)} : records.at(-1);
            // 用户手动选中/删除预览后，新结果只更新缩略图分类，不抢当前画面。
            // 首次打开工作流：不自动显示上次旧结果，保持「留白，亦是序章 / 下一帧，属于你的想象」初始态。
            if (!firstHydration && record && !hiddenStrips.has(stripKey("segment", {...record,origin:"session"})) && (!active || selected) && !previewPinned)
                show({...record, url:mediaUrl("session",record.file)}, {auto: true});
        }
    }
    function markActiveCard(record) {
        for (const kind of ["image", "audio", "video"]) {
            const list = mediaLists[kind];
            if (!list) continue;
            const targetFile = record?.file || "";
            for (const card of list.children) {
                const hit = targetFile && card.dataset.file === targetFile && (card.dataset.origin || "media") === (record?.origin || "media");
                card.classList.toggle("selected", !!hit);
            }
        }
    }
    function markRedrawRecord(item, kind, record, previous) {
        const pending = redrawPending || (record.preview_only && Number(record.segment_index) > 0
            ? {index:Number(record.segment_index),base:Number(record.segment_index),exact:true} : null);
        if (!(pending && (pending.index === Number(record.segment_index)
            || !pending.exact || !Number(record.segment_index)))) return false;
        const base = Number(pending.base) || Number(record.segment_index) || pending.index;
        const counts = rememberRedrawCounts();
        item.reset_id=record.reset_id||item.reset_id||latestSessionState?.reset_id||"legacy";
        const key = redrawKey(kind,item,base);
        const repeated = previous?.redraw && recordRoundNo(previous) === recordRoundNo(item)
            && Number(previous.redraw_base) === base && Number(previous.redraw_count) > 0;
        item.redraw = true;
        item.redraw_base = base;
        // 同一段已有抽卡编号时继续往后排（后台次数只作参考，避免清空重置后又从 -1 重来）。
        item.redraw_count = repeated ? Number(previous.redraw_count)
            : nextRedrawCount(item, base, mediaRecords[kind].filter(entry => entry !== item),
                record.official_redraw_count, counts[key]);
        counts[key] = Math.max(Number(counts[key]) || 0, item.redraw_count);
        item.serial = 0;  // 抽卡结果不占用正常生成的整数序号
        item.label = recordLabel(item, 0);
        redrawReview = Number(record.segment_index) || pending.index;
        redrawPending = null;
        node._adH3Hint?.(`第 ${base} 段已重抽完成（${item.label}，已作为该段当前选用）；点「开始」继续下一段`, false);
        return true;
    }
    function receive(record) {
        if(record?.task_id&&node.properties?.ad_task_id&&record.task_id!==node.properties.ad_task_id)return;
        // 后端已按保存路径写盘；这里只把结果显示出来并提示保存结果/错误。
        try {
            if (record?.auto_save_error) node._adH3ReviewStatus?.(`保存失败：${record.auto_save_error}`, true);
            else if (record?.auto_saved) node._adH3ReviewStatus?.(`已保存：${record.auto_saved}`);
        } catch (_error) {}
        if (!record?.file) return;
        if(record.version_id&&record.preview_only&&Number(record.redraw_count)>0)record={...record,official_redraw_count:Number(record.redraw_count)};
        record=canonicalResult(record,record.kind||"video");
        if(hiddenResult(record))return;
        // 轮次标记：新结果属于「当前执行轮」，编号 R{轮}-{段} 与折叠分组都靠它。
        rememberRound(latestSessionState);
        record.round_id = record.round_id || activeRoundId();
        record.round_no = record.round_no != null ? Number(record.round_no) : activeRoundNo();
        node._adH3StandaloneReady = true;
        const kind = record.kind || "video";
        const label = kind === "image" ? "本次图像" : kind === "audio" ? "本次音频" : "本次生成片段";
        if (kind === "image" || kind === "audio") {
            const nextSerial = () => Math.max(0, ...mediaRecords[kind].map(entry => Number(entry.serial) || 0)) + 1;
            const existing = mediaRecords[kind].find(entry => sameResult(entry,record));
            const item = {...record, label, serial: existing?.serial || nextSerial()};
            const index = mediaRecords[kind].findIndex(entry => sameResult(entry,record));
            if (index >= 0) mediaRecords[kind][index] = item; else mediaRecords[kind].push(item);
            // 本段抽卡结果：编号为「原编号-第几次」（黄色），并记录待继续的段号。
            if (markRedrawRecord(item, kind, record, existing)) placeRedrawRecord(kind, item);
            normalizeRedrawNumbers();
            node._adH3RedoPending = false;
            trimMediaRecords(kind);
            persistMedia();
            renderMedia(kind);
            syncStripFromMedia();
            // 用户正在手动查看/删除缩略图时不抢主预览，新结果只进列表。
            if (previewPinned) return;
            setTab(kind);
            show({...item, kind, url:mediaUrl(record.origin, record.file)}, {auto: true});
            markActiveCard(item);
            return;
        }
        const videoSerial = Math.max(0, ...mediaRecords.video.map(entry => Number(entry.serial) || 0)) + 1;
        const existingVideo = mediaRecords.video.find(entry => sameResult(entry,record));
        const item = {...record, label, kind: "video", serial: existingVideo?.serial || videoSerial};
        const index = mediaRecords.video.findIndex(entry => sameResult(entry,record));
        if (index >= 0) mediaRecords.video[index] = item; else mediaRecords.video.push(item);
        if (markRedrawRecord(item, "video", record, existingVideo)) placeRedrawRecord("video", item);
        normalizeRedrawNumbers();
        node._adH3RedoPending = false;
        rememberMergeRecord(item);persistMergeIndex();
        trimMediaRecords("video");
        persistMedia(); renderMedia("video"); syncStripFromMedia();
        if (previewPinned) return;
        markActiveCard(item);
        setTab("video");
        show({...item, kind:"video", url:mediaUrl(record.origin, record.file)}, {auto: true});
    }
    function restoreLegacy(raw) {
        if (active) return;
        try {
            const data = typeof raw === "string" ? JSON.parse(raw) : raw;
            const history = data?.preview?.history || [];
            const records = history.filter(item => item.filename).map((item,index) => ({origin:item.type || "temp",file:[item.subfolder,item.filename].filter(Boolean).join("/"),label:`历史片段 ${index+1}`}));
            if (!records.length) { syncStripFromMedia(); return; }
            renderRecords(segments, records, "segment");
            const record = records[data?.preview?.activeIdx] || records.at(-1);
            if (record) receive(record);
        } catch { /* 无效的旧界面状态不影响生成。 */ }
    }
    function restoreSavedMedia() {
        resultMetadata.clear();versionSignature="";
        expandedHistoryRounds.clear();
        for (const round of node.properties?.ad_h3_expanded_rounds || []) expandedHistoryRounds.add(Number(round)||0);
        showHistoryRounds = !!node.properties?.ad_h3_show_history;
        mergeIndex.clear();
        for (const record of node.properties?.ad_h3_merge_index || []) rememberMergeRecord(record);
        hiddenStrips.clear();
        for (const key of node.properties?.ad_h3_hidden_strips || []) hiddenStrips.add(String(key));
        const strips = node.properties?.ad_h3_strip_previews;
        for (const kind of ["segment","merged"]) {
            if (Array.isArray(strips?.[kind])) stripStore[kind] = strips[kind].filter(item=>item?.file);
        }
        stripStore.merged = numberMergedHistory(stripStore.merged);
        if (Array.isArray(node.properties?.ad_h3_merged_previews))
            node.properties.ad_h3_merged_previews = numberMergedHistory(node.properties.ad_h3_merged_previews);
        persistStripPreviews();
        renderRecords(merges, stripStore.merged, "merged");
        const saved = node.properties?.ad_h3_media_records;
        if (!saved || typeof saved !== "object") {
            restoreHistoryRecords();normalizeRedrawNumbers();syncStripFromMedia();
            renderRecords(segments, stripStore.segment.filter(record => record.role !== "B"), "segment");
            renderRecords(segmentsB, stripStore.segment.filter(record => record.role === "B"), "segmentB");
            return;
        }
        for (const kind of ["image", "audio", "video"]) {
            mediaRecords[kind] = (Array.isArray(saved[kind]) ? saved[kind] : [])
                .filter(item => item && item.file)
                .map((item, index) => ({...item, serial: item.redraw ? 0 : (Number(item.serial) > 0 ? Number(item.serial) : index + 1)}));
            renderMedia(kind);
        }
        restoreHistoryRecords();rememberRedrawCounts();normalizeRedrawNumbers();
        for (const kind of ["image","audio","video"]) renderMedia(kind);
        // 重新打开工作流时保持原先的分类与空闲画面，不自动弹出别的预览。
        syncStripFromMedia();
        setTab(node.properties?.ad_h3_active_tab || "image", {sync: false});
    }
    for (const record of node.properties?.ad_h3_merge_index || []) rememberMergeRecord(record);
    // 恢复现存完整索引中的历史，完整保留最近10组。
    restoreHistoryRecords();
    normalizeRedrawNumbers();
    syncStripFromMedia();
    renderRecords(merges, stripStore.merged, "merged");
    renderMedia("image");
    renderMedia("audio");
    setTab(node.properties?.ad_h3_active_tab || (mediaRecords.image.length ? "image" : mediaRecords.audio.length ? "audio" : "image"), {sync: false});
    const onFullscreenChange = () => {
        if (disposed) return;
        const screen = root.querySelector(".ad-h3-screen"), current = document.fullscreenElement;
        const owned = !!current && (current === screen || screen.contains(current));
        for (const button of root.querySelectorAll("[data-fullscreen],[data-ab-fullscreen]")) {
            button.textContent = owned ? "退出全屏" : "全屏";
            button.setAttribute("aria-label", owned ? "退出全屏" : "全屏预览");
            button.setAttribute("aria-pressed", String(owned));
        }
        requestAnimationFrame(() => { if (!disposed) applyAB(); });
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    onFullscreenChange();
    return {show, updateState, receive, restoreLegacy, restoreSavedMedia, getTab: () => activeTab,
        processingSelection: () => (mergeSelection.size ? [...mergeSelection.values()] : processingRecord ? [processingRecord] : [])
            .map(record => {const {url,thumb_b64,thumb_mime,...clean}=record;return {...clean};}),
        selectedSegment: () => (redoTarget && Number(redoTarget.segment_index) > 0 ? Number(redoTarget.segment_index) : 0),
        resumeTarget: () => resumeTarget ? {...resumeTarget} : null,
        groupRecords: target => [...Object.values(mediaRecords).flat(),...stripStore.segment].filter(record=>
            recordRoundNo(record)===recordRoundNo(target) && (record.role==="B"?"B":"A")===(target?.role==="B"?"B":"A")),
        consumeResumeTarget(){resumeTarget = null;},
        resetGroupSelection(){resumeTarget=null;redoTarget=null;redrawPending=null;redrawReview=null;batchResume=false;previewPinned=false;redrawRequest++;},
        clearPin(){ previewPinned = false; }, get revision(){return revision;},
        dispose(){
            if (disposed) return;
            disposed=true;
            abSyncToken += 1;
            stopABSyncLoop();
            closeABMenus();
            document.removeEventListener("pointerdown",onABDocPointerDown,true);
            document.removeEventListener("fullscreenchange",onFullscreenChange);
            window.removeEventListener("keydown",onPreviewEscape,true);
            setPlaylistEnabled(false); setIdle(true);
            if (videoWatchdog) {clearTimeout(videoWatchdog);videoWatchdog=0;}
            for (const close of [...lightboxClosers]) close();
            for (const media of [video,audioView,abVideoA,abVideoB,abAudioA,abAudioB]) releaseMedia(media);
            for (const image of root.querySelectorAll("img")) image.removeAttribute("src");
            mediaInfoCache.clear();cards.clear();
            for (const owned of Object.values(mediaCards)) owned.clear();
        }};
}

export function mountNativePlayer(node, api) {
    if (node._adH3Preview) return;
    const root = document.createElement("div");
    const url = (route, origin, file) => api.apiURL(`/aiden_h3/${route}/${origin}/${file.split("/").map(encodeURIComponent).join("/")}`);
    node._adH3Preview = createNativePlayer(node,root,(origin,file)=>url("media",origin,file),(origin,file)=>url("thumbnail",origin,file),api);
    const legacy = node.widgets?.find(widget=>widget.name==="video_oasis_ui");
    if (legacy) {legacy.type="hidden"; legacy.computeSize=()=>[0,-4];}
    const height = () => Math.max(300,root.querySelector(".ad-h3-preview-layout").scrollHeight+24)+20;
    const widget = node.addDOMWidget("ad_h3_native_player","div",root,{serialize:false,hideOnZoom:false,getHeight:height});
    widget.label = "视频预览"; widget.localized_name = widget.label;
    // 外层容器默认会吃掉节点下方空白处的鼠标事件：整块穿透，只让可交互子元素接收
    try {
        const wrapper = widget.element?.parentElement || root.parentElement;
        if (wrapper) { wrapper.style.pointerEvents = "none"; wrapper.style.overflow = "hidden"; }
    } catch {}
    bindWidgetWidth(node,widget);
    fitPreviewContent(node,root);
    widget.serialize = false; widget.computeSize = width=>[width,height()];
    // 初始最小尺寸只在新节点（未从工作流恢复尺寸）时生效；旧工作流保留用户保存的宽高。
    setTimeout(() => {
        if (!node.graph || node._adH3Configured) return;
        const width = Math.max(Number(node.size?.[0]) || 0, MIN_NODE_WIDTH);
        const height = Math.max(Number(node.size?.[1]) || 0, 460);
        if (Math.abs(width - Number(node.size?.[0])) > 0.5 || Math.abs(height - Number(node.size?.[1])) > 0.5) node.setSize([width, height]);
        node.setDirtyCanvas?.(true, true);
    }, 0);
    // 列表按实际内容收高，不把节点额外高度反复加回列表。
    for (const list of root.querySelectorAll(".ad-h3-media-list")) {
        list.style.height="auto";list.style.minHeight="0";list.style.maxHeight="none";
    }
    const executed = node.onExecuted;
    node.onExecuted = function(message){const result=executed?.apply(this,arguments);for(const record of message?.h3_preview || [])this._adH3Preview?.receive(record);return result;};
    const configured = node.onConfigure;
    node.onConfigure = function(){const result=configured?.apply(this,arguments);this._adH3Preview?.restoreSavedMedia();this._adH3Preview?.restoreLegacy(legacy?.value);return result;};
    const removed = node.onRemoved;
    node.onRemoved = function(){this._adH3Preview?.dispose();delete this._adH3Preview;removed?.apply(this,arguments);};
}

