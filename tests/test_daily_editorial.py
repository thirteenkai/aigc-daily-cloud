import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import daily_editorial as e
import daily_workflow as w
import aihot_pipeline as p


def fixture():
    source = {'schemaVersion': 1, 'items': [p.normalize_candidate({
        'id': 'image', 'title': '图像工具开放局部编辑功能', 'summary': '官方公布可以修改局部对象和图中文字，开放范围尚未说明。',
        'source': {'name': '官方'}, 'links': {'aihot': 'https://aihot.news/items/image', 'original': 'https://example.com/image'}
    }, origin='selected_24h')]}
    key = source['items'][0]['key']
    history = {'schemaVersion': 1, 'items': []}
    decision = {'schemaVersion': 2, 'date': '2026-09-14', 'sourceHash': e.fingerprint(source), 'historyHash': e.fingerprint(history),
                'reviews': [{'key': key, 'event': '图像工具局部编辑', 'priority': 'creation', 'importance': 'major',
                             'disposition': 'include', 'reason': '局部编辑改变图像修改的操作范围，值得优先了解。'}],
                'items': [{'key': key, 'topic': '图像生成', 'changeType': 'new', 'change': '官方公布可以修改局部对象和图中文字，开放范围尚未说明。',
                           'insight': '为修改画面对象和文字提供了更精细的入口。',
                           'evidence': [{'key': key, 'quote': '官方公布可以修改局部对象和图中文字'}]}]}
    return source, history, decision


class EditorialTests(unittest.TestCase):
    def test_valid_creation_renders_change_without_empty_claims(self):
        s,h,d = fixture()
        card = w.render(s,h,d)
        body = json.dumps(card, ensure_ascii=False)
        self.assertIn('首次报道', body)
        self.assertNotIn('兼顾', body)

    def test_real_product_beta_status_is_not_a_test_message(self):
        s,h,d=fixture();s['items'][0]['summary']='官方开放公开测试版，支持局部对象编辑，具体可用范围仍待确认。'
        d['sourceHash']=e.fingerprint(s);d['items'][0]['change']=s['items'][0]['summary']
        d['items'][0]['evidence']=[{'key':s['items'][0]['key'],'quote':s['items'][0]['title']}]
        self.assertIn('公开测试版',json.dumps(w.render(s,h,d),ensure_ascii=False))

    def test_missing_candidate_review_fails(self):
        s,h,d = fixture();d['reviews'] = []
        with self.assertRaises(ValueError): e.check_decision(s,h,d)

    def test_changed_candidate_or_history_cannot_reuse_decision(self):
        s,h,d = fixture();s['items'][0]['summary'] += 'changed'
        with self.assertRaises(ValueError):e.check_decision(s,h,d)

    def test_fabricated_evidence_fails(self):
        s,h,d = fixture();d['items'][0]['evidence'][0]['quote'] = '永久免费并已经全面开放给所有用户'
        with self.assertRaises(ValueError):e.check_decision(s,h,d)

    def test_duplicate_requires_real_receipt_history(self):
        s,h,d = fixture();d['items']=[];d['emptyReason']='当天候选全部为过去已经发送的重复内容，不再次打扰群聊。'
        d['reviews'][0].update(disposition='duplicate',historyRef='invented')
        with self.assertRaises(ValueError):e.check_decision(s,h,d)
        h['items']=[{'ref':'real-sent','links':s['items'][0]['links']}]
        d['historyHash']=e.fingerprint(h);d['reviews'][0]['historyRef']='real-sent'
        self.assertIsNone(w.render(s,h,d))

    def test_previous_link_requires_substantive_update(self):
        s,h,d=fixture();h['items']=[{'ref':'sent','links':s['items'][0]['links']}];d['historyHash']=e.fingerprint(h)
        with self.assertRaises(ValueError):e.check_decision(s,h,d)
        d['reviews'][0]['historyRef']='sent';d['items'][0].update(changeType='update',delta='本次新增开放范围说明，区别于上次功能预告。')
        e.check_decision(s,h,d)

    def test_major_creation_cannot_be_dropped_for_vague_reason(self):
        s,h,d=fixture();d['items']=[];d['reviews'][0]['disposition']='omit'
        d['emptyReason']='今天未发现足够明确的新变化，本期不向群里发送。'
        with self.assertRaises(ValueError):e.check_decision(s,h,d)
        d['reviews'][0].update(reasonCode='insufficient_evidence',coverageReview='仅有功能描述，无法判断此次是否新增或可用，保留候选等待进一步信息。')
        e.check_decision(s,h,d)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.day=self.root/'2026-09-14'

    def fetch(self,args):
        p.atomic_write_json(Path(args.out_dir)/'candidates.json',fixture()[0])

    def prepared(self):
        with patch.object(p,'command_fetch_daily',side_effect=self.fetch):
            result=w.prepare(self.root,'2026-09-14')
        run=Path(result['runDir']);s,h,d=fixture()
        d['historyHash']=e.fingerprint(p.read_json(run/'history.json'))
        p.atomic_write_json(run/'decision.json',d)
        return run

    def test_second_worker_does_not_fetch_or_overwrite(self):
        self.prepared()
        with patch.object(p,'command_fetch_daily') as fetch:
            self.assertEqual(w.prepare(self.root,'2026-09-14')['status'],'busy')
            fetch.assert_not_called()

    def test_expired_worker_cannot_publish_after_replacement(self):
        old=self.prepared();lease=p.read_json(self.day/'lease.json');lease['expiresAt']=0;p.atomic_write_json(self.day/'lease.json',lease)
        new=self.prepared();self.assertNotEqual(old,new)
        with patch.object(p,'command_send') as send, self.assertRaises(p.PipelineError):w.publish(self.day,old)
        send.assert_not_called()

    def test_sent_receipt_readback_failure_never_resends(self):
        run=self.prepared()
        def send(args):
            p.atomic_write_json(Path(args.log),{'chatId':p.DAILY_CHAT_ID,'idempotencyKey':'aigc-daily-2026-09-14-team','sent':True,'result':{'ok':True,'data':{'message_id':'om_test'}}})
        with patch.object(p,'command_send',side_effect=send),patch.object(w,'verify',side_effect=p.PipelineError('readback unavailable')):
            with self.assertRaises(p.PipelineError):w.publish(self.day,run)
        self.assertEqual(w.daily_status(self.day),'verify')
        frozen=p.read_json(self.day/'frozen.json')
        # Actual sender gate sees the receipt and performs no API call.
        with patch.object(p,'run_json_command') as network,patch.object(w,'verify'):
            w.publish(self.day)
        network.assert_not_called();self.assertEqual(p.read_json(self.day/'frozen.json'),frozen)

    def test_unknown_send_stops_recovery(self):
        self.prepared();p.atomic_write_json(self.day/'send.log',{'sendAttempted':True,'sent':False})
        with patch.object(p,'command_send') as send,self.assertRaises(p.PipelineError):w.publish(self.day)
        send.assert_not_called()

    def test_empty_day_is_completed_once_without_sending(self):
        run=self.prepared();d=p.read_json(run/'decision.json');d['items']=[]
        d['emptyReason']='现有候选缺少明确上线信息，暂不作为已经可用的更新发送。'
        d['reviews'][0].update(disposition='defer',reasonCode='insufficient_evidence',coverageReview='当前摘要没有上线或适用范围信息，先保留候选，待有依据后再做判断。')
        p.atomic_write_json(run/'decision.json',d)
        with patch.object(p,'command_send') as send:
            self.assertEqual(w.publish(self.day,run)['status'],'empty')
            self.assertEqual(w.prepare(self.root,'2026-09-14')['status'],'empty')
        send.assert_not_called()

    def test_readback_accepts_exact_body_and_expected_sender(self):
        run=self.prepared();f=w.review(run)
        p.atomic_write_json(self.day/'send.log',{'chatId':p.DAILY_CHAT_ID,'sent':True,'result':{'ok':True,'data':{'message_id':'om_test'}}})
        content=f['card']['header']['title']['content']+'\n'+ '\n'.join(x['text']['content'] for x in f['card']['elements'] if x.get('tag')=='div')
        message={'message_id':'om_test','chat_id':p.DAILY_CHAT_ID,'sender':{'id':p.EXPECTED_APP_ID},'content':content}
        with patch.object(p,'run_json_command',return_value={'data':{'messages':[message]}}):w.verify(self.day,f)
        self.assertTrue((self.day/'verified.json').exists())
        message['content']='different body'
        with patch.object(p,'run_json_command',return_value={'data':{'messages':[message]}}):
            with self.assertRaises(p.PipelineError):w.verify(self.day,f)

    def test_readback_rejects_wrong_sender(self):
        run=self.prepared();f=w.review(run)
        p.atomic_write_json(self.day/'send.log',{'chatId':p.DAILY_CHAT_ID,'sent':True,'result':{'ok':True,'data':{'message_id':'om_test'}}})
        with patch.object(p,'run_json_command',return_value={'data':{'messages':[{'message_id':'om_test','chat_id':p.DAILY_CHAT_ID,'sender':{'id':'other'}}]}}):
            with self.assertRaises(p.PipelineError):w.verify(self.day,f)


if __name__=='__main__':unittest.main()
