import {app} from "../../scripts/app.js";
import { injectHiddenRelay } from "./relay_hidden.js";
import {api} from "../../scripts/api.js";
import {loadCachedTask,rememberTask} from "./workflow_task_cache.js";

const runtime=new WeakMap();
let fallbackWarned=false;
const resultKeys=["ad_execution_round","ad_h3_media_records","ad_h3_strip_previews","ad_h3_merge_index","ad_h3_merged_previews","ad_h3_round_no","ad_h3_round_map","ad_h3_merge_serial","ad_h3_redraw_counts","ad_h3_hidden_strips","ad_h3_expanded_rounds","ad_h3_show_history","ad_final_records","ad_final_batch"];
const classOf=node=>node.comfyClass||node.type;
const relevant=node=>["AidenH3AudioQueue","AidenH3Segment","AidenH3VideoPreview","AD_FinalSave"].includes(classOf(node));
const cleanID=value=>String(value).replace(/[^A-Za-z0-9_-]/g,"_").slice(0,40);
const freshSession=(task,node)=>`AD-Task-${task.id}-${cleanID(node.id)}`;
function queueFor(graph,node){const input=node.inputs?.find(i=>i.name==="queue"),link=graph.links?.get?.(input?.link)||graph.links?.[input?.link];return link&&graph.getNodeById?.(link.origin_id);}
function queueValue(queue){const widget=queue?.widgets?.find(w=>w.name==="queue_state");try{return JSON.parse(widget?.getValue?.()??widget?.value??"{}");}catch{return {};}}
function legacySession(graph,node){return node.properties?.ad_execution_round?.session_id||node.widgets?.find(w=>w.name==="session_id")?.value||queueValue(queueFor(graph,node)).session_id||`AD-H3-${node.id}`;}
function resetNodeResults(node){
        if(!relevant(node))return;
        node.properties||={};for(const key of resultKeys)delete node.properties[key];
        if(classOf(node)==="AidenH3VideoPreview"){
            node.properties.ad_h3_media_records={image:[],audio:[],video:[]};node.properties.ad_h3_strip_previews={segment:[],merged:[]};node.properties.ad_h3_merge_index=[];node.properties.ad_h3_merged_previews=[];node.properties.ad_h3_hidden_strips=[];
            const legacy=node.widgets?.find(w=>w.name==="video_oasis_ui");if(legacy)legacy.value="{}";
            node._adH3Preview?.restoreSavedMedia?.();
        }
        if(classOf(node)==="AD_FinalSave")node._adFinal?.resetTask?.();
}
function resetResults(graph){for(const node of graph._nodes||[])resetNodeResults(node);}
export function currentTaskId(graph){return graph?.extra?.ad_segmented_task?.id||null;}
export function stampWorkflowTask(graph=app.graph){
    if(!graph||!(graph._nodes||[]).some(relevant))return null;
    graph.extra||={};
    if(app.configuringGraph)return graph.extra.ad_segmented_task||null;
    const state=graph.state||graph,previous=runtime.get(state),active=graph===app.graph?app.extensionManager?.workflow?.activeWorkflow:null;
    const path=active?.path||null,instance=active?.instanceId||null;
    const graphID=String(graph.id||"");let meta=graph.extra.ad_segmented_task;
    // 原生切页会先切 activeWorkflow 再序列化旧图，路径不能作为复制判据。
    // 原生复制/另存为均产生新 root graph.id；重开原图沿用原 UUID。
    const copied=!!meta&&meta.workflow_id!==graphID;
    if(!meta||copied){
        const migrating=!meta&&!!active?.isPersisted;
        const legacy={};
        if(migrating){
            for(const node of graph._nodes||[])if(classOf(node)==="AidenH3Segment")legacy[String(node.id)]=legacySession(graph,node);
        }
        // 未保存工作流的任务号缓存在浏览器本地：刷新/重开同一旧工作流时沿用原任务，
        // 不再因新任务号把旧会话 fork 掉并清空预览历史。
        const cached=migrating?loadCachedTask(graphID,path):null;
        meta={id:cached?.id||crypto.randomUUID().replaceAll("-",""),workflow_id:graphID,path,sessions:{},legacy_sessions:legacy};graph.extra.ad_segmented_task=meta;
        if(!migrating)resetResults(graph);
    }
    if(path)meta.path=path;
    rememberTask(meta,path);
    meta.sessions||={};meta.legacy_sessions||={};
    const assignedQueues=new Set();
    for(const node of graph._nodes||[]){
        if(!relevant(node))continue;node.properties||={};if(node.properties.ad_task_id&&node.properties.ad_task_id!==meta.id)resetNodeResults(node);node.properties.ad_task_id=meta.id;
        if(node.properties.ad_execution_round?.session_id&&node.properties.ad_execution_round.session_id!==meta.sessions[String(node.id)]&&classOf(node)==="AidenH3Segment")delete node.properties.ad_execution_round;
        if(classOf(node)!=="AidenH3Segment")continue;
        const id=String(node.id),sid=meta.sessions[id]||meta.legacy_sessions[id]||freshSession(meta,node);meta.sessions[id]=sid;node.properties.ad_task_session=sid;
        const queue=queueFor(graph,node);
        if(queue&&!assignedQueues.has(queue)){
            assignedQueues.add(queue);queue._adWorkflowSession=sid;queue._adWorkflowTask=meta.id;queue._adH3AssignTask?.(sid,meta.id);
        }
    }
    runtime.set(state,{...previous,instance});return meta;
}
export async function ensureWorkflowTask(graph=app.graph){
    if(app.configuringGraph)throw Error('工作流仍在加载，请加载完成后再运行');
    const meta=stampWorkflowTask(graph);if(!meta)return null;
    const segments=(graph._nodes||[]).filter(n=>classOf(n)==="AidenH3Segment").map(node=>({node_id:String(node.id),session_id:meta.sessions[String(node.id)],legacy:!!meta.legacy_sessions[String(node.id)]}));
    if(!segments.length)return meta;
    const state=graph.state||graph,existing=runtime.get(state)||{},key=JSON.stringify([meta.id,segments]);
    if(existing.key===key&&existing.promise){await existing.promise;return meta;}
    const promise=(async()=>{
        const response=await api.fetchApi('/aiden_h3/task/bind',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:meta.id,segments})});
        let data=null;try{data=await response.json();}catch{}
        if(!response.ok||!data||!Array.isArray(data.segments)){
            // 旧版后台还没有 /aiden_h3/task/bind：按旧会话命名继续排队，不阻断整个工作流。
            // （常见于“先更新文件、刷新页面，稍后才重启 ComfyUI”的升级顺序。）
            if(response.status===404||!data){
                if(!fallbackWarned){
                    fallbackWarned=true;
                    console.warn('AD任务隔离：后台插件版本较旧，本次按旧会话方式运行；安全重启 ComfyUI 后任务隔离生效。');
                }
                for(const item of segments)meta.sessions[item.node_id]=meta.legacy_sessions[item.node_id]||`AD-H3-${item.node_id}`;
                stampWorkflowTask(graph);
                return;
            }
            throw Error(data.error||'任务隔离接口返回异常；请保存工作流后安全重启 ComfyUI 重试。');
        }
        if(currentTaskId(graph)!==meta.id)return;
        if(data.segments.some(item=>item.forked))resetResults(graph);
        for(const item of data.segments){meta.sessions[item.node_id]=item.session_id;if(item.session_id!==meta.legacy_sessions[item.node_id])delete meta.legacy_sessions[item.node_id];}
        stampWorkflowTask(graph);
    })();runtime.set(state,{...existing,key,promise});
    try{await promise;}catch(error){runtime.delete(state);throw error;}return meta;
}
export function stampSubmission(serialized,graph=app.graph){
    const meta=graph?.extra?.ad_segmented_task;if(!meta)return serialized;
    for(const [id,node] of Object.entries(serialized.output||{})){
        node._meta||={};node._meta.ad_task_id=meta.id;
        if(node.class_type==="AidenH3Segment"&&meta.sessions[id])node.inputs.session_id=meta.sessions[id];
        if(node.class_type==="AD_FinalSave"){
            let source={};try{source=JSON.parse(node.inputs.source_record||'{}');}catch{}
            node.inputs.source_record=JSON.stringify({...source,final_task_id:meta.id});
        }
    }
    serialized.workflow||={};serialized.workflow.extra||={};serialized.workflow.extra.ad_segmented_task=structuredClone(meta);
    // 注入失败必须阻断本次提交：静默跳过后后台仍会按潜桥规则改变生成长度，
    // 会得到“没有桥/没有裁头”的错位成片，比报错更难排查。
    injectHiddenRelay(serialized, graph);
    return serialized;
}
