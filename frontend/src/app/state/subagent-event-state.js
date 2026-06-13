function setSubagentCardEventCount(agentId, count) {
    var aid = String(agentId || '');
    var n = Number(count);
    if (!aid || !Number.isFinite(n)) return;
    n = Math.max(0, n);
    if (currentSessionId) subagentStore.setEventCount(currentSessionId, aid, n);
}

function bumpSubagentCardEventCount(agentId, eventIndex, increment) {
    var aid = String(agentId || '');
    if (!aid) return;
    var prev = currentSessionId ? subagentStore.getEventCount(currentSessionId, aid) : 0;
    if (typeof eventIndex === 'number' && eventIndex >= 0) {
        setSubagentCardEventCount(aid, Math.max(prev, eventIndex + 1));
    } else if (increment) {
        setSubagentCardEventCount(aid, prev + 1);
    }
}

function trackSubagentStreamEventLightweight(card, agentId, event, eventIndex) {
    if (!card || !agentId || !event) return;
    var t = event.type;
    bumpSubagentCardEventCount(agentId, eventIndex, !event.ephemeral);
    if (t === 'context_tokens') {
        card.dataset.procCtxEstimated = String(event.estimated);
        card.dataset.procCtxThreshold = String(event.threshold);
    } else if (t === 'process_metrics') {
        applySubagentProcessMetricsToCard(card, event);
    } else if (t === 'cache_stats') {
        if (event.cache_hit != null) card.dataset.procCacheHit = String(Math.max(0, Math.floor(Number(event.cache_hit))));
        if (event.cache_miss != null) card.dataset.procCacheMiss = String(Math.max(0, Math.floor(Number(event.cache_miss))));
        if (event.hit_rate != null) card.dataset.procCacheRate = String(Math.max(0, Number(event.hit_rate)));
        if (event.model != null) card.dataset.procCacheModel = String(event.model);
    }
    if (event.react_iter != null) bumpAggregateMaxReactIter(card, event.react_iter);
    scheduleSubagentCardStats(card);
}
