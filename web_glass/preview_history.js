export const HISTORY_GROUP_LIMIT = 10;
export const HISTORY_PAGE_SIZE = 200;
export const historyRound = record => Number(record?.round_no) || 0;
export function retainedHistoryRounds(records, currentRound, limit = HISTORY_GROUP_LIMIT) {
    const rounds = new Set(records.filter(record => record?.file).map(historyRound));
    if (Number(currentRound) > 0) rounds.add(Number(currentRound));
    return new Set([...rounds].sort((a,b) => b-a).slice(0,limit));
}
export function previewHistoryGroups(records, currentRound, expanded, pages, prefix, pageSize = HISTORY_PAGE_SIZE) {
    const grouped = new Map();
    for (const record of records) {
        const round = historyRound(record);
        if (!grouped.has(round)) grouped.set(round, []);
        grouped.get(round).push(record);
    }
    const current = Number(currentRound) || Math.max(0,...grouped.keys());
    const order = [...grouped.keys()].sort((a,b) => (a===current)-(b===current) || a-b);
    return order.map(round => {
        const all = grouped.get(round), key = `${prefix}:${round}`;
        const page = Math.max(0,Math.min(Number(pages.get(key))||0,Math.ceil(all.length/pageSize)-1));
        pages.set(key,page);
        const open = round===current || expanded.has(round);
        return {round,current:round===current,open,all,page,key,
            visible:open?all.slice(page*pageSize,(page+1)*pageSize):[]};
    });
}
