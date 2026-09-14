import copy
import base64
import os
from cryptography.fernet import Fernet
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cloud_daily as c
from test_daily_editorial import fixture


def state_fixture():
    source, history, decision = fixture()
    return {'version': 1, 'kind': 'aigc-daily', 'startDate': '2026-09-14', 'history': [],
            'chatId': c.p.DAILY_CHAT_ID, 'appId': c.p.EXPECTED_APP_ID,
            'days': {'2026-09-14': {'phase': 'prepared', 'source': source, 'history': history,
                                   'decision': decision, 'card': c.render(source, history, decision)}},
            'migrationReceipt': {'messageId': 'om_old', 'contentHash': 'hash'}}


class CloudDailyTests(unittest.TestCase):
    def test_large_remote_state_uses_raw_content_fallback(self):
        key=Fernet.generate_key();state=state_fixture()
        encrypted=Fernet(key).encrypt(json.dumps(state).encode())
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'owner/repo','STATE_KEY':key.decode()}):
            remote=c.RemoteState()
            with patch.object(remote,'request',side_effect=[{'sha':'revision','encoding':'none'},encrypted]) as request:
                self.assertEqual(remote.load(),state)
                request.assert_called_with(raw=True)

    def test_corrupt_or_wrong_target_state_never_initializes_silently(self):
        key=Fernet.generate_key();state=state_fixture();state['chatId']='another-chat'
        encrypted=Fernet(key).encrypt(json.dumps(state).encode())
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'owner/repo','STATE_KEY':key.decode()}):
            remote=c.RemoteState()
            with patch.object(remote,'request',return_value={'sha':'revision','encoding':'base64','content':base64.b64encode(encrypted).decode()}):
                with self.assertRaises(ValueError):remote.load()

    def test_send_is_checkpointed_before_network_and_receipt_before_readback(self):
        state=state_fixture();events=[]
        def save(s): events.append(s['days']['2026-09-14']['phase'])
        def send(args):
            self.assertEqual(events,['attempting'])
            events.append('network-send')
            return {'ok':True,'data':{'message_id':'om_sent'}}
        def read(*args): self.assertEqual(events,['attempting','network-send','sent'])
        with patch.object(c,'dry_run'),patch.object(c,'lark',side_effect=send),patch.object(c,'readback',side_effect=read):
            self.assertEqual(c.publish(state,save,'2026-09-14'),'verified')
        self.assertEqual(events[-1],'verified')
        self.assertEqual(len(state['history']),1)

    def test_failed_pending_persistence_prevents_send(self):
        with patch.object(c,'dry_run'),patch.object(c,'lark') as send:
            with self.assertRaises(OSError): c.publish(state_fixture(),lambda s: (_ for _ in ()).throw(OSError()),'2026-09-14')
            send.assert_not_called()

    def test_uncertain_send_is_never_retried(self):
        state=state_fixture();state['days']['2026-09-14']['phase']='attempting'
        with patch.object(c,'lark') as send:
            with self.assertRaises(RuntimeError):c.publish(state,lambda s:None,'2026-09-14')
            send.assert_not_called()

    def test_successful_send_readback_failure_recovers_without_second_send(self):
        state=state_fixture();saved=[]
        with patch.object(c,'dry_run'),patch.object(c,'lark',return_value={'ok':True,'data':{'message_id':'om_sent'}}) as send,patch.object(c,'readback',side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError): c.publish(state,lambda s:saved.append(copy.deepcopy(s)),'2026-09-14')
            self.assertEqual(send.call_count,1)
        self.assertEqual(saved[-1]['days']['2026-09-14']['phase'],'sent')
        with patch.object(c,'lark') as send,patch.object(c,'readback'):
            c.publish(saved[-1],lambda s:None,'2026-09-14')
            send.assert_not_called()

    def test_completed_day_costs_no_model_calls(self):
        state=state_fixture();state['days']['2026-09-14']['phase']='verified'
        with patch.object(c,'create_publication') as model:
            self.assertEqual(c.run(state,lambda s:None,'scheduled',datetime(2026,9,14,10,tzinfo=ZoneInfo('Asia/Shanghai'))),'already_complete')
            model.assert_not_called()

    def test_older_uncertain_delivery_blocks_new_day(self):
        state=state_fixture();state['days']['2026-09-13']={'phase':'attempting'}
        with patch.object(c,'create_publication') as model:
            with self.assertRaises(RuntimeError):c.run(state,lambda s:None,'scheduled',datetime(2026,9,14,10))
            model.assert_not_called()

    def test_older_unsent_draft_expires_without_sending(self):
        state=state_fixture();state['days']['2026-09-13']={'phase':'prepared'}
        state['days']['2026-09-14']['phase']='verified'
        with patch.object(c,'publish') as send:
            c.run(state,lambda s:None,'scheduled',datetime(2026,9,14,10))
            send.assert_not_called()
        self.assertEqual(state['days']['2026-09-13']['phase'],'expired')

    def test_no_early_send_or_model_call(self):
        with patch.object(c,'create_publication') as model:
            self.assertEqual(c.run(state_fixture(),lambda s:None,'scheduled',datetime(2026,9,14,1,tzinfo=ZoneInfo('Asia/Shanghai'))),'outside_delivery_window')
            model.assert_not_called()

    def test_preview_cannot_freeze_or_send_production_day(self):
        state=state_fixture();before=copy.deepcopy(state['days']);s,h,d=fixture()
        with patch.object(c,'fetch_source',return_value=s),patch.object(c,'model_decision',return_value=(d,c.render(s,h,d),{})),patch.object(c,'dry_run'),patch.object(c,'readback'),patch.object(c,'publish') as send:
            c.run(state,lambda s:None,'validate',datetime(2026,9,14,1))
            send.assert_not_called()
        self.assertEqual(state['days'],before)
        self.assertEqual(state['modelCalls']['preview:2026-09-14'],1)

    def test_daily_budget_is_durable_and_bounded(self):
        state=state_fixture();state['modelCalls']={'preview:2026-09-14':6}
        with patch.object(c,'fetch_source',return_value=fixture()[0]),patch.object(c,'model_decision') as model:
            with self.assertRaises(RuntimeError):c.create_publication(state,lambda s:None,'2026-09-14',preview=True)
            model.assert_not_called()

    def test_history_window_and_private_metadata_removed(self):
        state=state_fixture();state['history']=[{'ref':'x','date':d,'messageId':'private','title':'news','links':{}} for d in ('2026-09-06','2026-09-07','2026-09-13','2026-09-14')]
        result=c.history_for(state,'2026-09-14')['items']
        self.assertEqual([h['date'] for h in result],['2026-09-07','2026-09-13'])
        self.assertNotIn('messageId',json.dumps(result))

    def test_wrong_bot_rejected_on_readback(self):
        result={'ok':True,'data':{'messages':[{'message_id':'om_sent','chat_id':c.p.DAILY_CHAT_ID,'sender':{'id':'wrong'},'content':'content'}]}}
        with patch.object(c,'lark',return_value=result):
            with self.assertRaises(RuntimeError):c.readback('om_sent')

    def test_readback_checks_body_and_source_links(self):
        card=state_fixture()['days']['2026-09-14']['card']
        texts=[card['header']['title']['content']]+[e['text']['content'] for e in card['elements'] if e['tag']=='div']+[e['elements'][0]['content'] for e in card['elements'] if e['tag']=='note']
        result={'ok':True,'data':{'messages':[{'message_id':'om_sent','chat_id':c.p.DAILY_CHAT_ID,'sender':{'id':c.p.EXPECTED_APP_ID},'content':'\n'.join(texts)}]}}
        with patch.object(c,'lark',return_value=result):c.readback('om_sent',card)
        result['data']['messages'][0]['content']='\n'.join(texts[:-1])
        with patch.object(c,'lark',return_value=result):
            with self.assertRaises(RuntimeError):c.readback('om_sent',card)


if __name__=='__main__':unittest.main()

class ReceiptImportTests(unittest.TestCase):
    def fixture(self):
        state=state_fixture();item=state['days']['2026-09-14'];selected=item['decision']['items'][0];original=item['source']['items'][0]
        entry={'ref':'2026-09-14/'+original['key'],'date':'2026-09-14','title':original['title'],'summary':original['summary'],'links':original['links'],'change':selected['change'],'insight':selected['insight']}
        return state,{'date':'2026-09-14','messageId':'om_approved','card':item['card'],'entries':[entry]}

    def test_verified_import_updates_history_once_without_sending(self):
        state,receipt=self.fixture()
        with patch.object(c,'identity'),patch.object(c,'readback') as read,patch.object(c,'lark') as send:
            self.assertEqual(c.import_approved_receipt(state,lambda s:None,receipt),'approved_receipt_imported_without_sending')
            self.assertEqual(c.import_approved_receipt(state,lambda s:None,receipt),'receipt_already_imported')
            read.assert_called_once();send.assert_not_called()
        self.assertEqual(len(state['history']),1)
        self.assertEqual(state['days']['2026-09-14']['phase'],'prepared')

    def test_unverified_or_mismatched_content_cannot_enter_history(self):
        state,receipt=self.fixture()
        with patch.object(c,'identity'),patch.object(c,'readback',side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError):c.import_approved_receipt(state,lambda s:None,receipt)
        self.assertEqual(state['history'],[])
        receipt['entries'][0]['title']='not in card'
        with self.assertRaises(ValueError):c.import_approved_receipt(state,lambda s:None,receipt)
