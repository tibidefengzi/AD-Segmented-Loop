import {segmentIntervals, clipEnd} from "./timeline_state.js?v=20261028-image-clips";
const FPS = 24;
const snap = value => Math.round(value * FPS) / FPS;

export function generationClips(state) {
    if (!Array.isArray(state.generation_clips)) {
        state.generation_clips = segmentIntervals(state).map(([start,end]) =>
            ({id:`g-${Math.round(start*FPS)}-${Math.round(end*FPS)}`,tl_start:start,src_in:0,src_out:end-start}));
    }
    return state.generation_clips;
}

export function commitGeneration(state, clips) {
    state.generation_clips = clips.sort((a,b) => a.tl_start-b.tl_start);
    state.duration = Math.max(0,...clips.map(clipEnd));
    state.split_points = [];
    state.segmentation = "clips";
    if (state.segment_recipe) state.segment_recipe.custom = true;
}

// 所有片段保留独立长度；碰撞只顺延后块，不挤短任何其它片段。
export function changeGeneration(state, id, start, end) {
    const clips = generationClips(state).map(clip => ({...clip}));
    const index = clips.findIndex(clip => clip.id===id);
    if (index<0) return;
    const minimum = index ? clipEnd(clips[index-1]) : 0;
    const length = Math.max(1/FPS,snap(end-start));
    clips[index].tl_start = Math.max(minimum,snap(start));
    clips[index].src_in = 0;
    clips[index].src_out = length;
    for (let i=index+1;i<clips.length;i++)
        clips[i].tl_start = Math.max(clips[i].tl_start,clipEnd(clips[i-1]));
    commitGeneration(state,clips);
}

export function deleteGeneration(state, ids) {
    commitGeneration(state,generationClips(state).filter(clip=>!ids.has(clip.id)));
}
