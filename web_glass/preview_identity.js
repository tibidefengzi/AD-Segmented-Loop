// 文件路径是读取位置；成果身份同时接受后台版本、抽卡请求与文件别名。
export function resultAliases(record) {
    if(!record?.file)return [];
    const paths=new Set([`${record.origin||"session"}/${record.file}`,...(record.result_aliases||[])]);
    if(record.trial_session&&record.trial_file)paths.add(`session/${record.trial_session}/${record.trial_file}`);
    if(record.preview_origin&&record.preview_file)paths.add(`${record.preview_origin}/${record.preview_file}`);
    return [...paths].map(value=>String(value).replaceAll("\\","/"));
}
export function sameResult(left,right) {
    if(!left||!right||(left.role||"A")!==(right.role||"A")||(left.kind&&right.kind&&left.kind!==right.kind))return false;
    if(left.version_id&&right.version_id&&left.version_id===right.version_id)return true;
    if(left.trial_request&&right.trial_request&&left.trial_request===right.trial_request)return true;
    const aliases=new Set(resultAliases(left));return resultAliases(right).some(alias=>aliases.has(alias));
}
export function mergeResult(previous,incoming) {
    if(!previous)return {...incoming,result_aliases:resultAliases(incoming)};
    const count=Number(incoming.official_redraw_count)||Math.min(...[previous.redraw_count,incoming.redraw_count].map(Number).filter(n=>n>0));
    return {...previous,...incoming,file:incoming.file||previous.file,origin:incoming.origin||previous.origin,
        serial:Number(previous.serial)||Number(incoming.serial)||0,
        redraw:!!(previous.redraw||incoming.redraw),redraw_base:Number(previous.redraw_base)||Number(incoming.redraw_base)||0,
        redraw_count:Number.isFinite(count)?count:0,
        result_aliases:[...new Set([...resultAliases(previous),...resultAliases(incoming)])]};
}
// 抽卡编号作用域：媒体类型 + A/B 端 + 组号 + 段号。
// 不含后台 reset_id：「清空重置」后旧卡仍留在同组预览里，编号必须继续递增，
// 否则同一段会出现两个 R1-01-1（用户 2026-11-03 反馈）。
export function redrawBase(record) {
    return Number(record?.redraw_base) || Number(record?.segment_index) || Number(record?.serial) || 0;
}
export function redrawKey(kind,record,base) {
    const role = record?.role === "B" ? "B" : "A";
    return `${kind}:${role}:${Number(record?.round_no)||0}:${Number(base)||redrawBase(record)}`;
}
// 卡片编号：R{组}-{段}，抽卡结果追加「-第几次」；旧数据没有组号时用 # 前缀。
export function recordLabel(record,index=0) {
    const round = Number(record?.round_no) || 0;
    const redraw = !!record?.redraw && Number(record?.redraw_base) > 0;
    const segment = redraw ? Number(record.redraw_base)
        : (Number(record?.segment_index) || Number(record?.serial) || (Number(index) + 1));
    const base = String(segment).padStart(2, "0");
    const prefix = round > 0 ? `R${round}-` : "#";
    if(!redraw)return `${prefix}${base}`;
    const count = Number(record.redraw_count) > 0 ? Number(record.redraw_count) : 1;
    return `${prefix}${base}-${count}`;
}
// 下一个可用的「第几次」：跳过现存编号与已删除卡片留下的高水位，再和后台给的次数取较大值。
export function nextRedrawCount(record,base,records,official=0,highWater=0) {
    const role = record?.role === "B" ? "B" : "A";
    const round = Number(record?.round_no) || 0;
    const target = Number(base) || redrawBase(record);
    let next = Math.max(1,(Number(highWater)||0)+1);
    for(const entry of records||[]){
        if(!entry?.redraw)continue;
        if((entry.role==="B"?"B":"A")!==role||(Number(entry.round_no)||0)!==round)continue;
        if(redrawBase(entry)!==target)continue;
        next = Math.max(next,(Number(entry.redraw_count)||0)+1);
    }
    return Math.max(next,Number(official)||0);
}
// 载入/同步时归一化：已经唯一的编号保持原样（不重排旧卡），只有重复或缺失的
// 才顺延到该作用域最大编号之后，保证同一段不会再显示两个相同编号。
export function normalizeRedrawCounts(entries) {
    const scopes = new Map();
    for(const entry of entries||[]){
        const record = entry?.record;
        if(!record?.redraw)continue;
        const key = redrawKey(entry.kind,record,redrawBase(record));
        if(!scopes.has(key))scopes.set(key,[]);
        scopes.get(key).push(record);
    }
    const counts = new Map();
    let changed = false;
    for(const [key,records] of scopes){
        let highest = 0;
        for(const record of records)highest = Math.max(highest,Number(record.redraw_count)||0);
        const used = new Set();
        let next = highest + 1;
        for(const record of records){
            let count = Number(record.redraw_count) || 0;
            if(count > 0 && !used.has(count)){used.add(count);continue;}
            if(count > 0){
                while(used.has(next))next++;
                count = next++;
            }else{
                count = 1;
                while(used.has(count))count++;
            }
            used.add(count);
            record.redraw_count = count;
            changed = true;
        }
        counts.set(key,Math.max(0,...records.map(record=>Number(record.redraw_count)||0)));
    }
    return {changed,counts};
}
export function identityFields(record) {
    return Object.fromEntries(["version_id","trial_request","trial_session","trial_file","preview_origin","preview_file","result_aliases","reset_id","official_redraw_count"]
        .filter(key=>record[key]!=null).map(key=>[key,record[key]]));
}
