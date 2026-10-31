/** 同一文件允许重复引用；每张参考卡各有使用标识，不改文件路径或段号。 */
export function repairSourceInstances(state) {
    if (!state || !Array.isArray(state.sources)) return state;
    const groups = new Map();
    for (const source of state.sources) if (source && typeof source.id === "string" && source.id) {
        if (!groups.has(source.id)) groups.set(source.id,[]);
        groups.get(source.id).push(source);
    }
    const safe = new Set([...groups].filter(([,items])=>new Set(items.map(s=>JSON.stringify([s.kind,s.origin,s.file]))).size===1).map(([id])=>id));
    const reserved = new Set(groups.keys());
    const fresh = base => { let n=2; while(reserved.has(`${base}__use_${n}`))n++; const id=`${base}__use_${n}`;reserved.add(id);return id; };
    const instances = new Map();
    const sources = state.sources.map(source=>{
        if (!source || !safe.has(source.id)) return source;
        if (!instances.has(source.id)) instances.set(source.id,[]);
        const ids=instances.get(source.id),id=ids.length?fresh(source.id):source.id;
        ids.push(id);return {...source,id};
    });
    const used = new Map(), seen = new Set();
    const imageIds = new Set((state.images||[]).map(item=>item?.id).filter(Boolean));
    const images = (state.images||[]).map(raw=>{
        if (!raw) return raw;
        const image={...raw},sid=image.source;
        if (safe.has(sid)&&groups.get(sid)[0].kind==="image") {
            const offset=used.get(sid)||0,ids=instances.get(sid);
            if(offset>=ids.length){const source={...groups.get(sid)[0],id:fresh(sid)};sources.push(source);ids.push(source.id);}
            image.source=ids[offset];used.set(sid,offset+1);
            let id=String(image.id||image.source);
            if(seen.has(id)){let n=2;while(imageIds.has(`${id}__ref_${n}`))n++;id=`${id}__ref_${n}`;imageIds.add(id);}
            image.id=id;
        }
        seen.add(image.id);return image;
    });
    return {...state,sources,images};
}

const FPS = 24, HALF_FRAME = .5 / FPS;
/** 当前保留素材的外边界；切断不丢内容，首尾裁掉的范围不能再拖回。 */
export function retainedSourceBounds(state, sourceId) {
    const clips=[...(state.video_tracks||[]),...(state.audio_tracks||[])].flat().filter(clip=>clip.source===sourceId);
    if(!clips.length)return null;
    return [Math.min(...clips.map(clip=>clip.src_in)),Math.max(...clips.map(clip=>clip.src_out))];
}
export function prependEmptyLane(lanes, limit = 3) {
    if (lanes.filter(lane => lane?.length).length >= limit) return false;
    const empty = lanes.findIndex(lane => !lane?.length);
    if (empty >= 0) lanes.splice(empty, 1);
    lanes.unshift([]);
    while (lanes.length < limit) lanes.push([]);
    return true;
}

export function playbackRange(playhead, total) {
    if (total>0 && playhead>=total-1e-9) return [0,total];
    const cursor=Math.max(0,Math.min(total,Math.round(playhead*FPS)/FPS));
    return [total>0 && cursor>=total ? 0 : cursor,total];
}

/** 新选片段从其最早起点播放；同一选择停止后继续白线，终点重播仍优先所选起点。 */
export function selectionPlaybackRange(playhead, total, clips, previousKey = "") {
    const signature = clips.map(clip=>[clip.id,clip.source,clip.tl_start,clip.src_in,clip.src_out])
        .sort((a,b)=>String(a[0]).localeCompare(String(b[0])));
    const selectionKey = signature.length ? JSON.stringify(signature) : "";
    const fromSelection = !!selectionKey && (selectionKey !== previousKey || playhead >= total - 1e-9);
    const start = fromSelection ? Math.min(...clips.map(clip=>clip.tl_start)) : playhead;
    return {range:playbackRange(start,total),selectionKey,fromSelection};
}

export const clipEnd = clip => clip.tl_start + clip.src_out - clip.src_in;

/**
 * 纯图片规则：只有图片素材、无音视频片段、无素材路径优先素材时适用。
 * 生成段由时间线上的「生成段」承载（可自由拖拽/延长最后一段），段数不再强制等于图片数量：
 * - 不传 count：段数 = 图片数量（首次载入或用户未手动设置时，仍自动跟随图片数量）；
 * - 传 count：段数 = 用户「应用分段」填的分段数；保留已有各段长度，只在末尾追加或从末尾删除差值段，
 *   新增段按每段目标帧数补默认长度。
 * 返回 null 表示不适用（混合素材/有音视频片段/素材路径模式）。
 */
export function imageOnlyClips(state, frames, options = {}) {
    const raw = Number(frames);
    if (!Number.isFinite(raw) || raw < 1) return null;
    const sources = state?.sources || [];
    if (!sources.length || sources.some(source => !source || source.kind !== "image" || source.priority)) return null;
    const hasClips = [...(state.video_tracks || []), ...(state.audio_tracks || [])].some(lane => lane?.length);
    if (hasClips) return null;
    const defaultFrames = Math.max(5, Math.round(raw));
    const defaultLength = Math.round((defaultFrames / FPS) * FPS) / FPS;
    const snap = value => Math.round(Number(value || 0) * FPS) / FPS;
    const requested = options.count == null ? sources.length : Math.round(Number(options.count));
    const count = Number.isInteger(requested) && requested >= 1 && requested <= 1000 ? requested : sources.length;
    const existing = (Array.isArray(state.generation_clips) ? state.generation_clips : [])
        .filter(clip => clip && Number(clip.src_out) > Number(clip.src_in || 0))
        .sort((left, right) => Number(left.tl_start || 0) - Number(right.tl_start || 0));
    const clips = [];
    const usedIds = new Set();
    for (let index = 0; index < Math.min(count, existing.length); index++) {
        const previous = existing[index];
        let id = previous.id || `image-clip-${index + 1}`;
        while (usedIds.has(id)) id = `${id}-${index + 1}`;
        usedIds.add(id);
        clips.push({ id, tl_start: Math.max(0, snap(previous.tl_start)), src_in: 0,
            src_out: Math.max(1 / FPS, snap(previous.src_out) - snap(previous.src_in || 0)) });
    }
    while (clips.length < count) {
        const start = clips.reduce((max, clip) => Math.max(max, clip.tl_start + clip.src_out), 0);
        let id = `image-clip-${clips.length + 1}`;
        for (let suffix = clips.length + 2; usedIds.has(id); suffix++) id = `image-clip-${suffix}`;
        usedIds.add(id);
        clips.push({ id, tl_start: snap(start), src_in: 0, src_out: defaultLength });
    }
    clips.sort((left, right) => left.tl_start - right.tl_start);
    const duration = clips.reduce((max, clip) => Math.max(max, clip.tl_start + clip.src_out), 0);
    return { count: clips.length, clips, duration, defaultFrames };
}

/** 纯图片模式是否仍跟随图片数量：用户点过「应用分段」或手动调过生成段后即停止跟随。 */
export function followsImageCount(recipe) {
    return !(recipe && (recipe.manual_count || recipe.custom));
}

/**
 * 清空参考图片：只移除 kind===image 的素材及其段别映射，视频/音频素材与轨道不变。
 * 返回移除的图片数量（用于提示）。
 */
export function clearImageSources(state) {
    const sources = state?.sources || [];
    const ids = new Set(sources.filter(source => source && source.kind === "image").map(source => source.id));
    if (!ids.size) return 0;
    state.sources = sources.filter(source => !ids.has(source.id));
    state.images = (state.images || []).filter(item => !item || !ids.has(item.source));
    return ids.size;
}

export function timelineDuration(state) {
    const hasAv=[...state.video_tracks,...state.audio_tracks].some(lane=>lane.length);
    if (!hasAv && Array.isArray(state.generation_clips)) return Math.max(0,...state.generation_clips.map(clipEnd));
    if (state.duration != null) return Number(state.duration);
    return Math.max(0, ...[...state.video_tracks, ...state.audio_tracks].flat().map(clipEnd));
}
/** 分段区间：clips 模式按素材片段计段（不含空隙）；markers/纯参考按 split_points 连续切。 */
export function segmentIntervals(state) {
    let total = timelineDuration(state);
    if (!(total > 0)) return [];
    const hasAv = [...state.video_tracks,...state.audio_tracks].some(lane=>lane.length);
    const videos = activeClips(state, "video_tracks");
    const audios = activeClips(state, "audio_tracks");
    if (hasAv) {
        if (!videos.length && !audios.length) return [];
        if (Object.values(state.disabled_tracks || {}).some(flags=>flags.some(Boolean)))
            total = Math.min(total,Math.max(...[...videos,...audios].map(clipEnd)));
    }
    if (!videos.length && !audios.length && Array.isArray(state.generation_clips))
        return state.generation_clips.map(clip=>[clip.tl_start,clipEnd(clip)]).sort((a,b)=>a[0]-b[0]);
    if (state.segmentation === "clips" && (videos.length || audios.length)) {
        const clips = videos.length ? videos : audios;
        const out = [];
        const points = [...new Set(clips.flatMap(clip => [clip.tl_start, clipEnd(clip)]).map(t => Math.max(0, Math.min(total, Math.round(t * FPS) / FPS))))].sort((a,b) => a-b);
        let previousOwner = -1;
        for (let i = 0; i < points.length - 1; i++) {
            const a = points[i], b = points[i + 1];
            if (b - a <= HALF_FRAME) continue;
            const owner = clips.findIndex(clip => clip.tl_start <= (a+b)/2 && (a+b)/2 < clipEnd(clip));
            if (owner < 0) { previousOwner = -1; continue; }
            if (videos.length && out.length && owner === previousOwner && Math.abs(out.at(-1)[1]-a) < HALF_FRAME) out.at(-1)[1] = b;
            else out.push([a,b]);
            previousOwner = owner;
        }
        return out;
    }
    const points = [...new Set((state.split_points || []).map(t => Math.round(t * FPS) / FPS)
        .filter(t => t > HALF_FRAME && t < total - HALF_FRAME))].sort((a, b) => a - b);
    // 素材路径优先：只按优先图片数量分轮
    const priorityImages = (state.sources || []).filter(source => source.priority);
    if (priorityImages.length) {
        const frames = Math.max(5, Math.round(Number(state.segment_recipe?.frames) || 243));
        const step = frames / FPS;
        return priorityImages.map((_, index) => [step * index, step * (index + 1)]);
    }
    // 未分段：整条素材作为 1 段（用户可自行调整单段上限或分段）
    if (!points.length) return [[0, total]];
    const bounds = [0, ...points, total];
    return bounds.slice(0, -1).map((start, i) => [start, bounds[i + 1]]);
}
/** 兼容旧调用：连续点列。clips 有空隙时仍含空隙端点，计段请用 segmentIntervals。 */
export function segmentBounds(state) {
    const intervals = segmentIntervals(state);
    if (!intervals.length) return [];
    const videos = state.video_tracks.flat();
    const audios = state.audio_tracks.flat();
    // clips 模式且有素材：返回 [s0,e0,s1,e1,…]，成对取用；计段用 segmentIntervals().length
    if (state.segmentation === "clips" && (videos.length || audios.length))
        return intervals.flat();
    return [intervals[0][0], ...intervals.map(pair => pair[1])];
}
export function cutTimelineAt(state, points) {
    const total = timelineDuration(state);
    const cuts = [...new Set(points.map(t => Math.round(t * FPS) / FPS))]
        .filter(t => t > HALF_FRAME && t < total - HALF_FRAME).sort((a, b) => a - b);
    const hasClips = [...state.video_tracks, ...state.audio_tracks].some(lane => lane.length);
    if (!hasClips && Array.isArray(state.generation_clips)) {
        state.generation_clips=state.generation_clips.flatMap(clip=>{
            const bounds=[clip.tl_start,...cuts.filter(t=>t>clip.tl_start+HALF_FRAME&&t<clipEnd(clip)-HALF_FRAME),clipEnd(clip)];
            return bounds.slice(1).map((end,i)=>({...clip,id:i?`${clip.id}:f${Math.round(bounds[i]*FPS)}`:clip.id,
                tl_start:bounds[i],src_in:0,src_out:end-bounds[i]}));
        });
    }
    for (const key of ["video_tracks", "audio_tracks"]) state[key] = state[key].map(lane => {
        const joined = [];
        for (const clip of [...lane].sort((a, b) => a.tl_start - b.tl_start)) {
            const previous = joined.at(-1);
            if (previous && previous.source === clip.source && Math.abs(clipEnd(previous) - clip.tl_start) < 1e-7 && Math.abs(previous.src_out - clip.src_in) < 1e-7)
                previous.src_out = clip.src_out;
            else joined.push({...clip});
        }
        return joined.flatMap(clip => {
        const bounds = [clip.tl_start, ...cuts.filter(t => t > clip.tl_start + HALF_FRAME && t < clipEnd(clip) - HALF_FRAME), clipEnd(clip)];
        return bounds.slice(1).map((end, index) => ({...clip,
            id: index ? `${clip.id}:f${Math.round(bounds[index] * FPS)}` : clip.id,
            tl_start: bounds[index], src_in: clip.src_in + bounds[index] - clip.tl_start,
            src_out: clip.src_in + end - clip.tl_start}));
        });
    });
    // Image-only generation retains timing ranges without drawing marker lines.
    state.split_points = hasClips ? [] : cuts;
    state.segmentation = "clips";
    return state;
}
/** 可见轨道的片段；selection省略时返回全部启用片段，传入空Set时返回空。 */
export function activeClips(state, key = null, selection = null) {
    return (key ? [key] : ["video_tracks","audio_tracks"]).flatMap(kind =>
        (state[kind] || []).flatMap((lane,index)=>trackDisabled(state,kind,index) ? [] :
            lane.filter(clip=>selection === null || selection.has(clip.id))));
}
/** 开关过滤播放、输出与分段推导，编辑布局保留。 */
export function trackDisabled(state, key, index) {
    return !!state.disabled_tracks?.[key]?.[index];
}
export function topVideoAt(state, time) {
    const t = Math.min(time, Math.max(0, timelineDuration(state) - 1e-7));
    for (const [index, lane] of state.video_tracks.entries()) {
        if (trackDisabled(state, "video_tracks", index)) continue;
        const clip = lane.find(clip => t >= clip.tl_start - 1e-8 && t < clipEnd(clip));
        if (clip) return clip;
    }
    return null;
}

export function framePlan(state, settings = {}) {
    const intervals = segmentIntervals(state);
    return intervals.map(([start, end], index) => {
        const target = Math.floor((end - start) * FPS + .5);
        const previous = index ? Math.floor((intervals[index - 1][1] - intervals[index - 1][0]) * FPS + .5) : 0;
        const reanchor = index && settings.reanchor_every && index % settings.reanchor_every === 0;
        const request = Math.max(5, target <= 5 ? 5 : 5 + 17 * Math.ceil((target - 5) / 17));
        return {target, request, overlap:0, guard:0, valid:target >= 1 && request <= (settings.max_frames ?? 362)};
    });
}
