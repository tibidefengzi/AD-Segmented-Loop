// 整组选中片段共享时间/轨道偏移；快照与排序索引只建立一次。
const FPS=24, EPS=1e-7;
const end=clip=>clip.tl_start+clip.src_out-clip.src_in;
const ceil=value=>Math.ceil(value*FPS-EPS),floor=value=>Math.floor(value*FPS+EPS);
function upperBound(items,time){let lo=0,hi=items.length;while(lo<hi){const mid=(lo+hi)>>1;if(items[mid].tl_start<=time)lo=mid+1;else hi=mid;}return lo;}
export function createGroupMove(state,ids,{generation=false,limit=3}={}) {
    const selected=new Set(ids),members=[],indexes=new Map(),keys=generation?["generation_clips"]:["video_tracks","audio_tracks"];
    const disabled=state.disabled_tracks||{};
    for(const key of keys){
        const lanes=generation?[state.generation_clips||[]]:state[key]||[];
        lanes.forEach((lane,index)=>{
            const obstacles=[];
            for(const original of lane){const clip={...original};if(selected.has(clip.id))members.push({id:clip.id,key,lane:index,clip});else obstacles.push(clip);}
            obstacles.sort((a,b)=>a.tl_start-b.tl_start);let maximum=-Infinity;
            indexes.set(`${key}:${index}`,{items:obstacles,prefix:obstacles.map(clip=>maximum=Math.max(maximum,end(clip)))});
        });
    }
    const minimum=members.length?Math.min(...members.map(m=>m.clip.tl_start)):0;
    const maximum=members.length?Math.max(...members.map(m=>end(m.clip))):0;
    const neighbors=(key,lane,start,finish)=>{
        const index=indexes.get(`${key}:${lane}`)||{items:[],prefix:[]},before=upperBound(index.items,start+EPS);
        const previous=before?index.prefix[before-1]:0,next=before<index.items.length?index.items[before].tl_start:Infinity;
        return {previous,next,overlap:previous>start+EPS||next<finish-EPS};
    };
    function plan(delta,laneShift=0,previous={delta:0,laneShift:0}) {
        const invalid=reason=>({valid:false,reason});
        if(!members.length)return invalid("没有可移动的选中片段");
        if(!Number.isFinite(delta)||!Number.isInteger(laneShift))return invalid("移动位置无效");
        const rows=new Map();
        for(const member of members){
            const lane=member.lane+laneShift;
            if(lane<0||lane>=(generation?1:limit))return invalid("已到轨道边界，整组停在上一个合法位置");
            if(disabled[member.key]?.[lane])return invalid("目标轨道输出已关闭，请先开启该轨道");
            const key=`${member.key}:${lane}`;if(!rows.has(key))rows.set(key,[]);rows.get(key).push(member.clip);
        }
        for(const clips of rows.values()){
            clips.sort((a,b)=>a.tl_start-b.tl_start);
            if(clips.some((clip,i)=>i&&clip.tl_start<end(clips[i-1])-EPS))return invalid("所选片段本身重叠，请先移开重叠片段");
        }
        const wanted=Math.round(delta*FPS);let frames=Math.max(ceil(-minimum),wanted);
        if(laneShift===previous.laneShift){
            let lower=ceil(-minimum),upper=Infinity;
            for(const member of members){
                const clip=member.clip,n=neighbors(member.key,member.lane+laneShift,clip.tl_start+previous.delta,end(clip)+previous.delta);
                if(n.overlap)return invalid("当前位置存在重叠，请先移到空轨道");
                lower=Math.max(lower,ceil(n.previous-clip.tl_start));upper=Math.min(upper,floor(n.next-end(clip)));
            }
            if(lower>upper)return invalid("目标位置放不下整组选中片段");
            frames=Math.max(lower,Math.min(frames,upper));
        }
        frames=frames||0;
        const applied=frames/FPS;
        for(const member of members){const clip=member.clip;if(neighbors(member.key,member.lane+laneShift,clip.tl_start+applied,end(clip)+applied).overlap)return invalid("目标轨道有其它片段，整组停在上一个合法位置");}
        return {valid:true,delta:applied,deltaFrames:frames,laneShift,blocked:frames!==wanted,
            items:members.map(member=>({id:member.id,key:member.key,lane:member.lane+laneShift,start:member.clip.tl_start+applied}))};
    }
    function snap(delta,anchors,pxs,threshold=26){
        let picked=delta,distance=Infinity;
        for(const edge of [minimum,maximum])for(const anchor of anchors){
            if(!Number.isFinite(anchor))continue;
            const candidate=Math.round((anchor-edge)*FPS)/FPS,d=Math.abs(candidate-delta)*pxs;
            if(d<=threshold&&d<distance){distance=d;picked=candidate;}
        }
        return Math.round(picked*FPS)/FPS;
    }
    return {members,plan,snap};
}
