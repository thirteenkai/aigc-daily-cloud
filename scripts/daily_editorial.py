"""Validate editorial coverage and evidence references, not subjective news truth."""
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_decision(source, history, decision):
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    def prose(value, field, minimum=12, maximum=240):
        require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum,
                field + ' 必须是具体说明')
        return value.strip()

    require(decision.get('schemaVersion') == 2, 'Daily 必须使用 decision v2')
    require(decision.get('sourceHash') == fingerprint(source), '候选快照已改变')
    require(decision.get('historyHash') == fingerprint(history), '历史快照已改变')
    candidates = {x['key']: x for x in source['items']}
    history_by_ref = {x['ref']: x for x in history['items']}
    reviews = decision.get('reviews', [])
    require(isinstance(reviews, list) and len(reviews) == len(candidates), '每条候选必须有且只有一条取舍记录')
    require({x.get('key') for x in reviews} == set(candidates), '取舍记录未覆盖全部候选')
    choices = decision.get('items', [])
    require(isinstance(choices, list) and len(choices) <= 7, '最多选7个事件')
    choice_by_key = {x['key']: x for x in choices}
    require(len(choice_by_key) == len(choices), '入选key重复')
    require(set(choice_by_key) <= set(candidates), '入选内容不在候选中')
    by_key = {x['key']: x for x in reviews}
    require({x['key'] for x in reviews if x.get('disposition') == 'include'} == set(choice_by_key), '入选与取舍记录不一致')
    events = set()
    for r in reviews:
        key = r['key']
        require(r.get('priority') in ('creation', 'workflow', 'industry'), '无效优先级')
        require(r.get('importance') in ('major', 'useful', 'low'), '无效重要性')
        require(r.get('disposition') in ('include', 'merge', 'duplicate', 'defer', 'omit'), '无效取舍')
        prose(r.get('reason'), key + '.reason')
        event = prose(r.get('event'), key + '.event', 3, 100)
        disposition = r['disposition']
        if disposition == 'merge':
            target = by_key.get(r.get('mergedInto'), {})
            require(target.get('disposition') == 'include' and target.get('event') == event, '合并必须指向同事件的入选候选')
        if disposition == 'duplicate':
            require(r.get('historyRef') in history_by_ref, '重复报道必须引用已发送历史')
        if r.get('historyRef'):
            require(r['historyRef'] in history_by_ref, 'historyRef 不存在')
        if r['priority'] == 'creation' and r['importance'] == 'major' and disposition in ('omit', 'defer'):
            require(r.get('reasonCode') in ('insufficient_evidence', 'not_new', 'not_available', 'capacity'), '重要创作内容不得无理由舍弃')
            prose(r.get('coverageReview'), key + '.coverageReview', 20)
            if r['reasonCode'] == 'not_new':
                require(r.get('historyRef') in history_by_ref, '无新变化判断必须引用历史')
            if r['reasonCode'] == 'capacity':
                require(len(choices) == 7 and r.get('preferredKey') in choice_by_key, '容量取舍须满7条并指出更重要的入选项')
                better = by_key[r['preferredKey']]
                require(better['priority'] == 'creation' and better['importance'] == 'major', '重要创作内容容量取舍不能让位于低优先级内容')
        if disposition != 'include':
            continue
        require(event not in events, '同一事件不能重复入选')
        events.add(event)
        c = choice_by_key[key]
        require(c.get('changeType') in ('new', 'update'), '入选须标明首次报道或实质更新')
        links = set(candidates[key].get('links', {}).values()) - {None, ''}
        previously_sent = [h for h in history['items'] if links & (set(h.get('links', {}).values()) - {None, ''})]
        if previously_sent:
            require(c['changeType'] == 'update' and r.get('historyRef') in {h['ref'] for h in previously_sent}, '已发送链接只能按有依据的新进展入选')
        if c['changeType'] == 'update':
            require(r.get('historyRef') in history_by_ref, '新进展必须引用已发送历史')
            prose(c.get('delta'), key + '.delta')
        for field in ('change', 'insight'):
            prose(c.get(field), key + '.' + field, 12, 140)
        evidence = c.get('evidence', [])
        require(isinstance(evidence, list) and evidence, '入选必须附来源原文依据')
        for e in evidence:
            origin = candidates.get(e.get('key'))
            require(origin is not None and by_key[e['key']]['event'] == event, '证据必须来自同事件候选')
            quote = prose(e.get('quote'), 'evidence.quote', 6, 300)
            require(quote in (origin.get('title', '') + '\n' + (origin.get('summary') or '')), '证据摘录不在来源标题或摘要中')
    # Preserve editorial order within a priority; prevent industry-first default sections.
    rank = {'creation': 0, 'workflow': 1, 'industry': 2}
    priorities = [rank[by_key[c['key']]['priority']] for c in choices]
    require(priorities == sorted(priorities), '按创作、工作流、行业顺序排列')
    if not choices:
        prose(decision.get('emptyReason'), 'emptyReason', 20)
    return {'ok': True, 'candidates': len(candidates), 'selected': len(choices),
            'note': '结构、覆盖和证据引用通过；不等于事实真伪或编辑质量已自动证明'}
