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
    return {...previous,...incoming,file:previous.file,origin:previous.origin||incoming.origin,
        serial:Number(previous.serial)||Number(incoming.serial)||0,
        redraw:!!(previous.redraw||incoming.redraw),redraw_base:Number(previous.redraw_base)||Number(incoming.redraw_base)||0,
        redraw_count:Number.isFinite(count)?count:0,
        result_aliases:[...new Set([...resultAliases(previous),...resultAliases(incoming)])]};
}
export function redrawKey(kind,record,base) {
    return `${kind}:${Number(record.round_no)||0}:${Number(base)||Number(record.redraw_base)||Number(record.segment_index)||0}:${record.reset_id||"legacy"}`;
}
export function identityFields(record) {
    return Object.fromEntries(["version_id","trial_request","trial_session","trial_file","preview_origin","preview_file","result_aliases","reset_id","official_redraw_count"]
        .filter(key=>record[key]!=null).map(key=>[key,record[key]]));
}
