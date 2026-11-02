"""工作流任务身份：新任务独立，旧进度只由一个明确任务继承。"""
from __future__ import annotations
import re
from .storage import STORE,identifier


def session_name(task_id,node_id):
    if not isinstance(task_id,str) or not re.fullmatch(r'[a-f0-9]{32}',task_id):
        raise ValueError('工作流任务标识无效')
    slot=re.sub(r'[^A-Za-z0-9_-]','_',str(node_id))[:40]
    return identifier(f'AD-Task-{task_id}-{slot}')


def bind_task(data):
    task=data.get('task_id');rows=[]
    for item in data.get('segments') or []:
        node=str(item['node_id']);fresh=session_name(task,node)
        candidate=identifier(item.get('session_id') or fresh) if item.get('legacy') else fresh
        forked=False
        with STORE.lock(candidate):
            try:state=STORE._load(candidate)
            except FileNotFoundError:state=None
            if state:
                owner=state.get('workflow_task_id')
                if owner and owner!=task:
                    candidate=fresh;forked=True
                elif owner!=task:
                    state['workflow_task_id']=task
                    STORE._save(state)  # 仅迁移身份，不清空/推进/重排原进度。
            elif item.get('legacy'):
                candidate=fresh  # 没有旧进度则使用独立命名空间，不占用共享节点编号。
        rows.append({'node_id':node,'session_id':candidate,'forked':forked})
    return {'task_id':task,'segments':rows}


def prompt_task(prompt,node_id,workflow=None):
    meta=(prompt.get(str(node_id)) or {}).get('_meta') or {}
    value=meta.get('ad_task_id') or ((workflow or {}).get('extra') or {}).get('ad_segmented_task',{}).get('id')
    return value if isinstance(value,str) and re.fullmatch(r'[a-f0-9]{32}',value) else None
