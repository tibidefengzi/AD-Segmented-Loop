import { bindWidgetWidth, fitPreviewContent, MIN_NODE_WIDTH } from "./dom_layout.js?v=20261026-glass-v031";
// 独立播放器。播放选择只存在于此处，绝不修改生成游标或分段选用版本。
export function createNativePlayer(node, root, mediaUrl, thumbnailUrl) {
    root.classList.add("aiden-h3", "ad-h3-video");
    root.innerHTML = `<div class="ad-h3-preview-layout"><section class="ad-h3-screen"><div class="ad-h3-preview-title">生成完成后在这里预览素材</div><video playsinline preload="metadata" aria-label="时间线预览"></video><img data-media-image hidden alt="图像预览"><audio data-media-audio controls hidden></audio><div class="ad-h3-idle-hint"><span>留白，亦是序章</span><span>下一帧，属于你的想象</span></div></section><div class="ad-h3-tabs"><button type="button" data-tab="image">图片</button><button type="button" data-tab="audio">音频</button><button type="button" data-tab="video" class="active">视频</button><span class="ad-h3-tab-hint" data-tab-hint hidden></span></div><div class="ad-h3-loop-actions"><button type="button" data-loop="start">开始</button><button type="button" data-loop="pause">停止</button><button type="button" data-loop="auto">分段循环</button><button type="button" data-loop="redo" title="选中下方任意预览后只重抽该段；运行中请先点「停止」">本段抽卡</button><button type="button" data-loop="approve">同意批准</button><button type="button" data-clear-preview title="只清空本轮生成进度与选用记录（预览内容与文件保留）">清空重置</button><button type="button" data-clear-media-preview title="只清空当前分类的预览内容（不删除文件、不影响进度）">清空预览</button><div class="ad-h3-loop-hint" data-loop-hint hidden></div><span class="ad-h3-video-actions"><button type="button" data-loop="merge" title="按住 Ctrl 在「分段生成」里点选视频后，按序号合并">合并已通过片段</button></span><button type="button" class="ad-h3-playlist-toggle" data-loop-playlist aria-pressed="false" title="按分段编号循环播放预览视频">循环播放</button></div><div class="ad-h3-panel" data-panel="video"><div class="ad-h3-media-list" data-video-list></div><div class="ad-h3-player-controls"><button data-play aria-label="播放或暂停">播放</button><button data-frame="-1" title="后退一帧" aria-label="后退一帧">上一帧</button><button data-frame="1" title="前进一帧" aria-label="前进一帧">下一帧</button><input data-seek type="range" min="0" max="0" step="1" value="0" aria-label="播放位置"><span data-time>0.00 / 0.00 秒</span><button data-mute aria-label="切换静音">取消静音</button><select data-rate aria-label="播放速度"><option value="0.5">0.5 倍速</option><option value="1" selected>1 倍速</option><option value="1.5">1.5 倍速</option><option value="2">2 倍速</option></select><button data-fullscreen aria-label="全屏预览">全屏</button></div><aside class="ad-h3-strips"><div class="ad-h3-strip-row"><span>分段生成</span><div class="ad-h3-segments"></div></div><div class="ad-h3-strip-row"><span>合并生成</span><div class="ad-h3-merges"></div></div></aside></div><div class="ad-h3-panel" data-panel="image" hidden><div class="ad-h3-media-list" data-image-list></div></div><div class="ad-h3-panel" data-panel="audio" hidden><div class="ad-h3-media-list" data-audio-list></div></div></div>`;
    const video = root.querySelector("video"), title = root.querySelector(".ad-h3-preview-title");
    const imageView = root.querySelector("[data-media-image]"), audioView = root.querySelector("[data-media-audio]");
    const layout = root.querySelector(".ad-h3-preview-layout");
    const transport = root.querySelector(".ad-h3-player-controls");
    root.querySelector(".ad-h3-screen").append(transport);
    audioView.controls = false;
    audioView.muted = true;
    const segments = root.querySelector(".ad-h3-segments"), merges = root.querySelector(".ad-h3-merges");
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
            const response = await fetch("/aiden_h3/pick_folder", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({initial_dir: saveState.dir, kind: active?.kind || ""})});
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
            const response = await fetch("/aiden_h3/pick_save_path", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({default_name: defaultName, initial_dir: saveState.dir, kind: info?.kind || active?.kind || "", title: "选择保存位置"}),
            });
            if (response.ok) {
                const picked = await response.json();
                const target = String(picked.path || "").trim();
                if (!target) return {cancelled: true};
                const saved = await fetch("/aiden_h3/save_media", {
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
        const response = await fetch("/aiden_h3/save_media", {
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
    const abOverlay = document.createElement("div");
    abOverlay.className = "ad-h3-ab-overlay"; abOverlay.hidden = true;
    abOverlay.innerHTML = '<div class="ad-h3-ab-layer"><img data-ab-a alt="A"></div>'
        + '<div class="ad-h3-ab-layer ad-h3-ab-top"><img data-ab-b alt="B"></div>'
        + '<div class="ad-h3-ab-divider"></div>'
        + '<span class="ad-h3-ab-tag" style="left:8px">A</span><span class="ad-h3-ab-tag" style="right:8px">B</span>'
        + '<div class="ad-h3-ab-videos" hidden><div class="ad-h3-ab-vcell"><video data-ab-va muted playsinline></video></div>'
        + '<div class="ad-h3-ab-vcell"><video data-ab-vb muted playsinline></video></div></div>'
        + '<div class="ad-h3-ab-audios" hidden><div class="ad-h3-ab-acell"><audio data-ab-aa controls preload="metadata"></audio></div>'
        + '<div class="ad-h3-ab-acell"><audio data-ab-ab controls preload="metadata"></audio></div></div>'
        + '<button type="button" class="ad-h3-ab-mode" data-ab-mode>叠加滑杆</button>'
        + '<button type="button" class="ad-h3-ab-fullscreen" data-ab-fullscreen>全屏</button>'
        + '<button type="button" class="ad-h3-ab-exit" data-ab-exit>退出对比</button>'
        + '<div class="ad-h3-ab-controls" hidden><button type="button" data-ab-play>播放</button>'
        + '<button type="button" data-ab-step="-1">上一帧</button><button type="button" data-ab-step="1">下一帧</button>'
        + '<input type="range" data-ab-seek min="0" max="0" step="0.001" value="0">'
        + '<span data-ab-time>0.00 / 0.00 秒</span><button type="button" data-ab-audio="A">A声</button><button type="button" data-ab-audio="B">B声</button></div>';
    (root.querySelector(".ad-h3-screen") || layout).append(abOverlay);
    const abA = abOverlay.querySelector("[data-ab-a]"), abB = abOverlay.querySelector("[data-ab-b]");
    const abTop = abOverlay.querySelector(".ad-h3-ab-top"), abDivider = abOverlay.querySelector(".ad-h3-ab-divider");
    const abSlots = {A: null, B: null};
    let abRatio = 0.5;
    let abPicking = false;
    const abVideos = abOverlay.querySelector(".ad-h3-ab-videos");
    const abControls = abOverlay.querySelector(".ad-h3-ab-controls");
    const abVideoA = abOverlay.querySelector("[data-ab-va]"), abVideoB = abOverlay.querySelector("[data-ab-vb]");
    const abPlay = abOverlay.querySelector("[data-ab-play]"), abSeek = abOverlay.querySelector("[data-ab-seek]");
    const abTime = abOverlay.querySelector("[data-ab-time]");
    const abAudioButtons = [...abOverlay.querySelectorAll("[data-ab-audio]")];
    let abAudioChoice = null;
    const abAudios = abOverlay.querySelector(".ad-h3-ab-audios");
    const abAudioA = abOverlay.querySelector("[data-ab-aa]"), abAudioB = abOverlay.querySelector("[data-ab-ab]");
    const abModeButton = abOverlay.querySelector("[data-ab-mode]");
    let abMode = "split";
    const markABMode = () => { abOverlay.dataset.zoom = abMode === "split" ? "image" : "canvas"; };
    let abFocus = "B";
    const abFps = {A: 24, B: 24};
    const mediaInfoCache = new Map();
    const fetchMediaFps = async (record) => {
        const origin = record?.origin || "media";
        const file = record?.file;
        if (!file) return 24;
        const key = `${origin}/${file}`;
        if (mediaInfoCache.has(key)) return mediaInfoCache.get(key);
        try {
            const response = await fetch(`/aiden_h3/media_info/${origin}/${String(file).split("/").map(encodeURIComponent).join("/")}`);
            const data = await response.json();
            const fps = Number(data?.fps) > 0 ? Number(data.fps) : 24;
            mediaInfoCache.set(key, fps);
            return fps;
        } catch {
            mediaInfoCache.set(key, 24);
            return 24;
        }
    };
    const abStepSeconds = () => 1 / Math.max(abFps.A, abFps.B, 1);
    const abTimeline = () => Math.min(abVideoA.duration || 0, abVideoB.duration || 0) || abVideoA.duration || abVideoB.duration || 0;
    const abSync = (source, target) => {
        if (Math.abs((target.currentTime || 0) - (source.currentTime || 0)) > 0.15) target.currentTime = source.currentTime;
    };
    for (const [videoEl, other] of [[abVideoA, abVideoB], [abVideoB, abVideoA]]) {
        videoEl.addEventListener("play", () => { if (other.paused) other.play().catch(() => {}); });
        videoEl.addEventListener("pause", () => { if (!other.paused) other.pause(); });
        videoEl.addEventListener("timeupdate", () => {
            abSync(videoEl, other);
            if (videoEl === abVideoA) {
                abSeek.value = String(videoEl.currentTime || 0);
                abTime.textContent = `${(videoEl.currentTime || 0).toFixed(2)} / ${abTimeline().toFixed(2)} 秒`;
            }
        });
        videoEl.addEventListener("loadedmetadata", () => {
            const duration = abTimeline();
            abSeek.max = String(duration);
            abTime.textContent = `${(videoEl.currentTime || 0).toFixed(2)} / ${duration.toFixed(2)} 秒`;
        });
    }
    abPlay.addEventListener("click", () => {
        if (abVideoA.paused) { abVideoA.play().catch(() => {}); abVideoB.play().catch(() => {}); abPlay.textContent = "暂停"; }
        else { abVideoA.pause(); abVideoB.pause(); abPlay.textContent = "播放"; }
    });
    abOverlay.querySelectorAll("[data-ab-step]").forEach(button => button.addEventListener("click", () => {
        const step = Number(button.dataset.abStep) * abStepSeconds();
        for (const videoEl of [abVideoA, abVideoB]) {
            videoEl.pause();
            videoEl.currentTime = Math.max(0, Math.min(abTimeline(), (videoEl.currentTime || 0) + step));
        }
        abSeek.value = String(abVideoA.currentTime || 0);
        abTime.textContent = `${(abVideoA.currentTime || 0).toFixed(2)} / ${abTimeline().toFixed(2)} 秒`;
    }));
    const exitAB = () => {
        if (document.fullscreenElement === abOverlay && document.exitFullscreen) { void document.exitFullscreen().catch(() => {}); }
        abSlots.A = null; abSlots.B = null; abPicking = false;
        resetABView();
        abVideoA.pause(); abVideoB.pause();
        abVideoA.removeAttribute("src"); abVideoB.removeAttribute("src");
        abA.removeAttribute("src"); abB.removeAttribute("src");
        paintAB();
        const hint = root.querySelector("[data-tab-hint]");
        if (hint) { hint.hidden = false; hint.textContent = "已退出 A/B 对比"; }
    };
    function toggleABFullscreen() {
        if (document.fullscreenElement) { if (document.exitFullscreen) void document.exitFullscreen().catch(() => {}); return; }
        if (abOverlay.requestFullscreen) void abOverlay.requestFullscreen().catch(() => {});
    }
    abOverlay.querySelector("[data-ab-fullscreen]").addEventListener("click", event => {
        event.stopPropagation();
        toggleABFullscreen();
    });
    abOverlay.addEventListener("click", event => {
        if (event.target.closest(".ad-h3-ab-exit, .ad-h3-ab-controls, .ad-h3-ab-mode")) return;
        if (!abSlots.A || !abSlots.B) return;
        const rect = abOverlay.getBoundingClientRect();
        const ratio = (event.clientX - rect.left) / (rect.width || 1);
        abFocus = ratio < 0.5 ? "A" : "B";
        const tags = abOverlay.querySelectorAll(".ad-h3-ab-tag");
        if (tags[0]) tags[0].classList.toggle("on", abFocus === "A");
        if (tags[1]) tags[1].classList.toggle("on", abFocus === "B");
    });
    abModeButton.addEventListener("click", event => {
        event.stopPropagation();
        abMode = abMode === "split" ? "slider" : "split";
        markABMode();
        abModeButton.textContent = abMode === "split" ? "叠加滑杆" : "独立左右";
        resetABView();
        applyAB();
    });
    abOverlay.querySelector("[data-ab-exit]").addEventListener("click", event => {
        event.stopPropagation(); exitAB();
    });
    window.addEventListener("keydown", event => {
        if (event.key === "Escape" && !abOverlay.hidden) exitAB();
    });
    abSeek.addEventListener("input", () => {
        const value = Number(abSeek.value) || 0;
        for (const videoEl of [abVideoA, abVideoB]) videoEl.currentTime = Math.min(value, videoEl.duration || value);
        abTime.textContent = `${value.toFixed(2)} / ${abTimeline().toFixed(2)} 秒`;
    });
    const applyAudioChoice = () => {
        abVideoA.muted = abAudioChoice !== "A";
        abVideoB.muted = abAudioChoice !== "B";
        for (const button of abAudioButtons) button.classList.toggle("on", button.dataset.abAudio === abAudioChoice);
    };
    for (const button of abAudioButtons) button.addEventListener("click", () => {
        abAudioChoice = abAudioChoice === button.dataset.abAudio ? null : button.dataset.abAudio;
        applyAudioChoice();
    });
    const paintAB = () => {
        const both = abSlots.A && abSlots.B;
        abOverlay.hidden = !both;
        layout.classList.toggle("is-comparing", !!both);
        paintABButton();
        if (!both) {
            abVideoA.pause(); abVideoB.pause();
            return;
        }
        const isVideo = abSlots.A.kind === "video" && abSlots.B.kind === "video";
        const isAudio = abSlots.A.kind === "audio" && abSlots.B.kind === "audio";
        const isImage = abSlots.A.kind === "image" && abSlots.B.kind === "image";
        abVideos.hidden = !isVideo; abControls.hidden = !isVideo;
        abAudios.hidden = !isAudio;
        markABMode();
        abModeButton.hidden = !isImage;
        abModeButton.textContent = abMode === "split" ? "叠加滑杆" : "独立左右";
        abA.parentElement.style.display = isVideo || isAudio ? "none" : "";
        abB.parentElement.style.display = isVideo || isAudio ? "none" : "";
        abDivider.style.display = isImage ? "" : "none";
        const tags = abOverlay.querySelectorAll(".ad-h3-ab-tag");
        const tagLabel = (slot, side) => `${side}-${Number(slot?.serial) > 0 ? String(Number(slot.serial)).padStart(2, "0") : "-"}`;
        if (tags[0]) { tags[0].textContent = tagLabel(abSlots.A, "A"); tags[0].title = String(abSlots.A.file || ""); }
        if (tags[1]) { tags[1].textContent = tagLabel(abSlots.B, "B"); tags[1].title = String(abSlots.B.file || ""); }
        if (isVideo) {
            abVideoA.src = abSlots.A.url; abVideoB.src = abSlots.B.url;
            abVideoA.volume = 1; abVideoB.volume = 1;
            abAudioChoice = null;
            applyAudioChoice();
            abPlay.textContent = "播放";
            Promise.all([fetchMediaFps(abSlots.A), fetchMediaFps(abSlots.B)]).then(([fpsA, fpsB]) => {
                abFps.A = fpsA; abFps.B = fpsB;
            });
        } else if (isAudio) {
            abAudioA.src = abSlots.A.url; abAudioB.src = abSlots.B.url;
        } else {
            abA.src = abSlots.A.url; abB.src = abSlots.B.url;
            resetABView();
            applyAB();
        }
    };
    const applyABView = () => {
        const transform = `translate(${Math.round(abView.x)}px, ${Math.round(abView.y)}px) scale(${abView.zoom.toFixed(3)})`;
        for (const image of [abA, abB]) {
            image.style.transformOrigin = "50% 50%";
            image.style.transform = transform;
        }
    };
    const resetABView = () => { abView.zoom = 1; abView.x = 0; abView.y = 0; applyABView(); };
    const applyAB = () => {
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
            setABHint(kind === "video" ? "A/B 视频对比已开启：同步播放 / 逐帧对照" : "A/B 图片对比已开启：拖动分界线对比");
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
        if (abOverlay.hidden || abMode !== "split") return;
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
        if (event.target.closest(".ad-h3-ab-exit, .ad-h3-ab-controls, .ad-h3-ab-mode")) return;
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
        if (abMode !== "slider") return;
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
    // 分段生成/合并生成缩略图的本地隐藏记录（X 只移除预览，不删文件/进度）；持久化到节点属性。
    const hiddenStrips = new Set(
        (Array.isArray(node.properties?.ad_h3_hidden_strips) ? node.properties.ad_h3_hidden_strips : [])
            .filter(key => typeof key === "string")
    );
    const persistHiddenStrips = () => {
        node.properties ||= {};
        node.properties.ad_h3_hidden_strips = [...hiddenStrips].slice(-300);
    };
    const stripRecords = {segment: [], merged: []};
    // 「合并已通过片段」的新用法：按住 Ctrl 在分段生成里多选视频，按序号合并。
    const mergeSelection = new Map();
    // 分段/合并缩略图持久化：后台会话切换或重开后仍展示之前生成的缩略图。
    const stripStore = {segment: [], merged: []};
    const savedStrip = node.properties?.ad_h3_strip_previews;
    if (savedStrip && typeof savedStrip === "object") {
        for (const kind of ["segment", "merged"]) {
            if (Array.isArray(savedStrip[kind]))
                stripStore[kind] = savedStrip[kind].filter(item => item && item.file).slice(-60);
        }
    }
    const persistStripPreviews = () => {
        node.properties ||= {};
        const clean = list => list.slice(-60).map(({file, origin, label, kind, segment_index}) =>
            ({file, origin: origin || "session", label, kind, segment_index}));
        node.properties.ad_h3_strip_previews = {segment: clean(stripStore.segment), merged: clean(stripStore.merged)};
    };
    // 「分段生成」按生成历史展示：和图片区域一致，所有生成/抽卡结果都保留，不随当前选用版本变动。
    const syncStripFromMedia = () => {
        const history = [];
        for (const kind of ["image", "audio", "video"]) {
            for (const record of mediaRecords[kind]) {
                if (!record?.file) continue;
                history.push({file: record.file, origin: record.origin || "media",
                    label: recordLabel(record, 0), kind: record.kind || kind,
                    segment_index: Number(record.segment_index) || 0,
                    serial: Number(record.serial) || 0,
                    redraw: !!record.redraw,
                    redraw_base: Number(record.redraw_base) || 0,
                    redraw_count: Number(record.redraw_count) || 0});
            }
        }
        history.sort((a, b) => {
            const left = numberOrder(a), right = numberOrder(b);
            return left[0] - right[0] || left[1] - right[1] || left[2] - right[2];
        });
        stripStore.segment = history.slice(-60);
        for (const key of [...mergeSelection.keys()])
            if (!stripStore.segment.some(record => keyOf(record) === key)) mergeSelection.delete(key);
        persistStripPreviews();
        renderRecords(segments, stripStore.segment, "segment");
    };
    const stripKey = (kind, record) => `${kind}:${record?.origin || "session"}/${record?.file || ""}`;
    let redoTarget = null;
    // 本段抽卡（2026-10-28 用户要求）：
    // - 任意时候（含分段循环已完成）选中任意预览卡片即可指定段重抽；
    // - 运行中必须先点「停止」让流程待命，再点「本段抽卡」；
    // - 重抽结果用黄色编号显示；抽完点「开始」继续分段流程。
    let latestSessionState = null;
    let redrawPending = null;
    let redrawReview = null;
    // 点「停止」时如果分段循环正在运行，记住它；抽卡只是预览备选，点「开始」要续接原来的分段循环位置。
    let batchResume = false, redrawRequest=0;
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
    // 卡片编号：普通结果用分段序号；本段抽卡结果用「原编号-第几次」显示（如 #02-1、#02-2）。
    const numberOrder = record => {
        const match = /(\d+)(?:-(\d+))?/.exec(String(record?.label || ""));
        if (match) return [Number(match[1]), Number(match[2] || 0), Number(record?.serial) || 0];
        const serial = Number(record?.serial) || 0;
        return [serial || 1e9, 0, serial];
    };
    const displayedBase = record => {
        const byLabel = /(\d+)(?:-(\d+))?/.exec(String(record?.label || ""));
        return Number(record?.redraw_base) || (byLabel ? Number(byLabel[1]) : 0) || Number(record?.serial) || 0;
    };
    function placeRedrawRecord(kind, item) {
        const list = mediaRecords[kind];
        const at = list.indexOf(item);
        if (at >= 0) list.splice(at, 1);
        const base = Number(item.redraw_base) || 0;
        let insert = -1;
        if (base) list.forEach((entry, index) => { if (numberOrder(entry)[0] === base) insert = index; });
        if (insert >= 0) list.splice(insert + 1, 0, item);
        else list.push(item);
    }
    const recordLabel = (record, index) => {
        if (record?.redraw && Number(record.redraw_base) > 0) {
            const base = String(Number(record.redraw_base)).padStart(2, "0");
            const count = Number(record.redraw_count) > 0 ? Number(record.redraw_count) : 1;
            return `#${base}-${count}`;
        }
        const serial = Number(record?.serial) > 0 ? Number(record.serial) : Number(index) + 1;
        return `#${String(serial).padStart(2, "0")}`;
    };
    let previewPinned = false;
    // 记录持久化到节点属性：重新打开工作流/刷新页面后仍能在分类里看到缩略图
    const savedMedia = node.properties?.ad_h3_media_records;
    if (savedMedia && typeof savedMedia === "object") {
        for (const kind of ["image", "audio", "video"]) {
            if (Array.isArray(savedMedia[kind]))
                mediaRecords[kind] = savedMedia[kind].filter(item => item && item.file).slice(-60)
                    .map((item, index) => ({...item, serial: item.redraw ? 0 : (Number(item.serial) > 0 ? Number(item.serial) : index + 1)}));
        }
    }
    const persistMedia = () => {
        node.properties ||= {};
        const strip = list => list.slice(-60).map(item => {
            const {thumb_b64, thumb_mime, ...rest} = item;
            return rest;
        });
        node.properties.ad_h3_media_records = {image: strip(mediaRecords.image), audio: strip(mediaRecords.audio), video: strip(mediaRecords.video)};
    };
    // 工作流没存过分段缩略图时，用已保存的媒体记录兜底，保证重新打开后预览区仍有历史缩略图。
    if (!stripStore.segment.length && !stripStore.merged.length) {
        const fallback = [];
        for (const kind of ["image", "audio", "video"]) {
            for (const record of mediaRecords[kind]) {
                if (!record?.file) continue;
                fallback.push({file: record.file, origin: record.origin || "media",
                    label: record.label || (Number(record.serial) > 0 ? `第 ${record.serial} 段` : "历史生成"),
                    kind: record.kind || kind,
                    segment_index: Number(record.segment_index) || Number(record.serial) || 0});
            }
        }
        stripStore.segment = fallback.sort((a, b) => (a.segment_index || 0) - (b.segment_index || 0)).slice(-60);
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
        // 切分类后按实际内容重算节点高度，避免缩略图被裁掉露出半截
        setTimeout(() => {
            try {
                const size = node.computeSize?.();
                if (size) node.setSize([node.size[0], Math.max(200, Math.round(size[1]))]);
                node.setDirtyCanvas?.(true, true);
            } catch {}
        }, 0);
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
        // 选中哪张预览只作为本段抽卡的内部目标，不再给缩略图加黄色高亮（用户 2026-10-28 要求去掉）。
        void button;
        const redoButton = root.querySelector('[data-loop="redo"]');
        if (redoButton) {
            redoButton.title = resolved
                ? `重新抽第 ${resolved} 段（已选中预览）；运行中请先点「停止」`
                : "重抽当前段；点下方预览可指定要重抽的段（运行中请先停止）";
        }
        if (record) {
            node._adH3ReviewStatus?.(resolved ? `已选为本段抽卡目标：第 ${resolved} 段（旧版本文件保留）`
                                              : "该预览缺少段号，本段抽卡将重抽当前段");
        }
    }
    function renderMedia(kind) {
        const list = mediaLists[kind];
        if (!list) return;
        list.replaceChildren();
        mediaRecords[kind].forEach((record, index) => {
            const button = document.createElement("button");
            button.type = "button"; button.className = "ad-h3-media-card";
            button.dataset.file = record.file || ""; button.dataset.origin = record.origin || "media";
            // 重抽结果：编号标黄，方便和普通结果区分。
            button.classList.toggle("redraw", !!record.redraw);
            if (record.redraw) button.title = "本段抽卡结果（黄色编号）";
            const labelText = recordLabel(record, index);
            if (kind === "image") {
                const image = document.createElement("img");
                image.src = record.thumb_b64
                    ? `data:${record.thumb_mime || "image/webp"};base64,${record.thumb_b64}`
                    : thumbnailUrl(record.origin || "media", record.file);
                image.alt = String(record.file || "").split("/").pop() || labelText;
                button.append(image);
            } else if (kind === "video") {
                const clip = document.createElement("video");
                clip.muted = true; clip.playsInline = true; clip.preload = "metadata";
                // 分段/合并结果 origin 是 session；缩略图接口只处理图片，视频卡片直接用媒体地址取首帧。
                clip.src = mediaUrl(record.origin || "media", record.file);
                clip.addEventListener("loadeddata", () => { try { clip.currentTime = 0.05; } catch {} }, {once: true});
                button.append(clip);
            } else {
                const icon = document.createElement("span");
                icon.className = "ad-h3-audio-icon"; icon.textContent = "♪";
                button.append(icon);
            }
            const label = document.createElement("span");
            label.className = "ad-h3-media-serial"; label.textContent = labelText;
            button.append(label);
            const remove = document.createElement("span");
            remove.className = "ad-h3-media-remove"; remove.textContent = "×"; remove.title = "删除这条缩略图记录";
            remove.onclick = event => {
                event.stopPropagation();
                clearRecordPreview(record, null);
            };
            button.append(remove);
            button.onclick = () => {
                pickAB(record, kind);
                show({...record, kind, label: recordLabel(record, index), url: mediaUrl(record.origin || "media", record.file)});
                markActiveCard(record);
                setRedoTarget(record, button);
                // 点选只切换素材与本段抽卡目标；试听由播放器的播放按钮触发。
            };
            list.append(button);
        });
        if (!mediaRecords[kind].length) {
            const empty = document.createElement("span"); empty.className = "ad-h3-strip-empty";
            empty.textContent = kind === "image" ? "暂无图像" : kind === "video" ? "暂无视频" : "暂无音频";
            list.append(empty);
        }
        setTimeout(() => {
            try {
                const size = node.computeSize?.();
                if (size) node.setSize([node.size[0], Math.max(240, Math.round(size[1]))]);
                node.setDirtyCanvas?.(true, false);
            } catch {}
        }, 0);
        if (mediaLists[kind]?.parentElement && !mediaLists[kind].parentElement.hidden) {
            setTimeout(() => {
                try {
                    const size = node.computeSize?.();
                    if (size) node.setSize([node.size[0], Math.max(200, Math.round(size[1]))]);
                    node.setDirtyCanvas?.(true, true);
                } catch {}
            }, 0);
        }
    }
    for (const button of tabButtons) button.onclick = () => setTab(button.dataset.tab);
    const clearButton = root.querySelector("[data-clear-preview]");
    if (clearButton) clearButton.addEventListener("click", async () => {
        // 只清空本轮进度与选用记录；预览内容、素材、分段、提示词和文件都保留。
        if (!window.confirm("只清空本轮生成进度和选用记录？（预览内容、素材、分段、提示词与已生成文件都保留）")) return;
        try { if(await node._adH3Act?.("clear", {skipConfirm:true})===false)return; } catch { return; }
        previewPinned = false;
        if (node._adH3ReviewStatus) node._adH3ReviewStatus("已清空本轮进度，预览内容与文件保留。");
        node.setDirtyCanvas?.(true, true);
    });
    const clearMediaButton = root.querySelector("[data-clear-media-preview]");
    if (clearMediaButton) clearMediaButton.addEventListener("click", () => {
        // 只清当前分类（图片/音频/视频）的预览记录，不删文件、不动进度；清空后主预览回到黑屏空闲态。
        const kind = activeTab || "image";
        const names = {image: "图片", audio: "音频", video: "视频"};
        const sameKind = record => inferMediaKind(record?.file, record?.kind) === kind;
        const stripMatches = ["segment", "merged"].flatMap(key =>
            [...stripStore[key], ...stripRecords[key]].filter(sameKind).map(record => ({key, record})));
        if (!(mediaRecords[kind] || []).length && !stripMatches.length) {
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
        for (const record of mediaRecords[kind] || []) hiddenStrips.add(stripKey("segment", record));
        for (const row of latestSessionState?.segments || []) for (const version of row.versions || []) {
            const record = {origin:"session", file:`${latestSessionState.id}/${version.file}`, kind:version.media_kind};
            if (sameKind(record)) hiddenStrips.add(stripKey("segment", record));
        }
        persistHiddenStrips();
        node.graph?.change?.(); // 通知工作流记录这次清空，刷新时使用新的预览状态。
        stripStore.segment = stripStore.segment.filter(record => !sameKind(record));
        stripStore.merged = stripStore.merged.filter(record => !sameKind(record));
        persistStripPreviews();
        mediaRecords[kind] = [];
        lastShown[kind] = null;
        if (hadRedoTarget) setRedoTarget(null, null);
        persistMedia();
        renderMedia(kind);
        syncStripFromMedia();
        renderRecords(merges, stripStore.merged, "merged");
        // 清空预览后主预览统一回到大黑屏空闲态（即使当前显示的是其它分类/分段预览）。
        active = null; generationKey = "";
        showLastForTab(kind);
        previewPinned = false;
        node.setDirtyCanvas?.(true, true);
        if (node._adH3ReviewStatus) node._adH3ReviewStatus(`已清空「${names[kind]}」预览，文件与进度保留。`);
    });
    const runningStatuses = ["queued", "running", "merging"];
    const sessionIsRunning = () => !!latestSessionState && runningStatuses.includes(latestSessionState.status);
    const interruptNow = () => { try { void fetch("/interrupt", { method: "POST", headers: { "Content-Type": "application/json" } }); } catch {} };
    for (const button of root.querySelectorAll("[data-loop]")) button.addEventListener("click", () => {
        const command = button.dataset.loop;
        if (node._adH3AffectedRunning && command !== "pause") {
            node._adH3Hint?.("正在运行所选段，请等待完成或先点停止。", true); return;
        }
        // 用户主动开始生成/分段循环/批准/合并时解除预览固定，新结果照常自动显示。
        if (["start", "auto", "approve", "merge"].includes(command)) previewPinned = false;
        if (command === "start") {
            // 点「停止」时如果分段循环正在运行：抽卡只是预览备选，点「开始」续接原来的分段循环位置。
            if (batchResume) {
                // 抽卡还在生成（预览结果尚未到达）时点开始：提示稍后再点，不抢跑。
                if (sessionIsRunning() && !redrawReview) {
                    node._adH3Hint?.("本段抽卡的生成还未结束，完成后点「开始」续接分段循环", false);
                    return;
                }
                batchResume = false; redrawPending = null; redrawReview = null;
                previewPinned = false;
                node._adH3Hint?.("已续接分段循环，从停止处继续…", false);
                if (typeof node._adH3Batch === "function") { void node._adH3Batch({merge:false}); return; }
            }
            // 分段循环已完成：点「开始」直接清空本轮进度（保留素材/分段/提示词/已生成文件）并重新开始第 1 段。
            if (latestSessionState && latestSessionState.status === "completed") {
                redrawPending = null; redrawReview = null; batchResume = false;
                node._adH3Hint?.("本轮已完成，正在清空进度并重新开始…", false);
                if (typeof node._adH3Act === "function") {
                    Promise.resolve(node._adH3Act("clear", {skipConfirm:true}))
                        .then(result => {
                            if (result !== false && typeof node._adH3Run === "function") node._adH3Run();
                        })
                        .catch(() => {});
                    return;
                }
            }
            // 与 ComfyUI 蓝色“运行”按钮一致：把当前工作流加入执行队列
            // 当前段已有有效但未批准的结果：点「开始」= 先按「通过」处理，再继续下一段，
            // 不再把请求直接塞进队列触发后台“当前段有待审核结果”的保护。
            if (latestSessionState && latestSessionState.status === "review") {
                const currentRow = latestSessionState.segments?.[latestSessionState.cursor];
                if (currentRow?.valid && typeof node._adH3Act === "function") {
                    node._adH3Hint?.("当前段有待审核结果：先按「通过」处理，并继续生成下一段…", false);
                    Promise.resolve(node._adH3Act("approve"))
                        .then(result => {
                            if (result && result.status !== "completed" && typeof node._adH3Run === "function") node._adH3Run();
                        })
                        .catch(error => {
                            node._adH3Hint?.("通过当前段失败：" + (error?.message || String(error)), true);
                        });
                    return;
                }
                node._adH3Hint?.("当前段有待审核结果，请先在预览审批点「同意批准」或「本段抽卡」。", true);
                return;
            }
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
            if (latestSessionState) latestSessionState = {...latestSessionState, status: "paused"};
            if (typeof node._adH3Act === "function") {
                Promise.resolve(node._adH3Act("stop"))
                    .then(result => { if (stopping===redrawRequest && result === false) interruptNow(); })
                    .catch(() => {if(stopping===redrawRequest)interruptNow();});
            } else interruptNow();
            return;
        }
        if (command === "redo") {
            // 运行中需要先点「停止」：流程待命后再抽卡。
            if (sessionIsRunning()) {
                node._adH3Hint?.("分段循环运行中：请先点「停止」，流程待命后再点「本段抽卡」", true);
                return;
            }
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
            redrawPending = {index: pendingIndex, base: Number(base), exact: targetIndex !== null};
            redrawReview = null;
            previewPinned = false;  // 抽卡结果要自动显示
            if (typeof node._adH3Act === "function") {
                node._adH3Hint?.(`正在本段抽卡（第 ${base} 段，仅预览备选，不改分段循环进度）…`, false);
                Promise.resolve(node._adH3Act("redo_preview", targetIndex === null ? undefined : {index: targetIndex}))
                    .then(result => { if(request!==redrawRequest)return; if (result === false && !node._adH3ActionCancelled) {
                        redrawPending = null;
                        node._adH3Hint?.("本段抽卡未完成："+(node._adH3LastActionError||"任务未加入队列、已停止或执行失败，请核对运行记录后重试"),true);
                    } })
                    .catch(error => { if(request!==redrawRequest)return; redrawPending = null; node._adH3Hint?.("本段抽卡未成功加入队列："+(error?.message||String(error)),true); });
            } else if (node._adH3ReviewStatus) node._adH3ReviewStatus("请先连接「当前段上下文」再使用", true);
            return;
        }
        if (command === "approve" && sessionIsRunning()) {node._adH3Hint?.("当前任务尚未结束，请完成后再批准。",true);return;}
        if (command === "approve" || command === "clear") {redrawRequest++;redrawPending=null;redrawReview=null;batchResume=false;}
        if (command === "merge") { void mergeSelectedVideos(); return; }
        if (typeof node._adH3Act === "function") void node._adH3Act(command, command === "pause" ? { skipConfirm: true } : undefined);
        else if (node._adH3ReviewStatus) node._adH3ReviewStatus("请先连接「当前段上下文」再使用", true);
    });
    const keyOf = record => `${record.origin || "session"}/${record.file}`;
    function playlistVideos() {
        return stripStore.segment.filter(record => inferMediaKind(record.file,record.kind)==="video"
            && !hiddenStrips.has(stripKey("segment",record)))
            .slice().sort((a,b)=>{const left=numberOrder(a),right=numberOrder(b);return left[0]-right[0]||left[1]-right[1]||left[2]-right[2];});
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
        for (const [key, button] of cards) button.classList.toggle("selected", key === active?.key);
    }
    function setMode(kind) {
        const isVideo = !kind || kind === "video" || kind === "segment";
        video.hidden = !isVideo;
        imageView.hidden = kind !== "image";
        audioView.hidden = kind !== "audio";
        transport.style.display = isVideo || kind === "audio" ? "" : "none";
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
            mediaEl.addEventListener("error", () => { hint.textContent = "视频无法播放，请重新生成预览"; });
        } else {
            mediaEl.className = "ad-h3-lightbox-img";
            mediaEl.src = src;
        }
        let scale = 1, offsetX = 0, offsetY = 0, dragging = false, startX = 0, startY = 0;
        const apply = () => { if (!isVideo) mediaEl.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`; };
        const close = () => overlay.remove();
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
            if (overlay.requestFullscreen) { void overlay.requestFullscreen().catch(() => {}); return; }
            if (isVideo && mediaEl.webkitEnterFullscreen) { try { mediaEl.webkitEnterFullscreen(); } catch {} }
        };
        overlay.append(mediaEl, download, fullscreen, closeButton, hint);
        overlay.dataset.role = "lightbox";
        document.body.append(overlay);
        const cleanup = () => {
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
            window.removeEventListener("keydown", keyHandler);
        };
        const observer = new MutationObserver(() => { if (!overlay.isConnected) { cleanup(); observer.disconnect(); } });
        observer.observe(document.body, {childList: true});
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
            title.textContent = `${active.label || "预览"} · 视频加载失败（文件可能已清理或服务繁忙），请刷新页面后重试`;
            node._adH3Hint?.("预览视频加载失败：文件可能已被清理或后端繁忙，请 Ctrl+F5 刷新后再试。", true);
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
        if (options.auto !== true) previewPinned = true;
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
    video.onerror = () => { title.textContent = `${active?.label || "预览"} · 文件无法播放，请重新生成预览`; };
    play.onclick = () => {
        if (!active) return;
        const media = playingMedia();
        if (media.paused) void media.play().catch(() => { title.textContent = "暂时无法播放，请重新选择片段"; });
        else media.pause();
    };
    // 点击预览画面：播放 / 暂停
    video.addEventListener("click", () => {
        if (!active) return;
        if (video.paused) void video.play().catch(() => { title.textContent = "暂时无法播放，请重新选择片段"; });
        else video.pause();
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
    root.querySelector("[data-fullscreen]").onclick = () => { void root.querySelector(".ad-h3-screen").requestFullscreen?.().catch(() => {}); };
    function renderRecords(target, records, kind) {
        const stripKind = kind === "merged" ? "merged" : "segment";
        stripRecords[stripKind] = records;
        if (stripKind==="segment" && playlistEnabled && !playlistVideos().length) setPlaylistEnabled(false);
        const visible = records.filter(record => !hiddenStrips.has(stripKey(stripKind, record)));
        const wanted = new Set(visible.map(keyOf));
        for (const [key, button] of cards) if (button.parentElement === target && !wanted.has(key)) {button.remove(); cards.delete(key);}
        // 没有记录时给一行提示，避免只显示“分段生成 / 合并生成”两个空行。
        let placeholder = target.querySelector(".ad-h3-strip-empty");
        if (!visible.length) {
            if (!placeholder) {
                placeholder = document.createElement("span");
                placeholder.className = "ad-h3-strip-empty";
                target.append(placeholder);
            }
            placeholder.textContent = kind === "merged" ? "暂无合并结果" : "暂无分段";
        } else if (placeholder) {
            placeholder.remove();
        }
        for (const record of visible) {
            const key = keyOf(record);
            const itemKind = inferMediaKind(record.file, record.kind);
            const wantsImage = itemKind === "image";
            let button = cards.get(key);
            if (button && ((button.dataset.thumbKind === "image") !== wantsImage)) {
                button.remove(); cards.delete(key); button = null;
            }
            if (!button) {
                button = document.createElement("button"); button.type = "button"; button.className = "ad-h3-preview-thumb";
                button.dataset.thumbKind = wantsImage ? "image" : "video";
                // 缩略图直接用媒体首帧：不依赖后端缩略图接口，避免出现裂图图标。
                let thumb;
                if (wantsImage) {
                    thumb = document.createElement("img");
                    thumb.alt = ""; thumb.setAttribute("aria-hidden", "true");
                } else {
                    thumb = document.createElement("video");
                    thumb.muted = true; thumb.playsInline = true; thumb.disablePictureInPicture = true;
                    thumb.preload = "metadata"; thumb.setAttribute("aria-hidden", "true");
                    thumb.addEventListener("loadedmetadata", () => {
                        try { thumb.currentTime = Math.min(0.12, Math.max(0.01, (thumb.duration || 0.2) / 2)); } catch { /* 忽略定位失败 */ }
                    }, {once: true});
                }
                const label = document.createElement("span");
                const remove = document.createElement("span");
                remove.className = "ad-h3-preview-remove"; remove.textContent = "×"; remove.title = "删除这条预览记录（不删除文件）";
                remove.onclick = event => { event.stopPropagation(); hideStripRecord(stripKind, record); };
                button.append(thumb, remove, label); cards.set(key, button);
            }
            button.firstElementChild.src = mediaUrl(record.origin || "session", record.file);
            button.lastElementChild.textContent = record.label; button.title = record.label; button.setAttribute("aria-label", record.label);
            button.classList.toggle("redraw", !!record.redraw);
            button.classList.toggle("merge-selected", mergeSelection.has(key));
            button.onclick = event => {
                // Ctrl/Cmd + 点击：多选要合并的视频片段（外框高亮），不改变当前预览。
                if (event?.ctrlKey || event?.metaKey) {
                    event.preventDefault(); event.stopPropagation();
                    toggleMergeSelection(record, itemKind);
                    return;
                }
                pickAB({...record, kind: itemKind}, itemKind);
                if (stripKind==="segment" && itemKind==="video" && playlistEnabled) playPlaylistRecord(record);
                else show({...record, key, kind: itemKind, url: mediaUrl(record.origin || "session",record.file)});
                // 分段生成缩略图与图片缩略图一致：点选即成为「本段抽卡」目标（合并片段无段号，只预览）。
                if (stripKind === "segment") setRedoTarget({...record, kind: itemKind}, button);
            };
            target.append(button);
        }
        markCards();
    }
    // 各类卡片 X 都清除该条预览的全部显示记录；不弹确认、不删除文件或进度。
    function clearRecordPreview(record, stripKind = null) {
        const origin = record.origin || (stripKind ? "session" : "media");
        const same = item => item?.file === record.file && (item.origin || (stripKind ? "session" : "media")) === origin;
        const kind = inferMediaKind(record.file, record.kind);
        const all = stripKind ? (stripRecords[stripKind] || []).filter(item => !hiddenStrips.has(stripKey(stripKind,item))) : mediaRecords[kind];
        const position = all.findIndex(same);
        const remaining = all.filter(item => !same(item));
        const removedActive = !!active && active.file === record.file && (active.origin || "media") === origin;
        const canonical = {...record, origin};
        hiddenStrips.add(stripKey("segment",canonical));
        hiddenStrips.add(stripKey("merged",canonical));
        persistHiddenStrips();
        for (const mediaKind of ["image","audio","video"]) {
            mediaRecords[mediaKind] = mediaRecords[mediaKind].filter(item => !same(item));
            if (same(lastShown[mediaKind])) lastShown[mediaKind] = null;
            renderMedia(mediaKind);
        }
        if (same(redoTarget)) setRedoTarget(null,null);
        if (mergeSelection.delete(keyOf(canonical))) paintMergeSelection();
        stripStore.segment = stripStore.segment.filter(item => !same(item));
        stripStore.merged = stripStore.merged.filter(item => !same(item));
        persistMedia(); persistStripPreviews();
        renderRecords(segments,stripStore.segment,"segment");
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
    // 合并多选：只允许视频；按显示编号（#05、#02-1…）排序后合并。
    const mergeOrderKey = record => {
        const match = /(\d+)(?:-(\d+))?/.exec(String(record?.label || ""));
        return match ? [Number(match[1]), Number(match[2] || 0), Number(record?.serial) || 0] : [1e9, 0, Number(record?.serial) || 0];
    };
    function paintMergeSelection() {
        for (const [key, button] of cards) button.classList.toggle("merge-selected", mergeSelection.has(key));
    }
    function toggleMergeSelection(record, kind) {
        if (kind !== "video") {
            node._adH3Hint?.("只能选择视频片段进行合并", true);
            return;
        }
        const key = keyOf(record);
        if (mergeSelection.has(key)) mergeSelection.delete(key);
        else mergeSelection.set(key, {...record, kind: "video"});
        paintMergeSelection();
        node._adH3Hint?.(mergeSelection.size ? `已选中 ${mergeSelection.size} 个视频片段，点「合并已通过片段」按序号合并` : "已取消合并选中", false);
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
    function addMergedPreview(source) {
        if (!source?.file) return;
        node.properties ||= {};
        const list = numberMergedHistory(Array.isArray(node.properties.ad_h3_merged_previews) ? node.properties.ad_h3_merged_previews : []);
        const serial = Math.max(Number(node.properties.ad_h3_merge_serial)||0, ...list.map(item=>Number(item.merge_serial)||0))+1;
        const label = `选中合并 #${String(serial).padStart(2,"0")}`;
        node.properties.ad_h3_merge_serial = serial;
        list.push({session:currentSession||"media",file:source.file,origin:"media",label,merge_serial:serial});
        node.properties.ad_h3_merged_previews = list.slice(-24);
        const records = list.map((item, index) => item.origin === "media"
            ? {file: item.file, origin: "media", label: item.label || `合并片段 ${index+1}`, kind: inferMediaKind(item.file)}
            : {file: `${item.session}/${item.file}`, origin: "session", label: item.label || `合并片段 ${index+1}`, kind: inferMediaKind(item.file)});
        stripStore.merged = records;
        persistStripPreviews();
        renderRecords(merges, records, "merged");
        previewPinned = true;
        show({...source, kind: "video", label, url: mediaUrl(source.origin || "media", source.file)}, {auto: true});
    }
    async function mergeSelectedVideos() {
        const selected = [...mergeSelection.values()].sort((left, right) => {
            const a = mergeOrderKey(left), b = mergeOrderKey(right);
            return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
        });
        if (selected.length < 2) {
            node._adH3Hint?.("请按住 Ctrl 在「分段生成」里点选至少 2 个视频，再点「合并已通过片段」", true);
            return;
        }
        node._adH3Hint?.(`正在按序号合并 ${selected.length} 个视频…`, false);
        try {
            const response = await fetch("/aiden_h3/merge_clips", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({kind: "video", clips: selected.map(item => ({origin: item.origin || "media", file: item.file}))}),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "合并失败");
            addMergedPreview(data.source || {});
            mergeSelection.clear();
            paintMergeSelection();
            node._adH3Hint?.(`已按序号合并 ${selected.length} 个视频。`, false);
        } catch (error) {
            node._adH3Hint?.(error.message || "合并失败", true);
        }
    }
    function updateState(state) {
        if (disposed || !state?.id) return;
        latestSessionState = {...state};
        if (currentSession && currentSession !== state.id) {
            video.pause(); video.removeAttribute("src"); video.load(); active = null; generationKey = "";
            segments.replaceChildren(); merges.replaceChildren(); cards.clear();
            title.textContent = "生成完成后在这里预览视频";
            setIdle(true);
            renderRecords(segments, stripStore.segment, "segment");
            renderRecords(merges, stripStore.merged, "merged");
        }
        currentSession = state.id;
        node.properties ||= {};
        let merged = numberMergedHistory(Array.isArray(node.properties.ad_h3_merged_previews) ? node.properties.ad_h3_merged_previews : []);
        node.properties.ad_h3_merged_previews = merged;
        if (state.merged && !merged.some(item => item.session === state.id && item.file === state.merged)) {
            merged = [...merged, {session: state.id, file: state.merged}].slice(-24);
            node.properties.ad_h3_merged_previews = merged;
        }
        const liveMergedRecords = merged.map((item,index) => item.origin === "media"
            ? {file:item.file,origin:"media",label:item.label||`合并片段 ${index+1}`,kind:inferMediaKind(item.file)}
            : {file:`${item.session}/${item.file}`,origin:"session",label:item.label||`合并片段 ${index+1}`,kind:inferMediaKind(item.file)});
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
    function markRedrawRecord(item, kind, record) {
        if (!(redrawPending && (redrawPending.index === Number(record.segment_index)
            || !redrawPending.exact || !Number(record.segment_index)))) return false;
        const base = Number(redrawPending.base) || Number(record.segment_index) || redrawPending.index;
        const used = mediaRecords[kind].filter(entry => entry !== item && entry.redraw
            && Number(entry.redraw_base) === base).length;
        item.redraw = true;
        item.redraw_base = base;
        item.redraw_count = used + 1;
        item.serial = 0;  // 抽卡结果不占用正常生成的整数序号
        item.label = recordLabel(item, 0);
        redrawReview = Number(record.segment_index) || redrawPending.index;
        redrawPending = null;
        node._adH3Hint?.(`第 ${base} 段已重抽完成（${item.label}，仅预览备选，不改变循环进度）；点「开始」续接分段循环`, false);
        return true;
    }
    function receive(record) {
        // 后端已按保存路径写盘；这里只把结果显示出来并提示保存结果/错误。
        try {
            if (record?.auto_save_error) node._adH3ReviewStatus?.(`保存失败：${record.auto_save_error}`, true);
            else if (record?.auto_saved) node._adH3ReviewStatus?.(`已保存：${record.auto_saved}`);
        } catch (_error) {}
        if (!record?.file) return;
        node._adH3StandaloneReady = true;
        const kind = record.kind || "video";
        const label = kind === "image" ? "本次图像" : kind === "audio" ? "本次音频" : "本次生成片段";
        if (kind === "image" || kind === "audio") {
            const nextSerial = () => Math.max(0, ...mediaRecords[kind].map(entry => Number(entry.serial) || 0)) + 1;
            const existing = mediaRecords[kind].find(entry => entry.file === record.file && entry.origin === record.origin);
            const item = {...record, label, serial: existing?.serial || nextSerial()};
            const index = mediaRecords[kind].findIndex(entry => entry.file === record.file && entry.origin === record.origin);
            if (index >= 0) mediaRecords[kind][index] = item; else mediaRecords[kind].push(item);
            // 本段抽卡结果：编号为「原编号-第几次」（黄色），并记录待继续的段号。
            if (markRedrawRecord(item, kind, record)) placeRedrawRecord(kind, item);
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
        const existingVideo = mediaRecords.video.find(entry => entry.file === record.file && entry.origin === record.origin);
        const item = {...record, label, kind: "video", serial: existingVideo?.serial || videoSerial};
        const index = mediaRecords.video.findIndex(entry => entry.file === record.file && entry.origin === record.origin);
        if (index >= 0) mediaRecords.video[index] = item; else mediaRecords.video.push(item);
        if (markRedrawRecord(item, "video", record)) placeRedrawRecord("video", item);
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
        hiddenStrips.clear();
        for (const key of node.properties?.ad_h3_hidden_strips || []) hiddenStrips.add(String(key));
        const strips = node.properties?.ad_h3_strip_previews;
        for (const kind of ["segment","merged"]) {
            if (Array.isArray(strips?.[kind])) stripStore[kind] = strips[kind].filter(item=>item?.file).slice(-60);
        }
        stripStore.merged = numberMergedHistory(stripStore.merged);
        if (Array.isArray(node.properties?.ad_h3_merged_previews))
            node.properties.ad_h3_merged_previews = numberMergedHistory(node.properties.ad_h3_merged_previews);
        persistStripPreviews();
        renderRecords(merges, stripStore.merged, "merged");
        const saved = node.properties?.ad_h3_media_records;
        if (!saved || typeof saved !== "object") {
            renderRecords(segments, stripStore.segment, "segment");
            return;
        }
        for (const kind of ["image", "audio", "video"]) {
            mediaRecords[kind] = (Array.isArray(saved[kind]) ? saved[kind] : [])
                .filter(item => item && item.file).slice(-60)
                .map((item, index) => ({...item, serial: item.redraw ? 0 : (Number(item.serial) > 0 ? Number(item.serial) : index + 1)}));
            renderMedia(kind);
        }
        // 重新打开工作流时保持原先的分类与空闲画面，不自动弹出别的预览。
        syncStripFromMedia();
        setTab(node.properties?.ad_h3_active_tab || "image", {sync: false});
    }
    // 初始就是空闲态：两排缩略图先给出“暂无”提示，避免只显示空行。
    syncStripFromMedia();
    renderRecords(merges, stripStore.merged, "merged");
    renderMedia("image");
    renderMedia("audio");
    setTab(node.properties?.ad_h3_active_tab || (mediaRecords.image.length ? "image" : mediaRecords.audio.length ? "audio" : "image"), {sync: false});
    return {show, updateState, receive, restoreLegacy, restoreSavedMedia, getTab: () => activeTab,
        clearPin(){ previewPinned = false; }, get revision(){return revision;},
        dispose(){disposed=true; setPlaylistEnabled(false); setIdle(true); video.pause(); video.removeAttribute("src"); video.load(); cards.clear();}};
}

export function mountNativePlayer(node, api) {
    if (node._adH3Preview) return;
    const root = document.createElement("div");
    const url = (route, origin, file) => api.apiURL(`/aiden_h3/${route}/${origin}/${file.split("/").map(encodeURIComponent).join("/")}`);
    node._adH3Preview = createNativePlayer(node,root,(origin,file)=>url("media",origin,file),(origin,file)=>url("thumbnail",origin,file));
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
    node.setSize([Math.max(node.size[0],MIN_NODE_WIDTH),Math.max(node.size[1],460)]);
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

