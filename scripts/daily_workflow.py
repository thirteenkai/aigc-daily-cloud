#!/usr/bin/env python3
"""Daily preparation, leased editing, frozen publication and receipt recovery."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import json
from pathlib import Path
import sys
import time
import uuid

import aihot_pipeline as p
from daily_editorial import check_decision, fingerprint

ROOT = Path(p.OPENCLAW_HOME) / 'workspace/artifacts/aigc-daily'
LEASE_SECONDS = 1800


@contextmanager
def day_lock(day_dir):
    day_dir.mkdir(parents=True, exist_ok=True)
    with (day_dir / 'workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def is_sent(log):
    return (log.get('sent') is True and log.get('chatId') == p.DAILY_CHAT_ID
            and log.get('result', {}).get('ok') is True
            and bool(log.get('result', {}).get('data', {}).get('message_id')))


def daily_status(day_dir):
    log = p.read_json(day_dir / 'send.log') if (day_dir / 'send.log').exists() else {}
    if log and (log.get('chatId') not in (None, p.DAILY_CHAT_ID)
                or log.get('idempotencyKey') not in (None, f'aigc-daily-{day_dir.name}-team')):
        raise p.PipelineError('发送台账与日期或群不一致')
    if is_sent(log):
        if (day_dir / 'frozen.json').exists() and not (day_dir / 'verified.json').exists():
            return 'verify'
        return 'delivered'
    if log.get('sendAttempted'):
        return 'uncertain'
    if (day_dir / 'complete.json').exists():
        return 'empty'
    if (day_dir / 'frozen.json').exists():
        return 'resume'
    if (day_dir / 'lease.json').exists():
        lease = p.read_json(day_dir / 'lease.json')
        if lease['expiresAt'] > time.time():
            return 'busy'
    return 'ready'


def history_snapshot(root, date):
    history = []
    day = datetime.strptime(date, '%Y-%m-%d')
    for offset in range(1, 8):
        date_dir = root / (day - timedelta(days=offset)).strftime('%Y-%m-%d')
        if not (date_dir / 'send.log').exists():
            continue
        log = p.read_json(date_dir / 'send.log')
        if not is_sent(log):
            continue
        receipt_id = log['result']['data']['message_id']
        if (date_dir / 'frozen.json').exists():
            frozen = p.read_json(date_dir / 'frozen.json')
            source, decision = frozen['source'], frozen['decision']
            confidence = 'frozen_sent_snapshot'
        elif all((date_dir / n).exists() for n in ('candidates.json', 'decision.json')):
            source = p.read_json(date_dir / 'candidates.json')
            decision = p.read_json(date_dir / 'decision.json')
            confidence = 'legacy_artifacts_may_differ_from_sent_message'
        else:
            continue
        by_key = {x['key']: x for x in source['items']}
        for choice in decision['items']:
            item = by_key.get(choice['key'])
            if not item:
                continue
            history.append({'ref': date_dir.name + '/' + choice['key'], 'date': date_dir.name,
                            'messageId': receipt_id, 'confidence': confidence,
                            'title': item['title'], 'summary': item.get('summary'),
                            'links': item['links'], 'change': choice.get('change'),
                            'insight': choice.get('insight')})
    return {'schemaVersion': 1, 'items': history}


def prepare(root=ROOT, date=None):
    date = date or datetime.now(p.ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    day_dir = root / date
    with day_lock(day_dir):
        status = daily_status(day_dir)
        if status in ('resume', 'verify'):
            return {'status': status, 'dayDir': str(day_dir)}
        if status != 'ready':
            return {'status': status}
        run_id = uuid.uuid4().hex
        run = day_dir / 'runs' / run_id
        run.mkdir(parents=True)
        p.atomic_write_json(day_dir / 'lease.json', {'runId': run_id, 'expiresAt': time.time() + LEASE_SECONDS})
        p.atomic_write_json(run / 'run.json', {'date': date, 'runId': run_id})
    try:
        p.command_fetch_daily(argparse.Namespace(out_dir=str(run)))
        history = history_snapshot(root, date)
        p.atomic_write_json(run / 'history.json', history)
        source = p.read_json(run / 'candidates.json')
        template = {'schemaVersion': 2, 'date': date, 'sourceHash': fingerprint(source),
                    'historyHash': fingerprint(history), 'reviews': [], 'items': []}
        p.atomic_write_json(run / 'decision-template.json', template)
        return {'status': 'ready', 'runDir': str(run), 'leaseMinutes': LEASE_SECONDS // 60}
    except Exception:
        with day_lock(day_dir):
            lease = p.read_json(day_dir / 'lease.json')
            if lease['runId'] == run_id:
                p.atomic_write_json(day_dir / 'lease.json', {**lease, 'expiresAt': 0})
        raise


def render(source, history, decision):
    p.daily_candidates(source)
    check_decision(source, history, decision)
    if not decision['items']:
        return None
    by_key = {x['key']: x for x in source['items']}
    elements = []
    for index, choice in enumerate(decision['items'], 1):
        item = by_key[choice['key']]
        topic = p.normalize_topic(choice.get('topic'), 'topic')
        label = '新进展' if choice['changeType'] == 'update' else '首次报道'
        p.ensure_text(item['title'], 'title', 1, 180)
        content = (f"**{index:02d}｜[{topic}] {item['title']}**\n"
                   f"{label}：{choice['change']}\n值得看：{choice['insight']}")
        elements.extend([{'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}},
                         p.note(item['source']['name'], item['displayLink'])])
    card = {'config': {'wide_screen_mode': True},
            'header': {'template': 'turquoise', 'title': {'tag': 'plain_text', 'content': 'AIGC 日报｜' + decision['date']}},
            'elements': elements}
    p.validate_card(card)
    return card


def review(run):
    source = p.read_json(run / 'candidates.json')
    history = p.read_json(run / 'history.json')
    decision = p.read_json(run / 'decision.json')
    card = render(source, history, decision)
    p.atomic_write_json(run / 'quality.json', check_decision(source, history, decision))
    if card:
        p.atomic_write_json(run / 'card.json', card)
    return {'source': source, 'history': history, 'decision': decision, 'card': card}


def verify(day_dir, frozen):
    log = p.read_json(day_dir / 'send.log')
    if not is_sent(log):
        raise p.PipelineError('没有成功回执可回读')
    message_id = log['result']['data']['message_id']
    result = p.run_json_command(['lark-cli', 'im', '+messages-mget', '--as', 'bot',
                                '--message-ids', message_id, '--no-reactions'], env=p.lark_env())
    messages = result.get('data', {}).get('messages', [])
    message = next((x for x in messages if x.get('message_id') == message_id), None)
    if not message or message.get('chat_id') != p.DAILY_CHAT_ID or message.get('sender', {}).get('id') != p.EXPECTED_APP_ID:
        raise p.PipelineError('回读的消息、群或章北海身份不匹配；不重发')
    content = message.get('content', '')
    texts = [frozen['card']['header']['title']['content']]
    texts += [x['text']['content'] for x in frozen['card']['elements'] if x.get('tag') == 'div']
    if not all(' '.join(x.split()) in ' '.join(content.split()) for x in texts):
        raise p.PipelineError('回读正文不匹配；保留回执，不重发')
    p.atomic_write_json(day_dir / 'verified.json', {'messageId': message_id, 'sender': p.EXPECTED_APP_ID,
                                                  'cardHash': fingerprint(frozen['card'])})


def publish(day_dir, run=None):
    with day_lock(day_dir):
        status = daily_status(day_dir)
        if status in ('delivered', 'empty'):
            return {'status': status}
        if status == 'uncertain':
            raise p.PipelineError('上次发送结果未知，需要核对消息；禁止自动重发')
        if not (day_dir / 'frozen.json').exists():
            if run is None:
                raise p.PipelineError('缺少有效运行目录')
            lease = p.read_json(day_dir / 'lease.json')
            if lease['runId'] != run.name or lease['expiresAt'] <= time.time():
                raise p.PipelineError('运行租约已过期或被接替，禁止发布旧产物')
            frozen = review(run)
            if frozen['decision']['date'] != day_dir.name:
                raise p.PipelineError('日期不匹配')
            p.atomic_write_json(day_dir / 'frozen.json', frozen)
            if frozen['card'] is None:
                p.atomic_write_json(day_dir / 'complete.json', {'status': 'empty', 'reason': frozen['decision']['emptyReason']})
                return {'status': 'empty'}
        frozen = p.read_json(day_dir / 'frozen.json')
        if frozen['card'] is None:
            p.atomic_write_json(day_dir / 'complete.json', {'status': 'empty', 'reason': frozen['decision']['emptyReason']})
            return {'status': 'empty'}
        # Always derive outbound bytes from the frozen publication snapshot.
        p.atomic_write_json(day_dir / 'published-card.json', frozen['card'])
        p.command_send(argparse.Namespace(card=str(day_dir / 'published-card.json'),
                       chat_id=p.DAILY_CHAT_ID, log=str(day_dir / 'send.log'), send=True, event_id=None))
        verify(day_dir, frozen)
        return {'status': 'verified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'review', 'publish', 'resume', 'due'])
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--day-dir', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare()
    elif args.action == 'review':
        review(args.run_dir.resolve())
        result = {'ok': True, 'preview': True}
    elif args.action == 'publish':
        run = args.run_dir.resolve()
        if run.parent.name != 'runs' or run.parent.parent.parent != ROOT:
            raise p.PipelineError('正式发布必须使用生产prepare创建的运行目录')
        result = publish(run.parent.parent, run)
    elif args.action == 'resume':
        day_dir = args.day_dir.resolve()
        if day_dir.parent != ROOT:
            raise p.PipelineError('恢复目录无效')
        result = publish(day_dir)
    else:
        day = datetime.now(p.ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
        with day_lock(ROOT / day):
            status = daily_status(ROOT / day)
        result = {'fire': status in ('ready', 'resume', 'verify', 'uncertain'), 'status': status, 'date': day}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (p.PipelineError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
