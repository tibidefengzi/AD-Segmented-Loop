import {app} from "../../scripts/app.js";
import {currentTaskId} from "./workflow_task.js";
import {api} from "../../scripts/api.js";
import {executeQueueOnce,interruptOwnedPrompt} from "./queue_runner.js";
import {bindWidgetWidth} from "./dom_layout.js";
import {previewSources,finalSubmission,finalRecords} from "./final_queue.js";

const style = document.createElement("style");
style.textContent = `
.ad-final{box-sizing:border-box;width:100%;height:auto!important;min-height:0!important;max-height:none!important;flex:0 0 auto!important;align-self:start;min-width:0;padding:12px;background:transparent;border:1px solid #43536b;color:var(--ad-text,#e6e9ef);font:13px/1.5 var(--ad-font,"Microsoft YaHei",sans-serif);border-radius:12px}
.ad-final *{box-sizing:border-box}.ad-final button{font:inherit;cursor:pointer;border:1px solid var(--ad-accent-border,#4d7ed8);border-radius:var(--ad-radius-sm,5px);background:var(--ad-accent,#3769c7);color:#fff;padding:5px 12px;min-height:30px}.ad-final button:hover{background:var(--ad-accent-hover,#2f5cb2)}.ad-final button:disabled{opacity:.45;cursor:default}
.ad-final-tools,.ad-final-tabs{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0}.ad-final-tabs{gap:6px}.ad-final-tabs button{background:var(--ad-surface-deep,#1e1e1e);border-color:var(--ad-border,#454545);color:var(--ad-text-dim,#aab3c2);border-radius:var(--ad-radius-sm,5px)}.ad-final-tabs button.on{background:var(--ad-accent,#3769c7);border-color:var(--ad-accent-border,#4d7ed8);color:#fff}
.ad-final-screen{width:100%;aspect-ratio:16/9;background:var(--ad-screen,#0d0d0d);position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;border:1px solid var(--ad-border-soft,#3a3a3a);border-radius:var(--ad-radius-sm,5px)}.ad-final-screen img,.ad-final-screen video{width:100%;height:100%;object-fit:contain}.ad-final-screen audio{width:90%}.ad-final-screen [hidden]{display:none}
.ad-final-status{min-height:24px;overflow-wrap:anywhere;color:var(--ad-warn,#e8c26a);font-size:15px;font-weight:700;letter-spacing:.2px}.ad-final-status.error{color:var(--ad-danger-text,#ffb4b9)}.ad-final-row{display:flex;gap:8px;align-items:flex-start;width:100%;border:1px solid var(--ad-border-soft,#3a3a3a);border-radius:8px;background:var(--ad-surface,#252525);padding:4px 8px;min-height:104px;margin-top:6px}.ad-final-row>span{flex:0 0 72px;color:var(--ad-text-dim,#aab3c2);font-size:13px;padding-top:6px}.ad-final-list{display:flex;flex-wrap:wrap;gap:6px;max-height:340px;overflow:auto;flex:1;min-height:96px;padding:2px 0}.ad-final-list>span{color:var(--ad-text-mute,#8b95a3);font-size:12px;padding:6px}.ad-final-card{position:relative;padding:0!important;background:var(--ad-surface-deep,#1e1e1e)!important;flex:0 0 160px;width:160px;min-height:96px;overflow:hidden;border-color:var(--ad-border,#454545)!important;border-radius:7px}.ad-final-card img,.ad-final-card .audio{width:158px;height:89px;object-fit:contain;background:var(--ad-screen,#0d0d0d);display:block}.ad-final-card .audio{font-size:40px;line-height:89px}.ad-final-card span:last-child{display:block;font-size:12px;overflow-wrap:anywhere;padding:2px 4px}.ad-final-card .remove{position:absolute;right:3px;top:2px;background:#151b24bb;border-radius:3px;padding:0 5px;color:#fff}.ad-final-card.selected{outline:2px solid var(--ad-accent-soft,#92b7ff)}.ad-final-title{margin-top:8px;color:var(--ad-text-dim,#aab3c2)}.ad-h3-media-card.merge-selected{outline:2px solid var(--ad-accent-soft,#92b7ff)}
`;
document.head.append(style);
// 媒体地址统一走 ComfyUI 的 api.apiURL，origin/file 逐段编码（部署在子路径时也能正确访问）。
const mediaUrl = record => api.apiURL(`/aiden_h3/media/${encodeURIComponent(record.origin || "media")}/${String(record.file).split("/").map(encodeURIComponent).join("/")}`);
const thumbnailUrl = record => api.apiURL(`/aiden_h3/thumbnail/${encodeURIComponent(record.origin || "media")}/${String(record.file).split("/").map(encodeURIComponent).join("/")}`);
const label = record => record.label || record.file?.split("/").at(-1) || "成果";
const getWidget = (node,name) => node.widgets?.find(widget => widget.name === name);
async function request(path,body) {
    const response = await api.fetchApi(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) {const error = new Error(data.error || "操作失败");error.code=data.code;throw error;}
    return data;
}

export function mountFinalSave(node) {
    if (node._adFinal) return;
    // 与三个主节点统一外观：节点底色/边框/标签色和 AD-分段预览 保持一致。
    node.color="#414957";node.bgcolor="#2b303b";node.boxcolor="#6b8ec6";
    node.constructor.title_text_color="#eef3fa";
    const root=document.createElement("div"); root.className="ad-final";
    root.innerHTML=`<div class="ad-final-screen"><img hidden><video hidden controls playsinline preload="metadata"></video><audio hidden controls preload="metadata"></audio><span data-idle>在来源预览中选择成果，再点加工所选</span></div>
<div class="ad-final-tools"><button data-start>加工所选</button><button data-stop>停止</button><button data-resume>继续剩余</button><button data-merge>合并片段</button></div>
<div class="ad-final-status" aria-live="polite"></div><div class="ad-final-tabs"><button data-kind="image">图片</button><button data-kind="audio">音频</button><button data-kind="video">视频</button></div>
<div class="ad-final-row"><span>最终成果</span><div class="ad-final-list" data-results></div></div><div class="ad-final-row"><span>合并成果</span><div class="ad-final-list" data-merges></div></div>`;
    const $=selector=>root.querySelector(selector);
    const hidden=getWidget(node,"source_record");
    if (hidden) {hidden.type="hidden";hidden.computeSize=()=>[0,-4];}
    if (getWidget(node,"帧序列帧率")) getWidget(node,"帧序列帧率").options.tooltip="仅用于直接输入的视频帧序列；0使用所选视频原帧率。VIDEO输入始终采用自身帧率。";
    for (const input of node.inputs || []) input.label = input.name === "media" ? "处理后媒体（图片/音频/视频）" : input.name === "audio" ? "帧序列配套音频" : input.label;
    node.properties ||= {};
    let records=node.properties.ad_final_records || [],kind=node.properties.ad_final_tab || "video",selected=new Map(),busy=false,preparing=false,stopped=false,disposed=false;
    let batch=node.properties.ad_final_batch || null,active=null,owner={};
    // 仅当渲染数据真正变化时才保存并标脏工作流，避免每次 render 都把工作流标记为已修改。
    let renderedStateKey="";
    const finalCards=new Map(),pages=new Map();
    const PAGE_SIZE=200;
    const video=$("video"),image=$("img"),audio=$("audio");video.muted=true;audio.muted=true;
    const dirty=()=>{node.graph?.change?.();node.setDirtyCanvas?.(true,true);};
    const saveState=()=>{node.properties.ad_final_records=records;node.properties.ad_final_batch=batch;node.properties.ad_final_tab=kind;dirty();};
    const say=(text,error=false)=>{const box=$(".ad-final-status");box.textContent=text;box.classList.toggle("error",error);};
    // 进度行后面附带已选预览的信息（一直高亮显示，不自动收起）。
    const selectionSummary=()=>{
        const chosen=batch?.plan?.records||[];
        if(!chosen.length)return "";
        const text=chosen.map(record=>`${record.source_record?.role?record.source_record.role+" · ":""}${label(record)}`).join("、");
        return `（已选 ${chosen.length} 条：${text}）`;
    };
    const buttons=()=>{$("[data-start]").disabled=busy||preparing;$("[data-stop]").disabled=!busy;$("[data-resume]").disabled=busy||preparing||!batch||batch.cursor>=batch.plan.records.length;$("[data-merge]").disabled=busy||preparing;};
    const show=record=>{
        active=record;video.pause();audio.pause();
        for(const media of [video,audio,image])media.hidden=true;
        $("[data-idle]").hidden=true;
        const media=record.kind==="video"?video:record.kind==="audio"?audio:image;
        media.hidden=false;media.src=mediaUrl(record);
        say(`${label(record)}${record.width?` · ${record.width}×${record.height} · ${record.fps} FPS`:""}`);
    };
    const key=record=>record.final_id || record.file;
    const paintSelection=()=>{for(const card of root.querySelectorAll(".ad-final-card"))card.classList.toggle("selected",selected.has(card.dataset.key));};
    const render=()=>{
        for(const button of root.querySelectorAll("[data-kind]"))button.classList.toggle("on",button.dataset.kind===kind);
        for(const [selector,merged] of [["[data-results]",false],["[data-merges]",true]]){
            const target=$(selector),all=records.filter(record=>record.kind===kind&&!!record.merged===merged),pageKey=`${kind}:${merged}`;
            const page=Math.min(pages.get(pageKey)||0,Math.max(0,Math.ceil(all.length/PAGE_SIZE)-1));pages.set(pageKey,page);
            const visible=all.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE),wanted=new Set(visible.map(key));
            for(const child of [...target.children])if(!wanted.has(child.dataset.key))child.remove();
            for(const [index,record] of visible.entries()){
                let card=finalCards.get(key(record));
                if(!card){
                    card=document.createElement("button");card.className="ad-final-card";card.dataset.key=key(record);
                    if(record.kind==="audio"){const icon=document.createElement("span");icon.className="audio";icon.textContent="♪";card.append(icon);}
                    else{const thumb=document.createElement("img");thumb.loading="lazy";thumb.decoding="async";thumb.src=thumbnailUrl(record);thumb.alt="";card.append(thumb);}
                    const remove=document.createElement("span");remove.className="remove";remove.textContent="×";remove.title="移除这条最终预览记录（文件保留）";
                    remove.onclick=event=>{event.stopPropagation();records=records.filter(item=>key(item)!==key(record));selected.delete(key(record));finalCards.delete(key(record));if(active&&key(active)===key(record)){video.pause();audio.pause();for(const media of [video,audio,image])media.hidden=true;$("[data-idle]").hidden=false;active=null;}render();};card.append(remove);
                    const text=document.createElement("span");text.textContent=`${record.source_record?.role?record.source_record.role+" · ":""}${label(record)}`;card.append(text);card.title=record.archive || label(record);
                    card.onclick=event=>{if(event.ctrlKey||event.metaKey){if(selected.has(key(record)))selected.delete(key(record));else selected.set(key(record),record);}else{selected.clear();selected.set(key(record),record);show(record);}paintSelection();};finalCards.set(key(record),card);
                }
                if(target.children[index]!==card)target.insertBefore(card,target.children[index]||null);
            }
            if(!visible.length){const empty=document.createElement("span");empty.textContent=merged?"暂无合并成果":"暂无最终成果";target.append(empty);}
            if(all.length>PAGE_SIZE){
                const pager=document.createElement("div");pager.className="ad-final-pager";pager.style.cssText="display:flex;align-items:center;gap:8px;flex-basis:100%;padding:4px 0";
                const previous=document.createElement("button"),next=document.createElement("button"),text=document.createElement("span");
                previous.textContent="上一页";next.textContent="下一页";previous.disabled=page===0;next.disabled=(page+1)*PAGE_SIZE>=all.length;
                text.textContent=`第 ${page+1}/${Math.ceil(all.length/PAGE_SIZE)} 页 · ${all.length} 条`;
                previous.onclick=()=>{pages.set(pageKey,page-1);render();};next.onclick=()=>{pages.set(pageKey,page+1);render();};pager.append(previous,text,next);target.append(pager);
            }
        }
        // 仅缓存本页卡片；完整成果与跨页选择仍保存在数据里。
        for(const [id,card] of finalCards)if(!root.contains(card))finalCards.delete(id);
        paintSelection();buttons();
        // 数据（记录/批次游标/分类）真正变化时才写属性并标脏工作流。
        const nextStateKey=JSON.stringify([kind,records.map(key),batch?.cursor??-1,batch?.status||""]);
        if(nextStateKey!==renderedStateKey){renderedStateKey=nextStateKey;saveState();}
    };
    const followPage=record=>{
        const list=records.filter(item=>item.kind===record.kind&&!!item.merged===!!record.merged);
        pages.set(`${record.kind}:${!!record.merged}`,Math.floor(Math.max(0,list.findIndex(item=>key(item)===key(record)))/PAGE_SIZE));
    };
    const accept=(job,token)=>{
        const result=finalRecords(job,batch.plan.final_id,token);
        for(const record of result)if(!records.some(item=>key(item)===key(record)))records.push(record);
        kind=result[0].kind;followPage(result[0]);show(result[0]);batch.cursor++;batch.pending=null;batch.status="ready";render();
    };
    const recover=async()=>{
        const pending=batch?.pending;if(!pending?.promptId)return false;
        const deadline=Date.now()+60000;
        while(!stopped&&!disposed){
            const response=await api.fetchApi('/history/'+encodeURIComponent(pending.promptId));if(!response.ok)throw Error('无法核对上次加工任务');
            const history=await response.json(),job=history[pending.promptId];
            if(job?.status?.status_str==="success"&&job.status.completed){accept(job,pending.token);return true;}
            if(job?.status?.status_str==="error"){batch.pending=null;saveState();return false;}
            const queueResponse=await api.fetchApi('/queue');if(!queueResponse.ok)throw Error('无法核对上次任务是否仍在执行');
            const queue=await queueResponse.json();
            const queued=[...(queue.queue_running||[]),...(queue.queue_pending||[])].some(row=>String(row[1])===pending.promptId);
            if(!queued){batch.pending=null;saveState();return false;}
            if(Date.now()>deadline)throw Error('上次加工任务尚未结束，请稍后点击继续剩余');
            say('等待上次加工任务结束后继续…');
            await new Promise(resolve=>setTimeout(resolve,400));
        }
        return false;
    };
    const run=async()=>{
        if(busy||!batch)return;busy=true;stopped=false;buttons();
        try{
            await recover();
            while(!stopped&&!disposed&&batch.cursor<batch.plan.records.length){
                const record=batch.plan.records[batch.cursor],token=crypto.randomUUID();
                batch.status="running";batch.pending={index:batch.cursor,token,promptId:null};saveState();
                say(`正在加工 ${label(record)}${selectionSummary()}`);
                owner={};Object.defineProperty(owner,"_adH3PromptId",{configurable:true,get:()=>batch?.pending?.promptId,set:id=>{batch.pending.promptId=id;saveState();}});
                const result=await executeQueueOnce(app,api,batch.plan.final_id,{serialized:finalSubmission(batch.plan,batch.cursor,token),owner,isStopped:()=>stopped||disposed});
                if(result.stopped)break;
                accept(result.job,token);
            }
            batch.status=batch.cursor>=batch.plan.records.length?"completed":"paused";
            if(!disposed)say((batch.status==="completed"?"所选成果已逐个加工并保存。":`已停止，保留已保存成果。`)+selectionSummary());
        }catch(error){if(batch)batch.status=stopped?"paused":"error";if(!disposed)say(stopped?"已停止，保留已保存成果。":error.message,!stopped);}
        finally{busy=false;buttons();saveState();}
    };
    $("[data-start]").onclick=async()=>{
        if(busy||preparing)return;
        preparing=true;buttons();
        try{
            const serialized=await app.graphToPrompt();
            const sources=previewSources(serialized.output,node.id);
            if(sources.length!==1)throw Error('请将一个分段预览的媒体输出接入处理链，再连接到最终保存');
            const preview=node.graph?.getNodeById?.(Number(sources[0])) || node.graph?.getNodeById?.(sources[0]);
            const chosen=preview?._adH3Preview?.processingSelection?.() || [];
            if(!chosen.length)throw Error('请先在来源预览中单击或Ctrl选择成果');
            const plan=await request('/aiden_h3/final/prepare',{prompt:serialized.output,final_id:String(node.id),records:chosen});
            plan.task_id=currentTaskId(node.graph);
            batch={plan,cursor:0,pending:null,status:"ready"};saveState();void run();
        }catch(error){say(error.message,true);}
        finally{preparing=false;buttons();}
    };
    $("[data-resume]").onclick=async()=>{
        if(busy||preparing||!batch)return;
        preparing=true;buttons();
        try{
            // 文件清单不变；重试采用用户当前修好的处理分支，不强迫使用失败时的参数。
            if(node.graph!==app.graph)throw Error('请回到该最终保存节点所在工作流再继续');
            const serialized=await app.graphToPrompt();
            const remaining=batch.plan.records.slice(batch.cursor);
            if(!remaining.length)return;
            const updated=await request('/aiden_h3/final/prepare',{prompt:serialized.output,final_id:String(node.id),records:remaining});
            updated.task_id=currentTaskId(node.graph);
            if(updated.source_id!==batch.plan.source_id)throw Error('来源预览已经改变，请重新选择成果并点击加工所选');
            batch.plan={...updated,records:[...batch.plan.records.slice(0,batch.cursor),...updated.records]};saveState();void run();
        }catch(error){say(error.message,true);}
        finally{preparing=false;buttons();}
    };
    $("[data-stop]").onclick=()=>{stopped=true;say('正在停止本次加工…');void interruptOwnedPrompt(api,owner).catch(error=>say(error.message,true));};
    for(const button of root.querySelectorAll('[data-kind]'))button.onclick=()=>{video.pause();audio.pause();kind=button.dataset.kind;selected.clear();for(const media of [video,audio,image])media.hidden=true;$("[data-idle]").hidden=false;render();};
    $("[data-merge]").onclick=async()=>{
        if(busy||preparing)return;
        const chosen=selected.size?[...selected.values()]:records.filter(record=>record.kind===kind&&!record.merged);
        if(chosen.some(record=>record.kind!==kind)){say('请在同一分类中选择合并成果',true);return;}
        const body={records:chosen,directory:getWidget(node,'保存目录')?.value||'',prefix:getWidget(node,'文件名前缀')?.value||'最终合并'};
        preparing=true;buttons();
        try{
            let result;
            try{result=await request('/aiden_h3/final/merge',body);}catch(error){if(error.code!=='spec_mismatch'||!window.confirm(error.message))throw error;result=await request('/aiden_h3/final/merge',{...body,normalize:true});}
            const record={...result.record,merged:true};records.push(record);selected.clear();followPage(record);render();show(record);
        }catch(error){say(error.message,true);}
        finally{preparing=false;buttons();}
    };
    const widget=node.addDOMWidget('ad_final_ui','custom',root,{serialize:false,hideOnZoom:false});
    widget.computeSize=()=>[Math.max(300,Number(node.size?.[0])||760),root.offsetHeight+12];bindWidgetWidth(node,widget);
    node.size=[Math.max(760,Number(node.size?.[0])||760),Number(node.size?.[1])||800];
    let resizeFrame=0;
    // 高度统一由 scheduleLayout 合并计算（ResizeObserver 只负责调度，不直接 setSize），避免多次回调反复改高度。
    const scheduleLayout=()=>{if(resizeFrame)return;resizeFrame=requestAnimationFrame(()=>{resizeFrame=0;if(disposed||!root.isConnected)return;const height=node.computeSize?.()[1];if(height&&Math.abs(node.size[1]-height)>2)node.setSize([node.size[0],height]);node.setDirtyCanvas?.(true,true);});};
    const observer=new ResizeObserver(scheduleLayout);observer.observe(root);
    const executed=node.onExecuted;node.onExecuted=function(message){const result=executed?.apply(this,arguments);const incoming=(message?.ad_final||[]).filter(r=>!r.source_record?.final_task_id||!this.properties.ad_task_id||r.source_record.final_task_id===this.properties.ad_task_id);for(const record of incoming)if(!records.some(item=>key(item)===key(record)))records.push(record);if(incoming.length){kind=incoming[0].kind;followPage(incoming[0]);render();show(incoming[0]);}return result;};
    const configured=node.onConfigure;node.onConfigure=function(){const result=configured?.apply(this,arguments);busy=false;preparing=false;finalCards.clear();pages.clear();records=this.properties.ad_final_records||[];batch=this.properties.ad_final_batch||null;kind=this.properties.ad_final_tab||'video';selected.clear();renderedStateKey=JSON.stringify([kind,records.map(key),batch?.cursor??-1,batch?.status||""]);render();return result;};
    const removed=node.onRemoved;node.onRemoved=function(){disposed=true;stopped=true;busy=false;preparing=false;observer.disconnect();if(resizeFrame)cancelAnimationFrame(resizeFrame);for(const media of [video,audio]){media.pause();if(media.hasAttribute("src")){media.removeAttribute("src");media.load();}}for(const image of root.querySelectorAll("img"))image.removeAttribute("src");finalCards.clear();root.remove();delete node._adFinal;return removed?.apply(this,arguments);};
    node._adFinal={root,run,selection:()=>[...selected.values()],get batch(){return batch;},resetTask(){records=[];batch=null;selected.clear();finalCards.clear();pages.clear();active=null;video.pause();audio.pause();for(const media of [video,audio,image])media.hidden=true;$("[data-idle]").hidden=false;render();}};
    render();if(batch&&batch.status!=="completed")say('上次加工记录已保留，可点击继续剩余。');
}

app.registerExtension({name:"AD.SegmentedLoop.FinalSave",nodeCreated(node){if((node.comfyClass||node.type)==="AD_FinalSave")mountFinalSave(node);}});
