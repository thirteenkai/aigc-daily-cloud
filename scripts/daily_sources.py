"""Supplement the general selected feed with explicit creative-topic searches."""
from urllib.parse import urlencode
import aihot_pipeline as p

CREATION_QUERIES = ('视频', '图像', '音频', '音乐', '语音', '数字人', '短剧')
MAX_PAGES = 5
MAX_CANDIDATES = 70


def supplement_creation(source, fetch=p.fetch_json, queries=CREATION_QUERIES):
    items = [dict(x) for x in source['items']]
    link_index = {link: i for i, x in enumerate(items) for link in x['links'].values() if link}
    coverage = []
    for query in queries:
        cursor = None
        seen = set()
        count = 0
        for page in range(MAX_PAGES):
            params = {'mode': 'all', 'window': '24h', 'by': 'timeline', 'q': query, 'limit': 50}
            if cursor:
                params['cursor'] = cursor
            payload, meta = fetch('https://aihot.virxact.com/api/v1/items?' + urlencode(params))
            raw = p.validate_selected_response(payload)
            count += len(raw)
            for record in raw:
                candidate = p.normalize_candidate(record, origin='creative_search_24h')
                candidate['selected'] = record.get('selected') is True
                candidate['recommendationReason'] = record.get('reason')
                candidate['matchedQueries'] = [query]
                links = [v for v in candidate['links'].values() if v]
                index = next((link_index[v] for v in links if v in link_index), None)
                if index is None:
                    index = len(items)
                    items.append(candidate)
                else:
                    before = items[index]
                    merged = p.merge_candidate(before, candidate)
                    merged['matchedQueries'] = list(dict.fromkeys(before.get('matchedQueries', []) + [query]))
                    merged['selected'] = before.get('selected', False) or candidate['selected'] or 'selected_24h' in before['origins']
                    merged['recommendationReason'] = candidate['recommendationReason'] or before.get('recommendationReason')
                    items[index] = merged
                for link in links:
                    link_index[link] = index
            pagination = payload.get('page', {})
            if pagination.get('hasMore') is False:
                break
            cursor = pagination.get('nextCursor')
            if pagination.get('hasMore') is not True or not cursor or cursor in seen:
                raise p.PipelineError('创作候选分页不完整，禁止伪装抓取成功')
            seen.add(cursor)
        else:
            raise p.PipelineError('创作候选超过分页上限，需要完整审查，不自动截断')
        coverage.append({'query': query, 'count': count, 'pages': page + 1})
    if len(items) > MAX_CANDIDATES:
        raise p.PipelineError('创作候选超过单轮编辑容量，需要拆批审查，不自动删减')
    return {**source, 'items': items, 'creativeCoverage': coverage}
