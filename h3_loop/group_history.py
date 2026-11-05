"""按真实组保存/恢复执行进度；旧版仅从仍可精确定位的预览文件恢复。"""
from __future__ import annotations
import uuid
import copy,re
from .storage import atomic_json,contained

# 历史轮次回抽保留策略：默认只保留最近 10 轮可回抽（含当前轮）。
REROLL_RETAIN_ROUNDS = 10


def _load_snapshot(store,state,path):
    import json
    with contained(store.directory(state['id']),path).open(encoding='utf-8') as f:
        return json.load(f)


def rerollable_rounds(state):
    """最近 N 轮（含当前轮）可回抽，更早的轮次只读。"""
    current=int(state.get('round_no',1) or 1)
    numbers={current}
    for number in (state.get('group_snapshots') or {}):
        try:
            numbers.add(int(number))
        except (TypeError,ValueError):
            continue
    ordered=sorted(numbers,reverse=True)
    return set(ordered[:REROLL_RETAIN_ROUNDS])


def ensure_rerollable(state,number):
    if int(number) not in rerollable_rounds(state):
        raise ValueError(
            '该轮已超出回抽保留范围（默认只保留最近 %d 轮）：仍可预览/合并，但不能回抽。'
            '如需回抽，请在最近的轮次中操作。' % REROLL_RETAIN_ROUNDS)


def shared_relay_run_ids(store,state):
    """旧数据可能出现多个历史组（或历史组与当前轮）共用同一条潜桥链目录。

    只统计历史快照会漏掉“历史轮 == 当前轮链”的情况，回抽会读到被当前轮覆盖的 stage；
    因此这里把当前会话自己的 relay_run_id 也计入参与者。
    """
    counts={}
    for path in (state.get('group_snapshots') or {}).values():
        try:
            snapshot=_load_snapshot(store,state,path)
        except (FileNotFoundError,ValueError,OSError):
            continue
        relay=str(snapshot.get('relay_run_id') or '')
        if relay:
            counts[relay]=counts.get(relay,0)+1
    current=str(state.get('relay_run_id') or '')
    if current:
        counts[current]=counts.get(current,0)+1
    return {relay for relay,count in counts.items() if count>1}


def snapshot_path(state,number):
    return (state.get('group_snapshots') or {}).get(str(int(number)))


def save_snapshot(store,state,number,snapshot):
    path=snapshot_path(state,number)
    if not path:
        return False
    atomic_json(contained(store.directory(state['id']),path),snapshot)
    return True

def archive(store,state):
    number=int(state.get('round_no',1) or 1)
    snapshot=copy.deepcopy(state)
    for key in ('group_snapshots','group_seq','active','execution_round','resume_round','draft_trial'):
        snapshot.pop(key,None)
    name=f'groups/round_{number:06d}.json'
    atomic_json(contained(store.directory(state['id']),name),snapshot)
    state.setdefault('group_snapshots',{})[str(number)]=name
    state['group_seq']=max(int(state.get('group_seq',number)),number)

def version_match(sid,version,record):
    name=str(record.get('file') or '').replace('\\','/')
    named=record.get('session_id');scope=''
    if record.get('origin')=='session':
        scope,sep,name=name.partition('/')
        if not sep:return False
    file=str(version.get('file') or '').replace('\\','/')
    if scope in ('',sid) and named in (None,'',sid):
        return bool(name) and (file==name or file.rsplit('/',1)[-1].endswith('_'+name.rsplit('/',1)[-1]))
    child=scope if scope.startswith('trial_') else named
    return bool(child and str(child).startswith('trial_') and version.get('preview_only')
                and version.get('trial_session') in (None,child)
                and file=='trial_'+str(version.get('trial_request'))+'_'+name)

def legacy_group(state,number,records):
    """不把另一组的选用版本改号：只取该组仍在预览中的精确文件。"""
    candidate=copy.deepcopy(state)
    rows=candidate.get('segments') or []
    all_rows=[*(state.get('segments') or []),*(state.get('retired_segments') or [])]
    grouped={}
    for record in records:
        if int(record.get('round_no') or 0)!=number:continue
        found=[]
        for row_index,row in enumerate(all_rows):
            for version in row.get('versions') or []:
                if version_match(state['id'],version,record):
                    hit=re.search(r'(?:^|_)segment_(\d+)_',str(version.get('file') or ''))
                    index=int(version.get('segment_index') or (int(hit[1]) if hit else row_index+1))-1
                    found.append((index,row,version))
        unique={str(v['id']):(i,r,v) for i,r,v in found}
        if len(unique)!=1:continue
        index,row,version=next(iter(unique.values()))
        if not 0<=index<len(rows):continue
        group=grouped.setdefault(index,{'row':row,'versions':{}})
        group['versions'][version['id']]=copy.deepcopy(version)
    if not grouped:raise ValueError('该旧组缺少可核实的结果文件记录，未切换组或改动进度')
    for index,row in enumerate(rows):
        match=grouped.get(index)
        if match:
            row.update(entry=copy.deepcopy(match['row']['entry']),signature=match['row']['signature'],
                       versions=list(match['versions'].values()))
        else:row['versions']=[]
        row.update(selected=None,valid=False,accepted=False)
    candidate['retired_segments']=[]
    candidate['round_no']=number
    return candidate

def lookup(store,state,number,records):
    if number<=0:raise ValueError('旧版历史没有记录组号，不能猜测归组续跑')
    path=(state.get('group_snapshots') or {}).get(str(number))
    if path:
        import json
        with contained(store.directory(state['id']),path).open(encoding='utf-8') as f:candidate=json.load(f)
    else:candidate=legacy_group(state,number,records)
    if candidate.get('id')!=state['id'] or candidate.get('workflow_task_id') not in {None,state.get('workflow_task_id')}:
        raise ValueError('旧组不属于当前工作流，未切换进度')
    if state.get('workflow_task_id'):candidate['workflow_task_id']=state['workflow_task_id']
    return candidate

def restore(store,state,number,records):
    candidate=lookup(store,state,number,records)
    archive(store,state)
    if not candidate.get("relay_run_id"):
        candidate["relay_run_id"] = state.get("relay_run_id") or uuid.uuid4().hex
    candidate.update(group_snapshots=copy.deepcopy(state.get('group_snapshots') or {}),
                     group_seq=max(int(state.get('group_seq',1)),int(state.get('round_no',1)),number),
                     epoch=int(state.get('epoch',0))+1,active=None,auto=False,pause_after=False,status='ready',error='')
    for key in ('execution_round','resume_round','draft_trial','preview_hold','run_return','affected_run'):
        candidate.pop(key,None)
    return candidate
