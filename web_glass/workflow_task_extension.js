import {app} from "../../scripts/app.js";
import {ensureWorkflowTask,stampWorkflowTask,stampSubmission} from "./workflow_task.js?v=20261102-group-v64";
let installed=false;
app.registerExtension({name:"AD.SegmentedLoop.WorkflowTasks",
    setup(){if(installed)return;installed=true;const original=app.graphToPrompt;app.graphToPrompt=async function(...args){const graph=this.graph,state=graph?.state;await ensureWorkflowTask(graph);if(this.graph!==graph||graph?.state!==state)throw Error('工作流已切换，本次未提交，请在目标工作流重新运行');const result=await original.apply(this,args);if(this.graph!==graph||graph?.state!==state)throw Error('工作流已切换，本次未提交，请在目标工作流重新运行');return stampSubmission(result,graph);};},
    afterLoadGraph(){const graph=app.graph,state=graph?.state;setTimeout(()=>{if(app.graph===graph&&graph?.state===state)void ensureWorkflowTask(graph).catch(error=>console.warn('AD任务隔离：'+error.message));},0);},
    nodeCreated(node){if(!['AidenH3AudioQueue','AidenH3Segment','AidenH3VideoPreview','AD_FinalSave'].includes(node.comfyClass||node.type))return;const original=node.onSerialize;node.onSerialize=function(){const result=original?.apply(this,arguments);if(this.graph===app.graph)stampWorkflowTask(this.graph);return result;};}
});
