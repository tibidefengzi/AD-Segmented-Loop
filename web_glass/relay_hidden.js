// 潜桥内部反射层：
// - 新工作流：提交时把内部桥接/存盘/裁头节点注入 prompt，画布上完全不出现。
// - 旧工作流：加载时把三个外部 H3Relay 节点迁移掉，保留连线/参数；之后同样走内部注入。
// - 选择器 0 = 关闭，5/22/39 = 开启；C 直接由选择器决定。

export const INTERNAL_TYPES = ["ADH3InternalBridge", "ADH3InternalSave", "ADH3InternalTrim"];
export const RELAY_LABELS = {
    0: "0 · 关闭",
    5: "5 · 上下文5帧",
    22: "22 · 上下文22帧（推荐）",
    39: "39 · 上下文39帧",
};
export function relayValue(raw) {
    if (typeof raw === "number" && [0, 5, 22, 39].includes(raw)) return raw;
    const text = String(raw ?? "").trim();
    const match = text.match(/^(\d+)/);
    const value = match ? Number(match[1]) : 0;
    return [0, 5, 22, 39].includes(value) ? value : 0;
}
export function normalizeRelayWidget(node) {
    const selector = widget(node, "潜桥续接");
    if (!selector) return;
    const value = relayValue(selector.value);
    const label = RELAY_LABELS[value];
    if (label && selector.value !== label) selector.value = label;
}
export const LEGACY_TYPES = ["H3RelayCopyBridge", "H3RelayLatentSave", "H3RelayTrimAV", "H3RelayLatentLoad", "H3RelayApplyEditMask"];

const num = v => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
};

function widget(node, name) {
    return (node?.widgets || []).find(w => w.name === name);
}

function relayChoice(node) {
    const w = widget(node, "潜桥续接");
    if (w) {
        return relayValue(w.value);
    }
    // 兼容极旧节点：从旧模式/上下文推一次
    const mode = String(widget(node, "潜桥模式")?.value || "");
    const context = num(widget(node, "潜桥上下文帧数")?.value || 0);
    return mode && mode !== "关闭" && [5, 22, 39].includes(context) ? context : 0;
}

function hideLegacyWidgets(node) {
    for (const name of ["潜桥模式", "潜桥上下文帧数"]) {
        const w = widget(node, name);
        if (!w) continue;
        w.type = "hidden";
        w.hidden = true;
        try { w.computeSize = () => [0, 0]; } catch (_) {}
    }
    const selector = widget(node, "潜桥续接");
    if (selector) {
        selector.type = "combo";
        selector.hidden = false;
        try { delete selector.computeSize; } catch (_) {}
    }
    try { node.setSize?.(node.computeSize?.() || node.size); } catch (_) {}
    try { node.graph?.setDirtyCanvas?.(true, true); } catch (_) {}
}

function linkOf(graph, id) {
    if (id == null) return null;
    return graph?.links?.get?.(id) || graph?.links?.[id] || null;
}

function nodeById(graph, id) {
    return graph?.getNodeById?.(Number(id)) || graph?.getNodeById?.(id) || (graph?._nodes || []).find(n => String(n.id) === String(id));
}

function removeNode(graph, node) {
    if (!node) return;
    for (const input of node.inputs || []) {
        if (input.link != null) {
            try { graph.removeLink?.(input.link); } catch (_) {}
        }
    }
    for (const output of node.outputs || []) {
        for (const id of [...(output.links || [])]) {
            try { graph.removeLink?.(id); } catch (_) {}
        }
    }
    try { graph.remove?.(node); } catch (_) {}
}

function firstNode(graph, type) {
    return (graph?._nodes || []).find(n => (n.comfyClass || n.type) === type);
}

function enabledSegmentNodes(graph) {
    return (graph?._nodes || []).filter(node => (node?.comfyClass || node?.type) === "AidenH3Segment" && relayChoice(node) > 0);
}

function promptLink(node, name) {
    const value = node?.inputs?.[name];
    return Array.isArray(value) && value.length === 2 ? value : null;
}

function previewEntriesForSegment(prompt, segId, contextIdx) {
    const found = [];
    for (const [id, node] of Object.entries(prompt || {})) {
        if (node?.class_type !== "AidenH3VideoPreview") continue;
        const context = promptLink(node, "context");
        if (!context) continue;
        if (String(context[0]) !== String(segId) || Number(context[1]) !== Number(contextIdx)) continue;
        found.push([id, node]);
    }
    return found;
}

function findSamplerUpstream(prompt, startLink, maxDepth = 8) {
    let link = startLink;
    const seen = new Set();
    for (let depth = 0; depth < maxDepth && link; depth++) {
        const id = String(link[0]);
        if (seen.has(id)) return null;
        seen.add(id);
        const node = prompt[id];
        if (!node) return null;
        if (String(node.class_type || "").includes("Sampler")) return [id, node];
        link = promptLink(node, "samples") || promptLink(node, "latent") || promptLink(node, "images");
    }
    return null;
}

function originatesFromDecodedAudio(prompt, link, depth = 6, seen = new Set()) {
    if (!Array.isArray(link) || depth < 0) return false;
    const id = String(link[0]);
    if (seen.has(id)) return false;
    seen.add(id);
    const node = prompt[id];
    if (!node) return false;
    const cls = String(node.class_type || "");
    if (cls.includes("VAEDecodeAudio")) return true;
    if (!/Audio|Decode/i.test(cls)) return false;
    for (const [name, value] of Object.entries(node.inputs || {})) {
        if (!Array.isArray(value) || value.length !== 2) continue;
        if (!/audio|samples|waveform/i.test(name)) continue;
        if (originatesFromDecodedAudio(prompt, value, depth - 1, seen)) return true;
    }
    return false;
}

const RELAY_MIGRATION = {
    H3RelayCopyBridge: { outputs: { latent: "latent", conditioning: "conditioning" }, fallback: { 0: "latent", 3: "conditioning" } },
    H3RelayTrimAV: { outputs: { images: "images", audio: "audio" }, fallback: { 0: "images", 1: "audio" } },
};

function reconnectMovedOutputs(graph, node) {
    const spec = RELAY_MIGRATION[node?.type] || { outputs: {}, fallback: {} };
    const inputs = new Map((node.inputs || []).map(input => [input.name, input]));
    (node.outputs || []).forEach((output, index) => {
        const key = String(output?.name || "");
        const inputName = Object.prototype.hasOwnProperty.call(spec.outputs, key)
            ? spec.outputs[key]
            : (spec.fallback[index] || null);
        if (!inputName) return; // report / trim_frames / prev_tail 等没有对应输入，保留空
        const input = inputs.get(inputName);
        const incoming = linkOf(graph, input?.link);
        const source = incoming ? nodeById(graph, incoming.origin_id) : null;
        if (!source) return;
        for (const id of [...(output?.links || [])]) {
            const link = linkOf(graph, id);
            if (!link) continue;
            const target = nodeById(graph, link.target_id);
            if (!target) continue;
            try { graph.removeLink?.(id); } catch (_) {}
            try { source.connect(incoming.origin_slot, target, link.target_slot); } catch (_) {}
        }
    });
}

export function migrateLegacyRelay(graph) {
    if (!graph) return;
    const segment = firstNode(graph, "AidenH3Segment");
    const bridge = firstNode(graph, "H3RelayCopyBridge");
    const trim = firstNode(graph, "H3RelayTrimAV");
    const save = firstNode(graph, "H3RelayLatentSave");
    const loader = firstNode(graph, "H3RelayLatentLoad");
    if (!segment) return;

    // 1. 确定选择器 C：优先桥的 context_frames，其次旧 AD 上下文。
    const selector = widget(segment, "潜桥续接");
    const storedChoice = segment.properties?.ad_relay_choice;
    const hasStoredChoice = storedChoice === 0 || [5, 22, 39].includes(Number(storedChoice));
    if (hasStoredChoice) {
        if (selector) selector.value = RELAY_LABELS[Number(storedChoice)];
    } else if (!segment.properties || segment.properties.ad_relay_ui_version !== 2) {
        const oldMode = String(widget(segment, "潜桥模式")?.value || "关闭");
        const oldC = num(widget(segment, "潜桥上下文帧数")?.value || 0);
        let c = 0;
        if (oldMode !== "关闭") {
            const bridgeC = bridge ? num(widget(bridge, "context_frames")?.value) : 0;
            c = [5, 22, 39].includes(bridgeC) ? bridgeC : ([5, 22, 39].includes(oldC) ? oldC : 22);
        }
        if (selector) selector.value = RELAY_LABELS[c ? c : 0];
        segment.properties ||= {};
        segment.properties.ad_relay_ui_version = 2;
    }
    normalizeRelayWidget(segment);
    try { segment.properties.ad_relay_choice = relayValue(widget(segment, "潜桥续接")?.value); } catch (_error) {}
    hideLegacyWidgets(segment);

    // 1b. 旧的 h3-relay ApplyEditMask 迁移到 AD 自己的 EditMask（不依赖外部插件）。
    const legacyEditMask = firstNode(graph, "H3RelayApplyEditMask");
    if (legacyEditMask) {
        try { replaceNodeType(graph, legacyEditMask, "AidenH3EditMask"); } catch (_) {}
    }

    // 2. 桥：把它的每一路输出按“同名输入”退回到上游（latent 与第 4 路 conditioning 都要保留）。
    if (bridge) {
        reconnectMovedOutputs(graph, bridge);
        removeNode(graph, bridge);
    }
    if (loader) removeNode(graph, loader);

    // 3. 保存节点没有下游，直接移除。
    if (save) removeNode(graph, save);

    // 4. 裁头：images 与 audio 两路都按同名输入退回上游。
    if (trim) {
        reconnectMovedOutputs(graph, trim);
        removeNode(graph, trim);
    }
    try { graph.setDirtyCanvas?.(true, true); } catch (_) {}
}


function replaceNodeType(graph, oldNode, newType) {
    if (!oldNode || !newType) return null;
    const ctor = globalThis.LiteGraph?.createNode?.(newType);
    if (!ctor) return null;
    ctor.pos = [...(oldNode.pos || [0, 0])];
    ctor.size = [...(oldNode.size || [220, 120])];
    ctor.title = oldNode.title || newType;
    graph.add(ctor);
    for (let i = 0; i < (oldNode.inputs || []).length; i++) {
        const input = oldNode.inputs[i];
        const link = linkOf(graph, input?.link);
        if (!link) continue;
        const origin = nodeById(graph, link.origin_id);
        if (origin && typeof origin.connect === "function") {
            try { origin.connect(link.origin_slot, ctor, Math.min(i, (ctor.inputs || []).length - 1)); } catch (_) {}
        }
    }
    for (let i = 0; i < (oldNode.outputs || []).length; i++) {
        const output = oldNode.outputs[i];
        for (const id of [...(output.links || [])]) {
            const link = linkOf(graph, id);
            if (!link) continue;
            const target = nodeById(graph, link.target_id);
            if (!target) continue;
            const newSlot = Math.min(i, (ctor.outputs || []).length - 1);
            try { graph.removeLink?.(id); } catch (_) {}
            if (typeof ctor.connect === "function") {
                try { ctor.connect(newSlot, target, link.target_slot); } catch (_) {}
            }
        }
    }
    removeNode(graph, oldNode);
    return ctor;
}

function nextNumericId(prompt) {
    let max = 0;
    for (const key of Object.keys(prompt || {})) {
        const n = Number(key);
        if (Number.isFinite(n)) max = Math.max(max, n);
    }
    return max + 1;
}

function outputIndex(node, name) {
    return (node?.outputs || []).findIndex(o => o.name === name);
}

export function injectHiddenRelay(serialized, graph) {
    const prompt = serialized?.output;
    if (!prompt || !graph) return serialized;
    const segments = enabledSegmentNodes(graph);
    if (!segments.length) return serialized; // 0 = 关闭，不注入

    // 旧外部节点尚未迁移时不能双重注入；显式报错，不能静默退回“无桥运行”（后台仍按潜桥规则改长度）。
    if (Object.values(prompt).some(n => LEGACY_TYPES.includes(n?.class_type))) {
        throw new Error("潜桥续接已开启，但画布里还残留旧版外部 H3 Relay 节点（桥/存盘/裁头/读取）。请保存工作流并重新打开，让插件自动迁移后再运行。");
    }
    if (Object.values(prompt).some(n => INTERNAL_TYPES.includes(n?.class_type))) return serialized; // 已注入，幂等

    let id = nextNumericId(prompt);
    const usedSamplers = new Set();
    for (const segmentNode of segments) {
        const segId = String(segmentNode.id);
        if (!prompt[segId]) continue; // 该节点未进入本次提交（旁路/未使用），不需要注入
        const contextIdx = outputIndex(segmentNode, "context");
        if (contextIdx < 0) {
            throw new Error("潜桥续接已开启，但分段节点缺少 context 输出；请更新插件并保存工作流后重试。");
        }
        const previews = previewEntriesForSegment(prompt, segId, contextIdx);
        if (!previews.length) {
            throw new Error("潜桥续接已开启，但找不到与该分段节点配对的「AD-分段预览」：请把预览节点的 context 输入连到本分段节点，并让它的 media 输入接本段的 CreateVideo 输出。");
        }
        const [previewId, preview] = previews[0];
        const mediaLink = promptLink(preview, "media");
        if (!mediaLink) {
            throw new Error("潜桥续接已开启，但预览节点（" + previewId + "）的 media 输入没有连线；请把 CreateVideo 的视频输出接到该输入。");
        }
        const createId = String(mediaLink[0]);
        const create = prompt[createId];
        if (!create || !String(create.class_type || "").includes("CreateVideo")) {
            throw new Error("潜桥续接已开启，但预览节点的 media 不是 CreateVideo 输出（当前为 " +
                (create?.class_type || "缺失") + "）；请改回 CreateVideo 再运行。");
        }
        const imagesLink = promptLink(create, "images");
        if (!imagesLink) {
            throw new Error("潜桥续接已开启，但 CreateVideo（" + createId + "）的 images 输入没有连线；请先把解码画面接上。");
        }
        const samplerFound = findSamplerUpstream(prompt, imagesLink);
        if (!samplerFound) {
            throw new Error("潜桥续接已开启，但无法沿 CreateVideo.images 找到采样器（SamplerCustomAdvanced）；请检查解码与采样器连线。");
        }
        const [samplerId, sampler] = samplerFound;
        if (usedSamplers.has(samplerId)) {
            throw new Error("潜桥续接已开启，但多个分段节点共用同一条采样/视频链（采样器 " + samplerId + "）；请为每个分段节点各接一套生成链。");
        }
        usedSamplers.add(samplerId);
        const latentLink = promptLink(sampler, "latent_image") || promptLink(sampler, "latent");
        if (!latentLink) {
            throw new Error("潜桥续接已开启，但采样器（" + samplerId + "）没有 latent_image 输入；请检查工作流后再运行。");
        }
        // 生成音频：沿 CreateVideo.audio 上溯查找 VAEDecodeAudio（允许中间夹音频后处理节点）；
        // 源音频 / 时间线音频 / 本段音频不经过裁头。
        const audioLink = promptLink(create, "audio");
        const generatedAudio = originatesFromDecodedAudio(prompt, audioLink);
        const contextFrames = relayChoice(segmentNode);
        const bridgeId = String(id++);
        const saveId = String(id++);
        const trimId = String(id++);
        prompt[bridgeId] = {
            class_type: "ADH3InternalBridge",
            inputs: {
                latent: latentLink,
                relay_context: [segId, contextIdx],
                context_frames: contextFrames,
                mask_mode: "hard",
                pin_audio: true,
            },
            _meta: { title: "AD-内部潜桥桥接" },
        };
        sampler.inputs.latent_image = [bridgeId, 0];
        prompt[saveId] = {
            class_type: "ADH3InternalSave",
            inputs: {
                latent: [samplerId, 0],
                relay_context: [segId, contextIdx],
                note: "AD hidden relay",
            },
            _meta: { title: "AD-内部潜桥存盘" },
        };
        const trimInputs = {
            images: imagesLink,
            relay_context: [segId, contextIdx],
            fps: 24.0,
        };
        if (generatedAudio) trimInputs.audio = audioLink;
        prompt[trimId] = {
            class_type: "ADH3InternalTrim",
            inputs: trimInputs,
            _meta: { title: "AD-内部潜桥裁头" },
        };
        create.inputs.images = [trimId, 0];
        // 生成音频与画面一样包含 C 帧预卷，必须一起裁；源音频/本段音频不经过这里。
        if (generatedAudio) create.inputs.audio = [trimId, 1];
    }
    return serialized;
}

const _hiddenTypes = new Map();

export function hideInternalNodeTypes() {
    // 内部类型只用于 prompt，不在画布/菜单出现；
    // 旧类型平时也隐藏，仅在加载含旧节点的工作流前临时恢复。
    try {
        const lg = globalThis.LiteGraph;
        if (!lg?.registered_node_types) return;
        for (const type of [...INTERNAL_TYPES, ...LEGACY_TYPES]) {
            if (!(type in lg.registered_node_types)) continue;
            _hiddenTypes.set(type, lg.registered_node_types[type]);
            delete lg.registered_node_types[type];
        }
    } catch (_) {}
}

export function restoreLegacyTypesForGraph(graphData) {
    try {
        const lg = globalThis.LiteGraph;
        if (!lg?.registered_node_types) return;
        const types = new Set((graphData?.nodes || []).map(n => n?.type));
        for (const type of LEGACY_TYPES) {
            if (!types.has(type) || !_hiddenTypes.has(type)) continue;
            lg.registered_node_types[type] = _hiddenTypes.get(type);
        }
    } catch (_) {}
}

export function hideLegacyTypesAfterLoad() {
    try {
        const lg = globalThis.LiteGraph;
        if (!lg?.registered_node_types) return;
        for (const type of LEGACY_TYPES) {
            if (type in lg.registered_node_types) {
                _hiddenTypes.set(type, lg.registered_node_types[type]);
                delete lg.registered_node_types[type];
            }
        }
    } catch (_) {}
}

export function prepareSegmentNode(node) {
    if (!node) return;
    node.properties ||= {};
    if (node.properties.ad_relay_ui_version !== 2) node.properties.ad_relay_ui_version = 2;
    normalizeRelayWidget(node);
    hideLegacyWidgets(node);
}
