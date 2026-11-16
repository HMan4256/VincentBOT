import datetime as dt,tempfile,types,unittest
from pathlib import Path
from unittest.mock import PropertyMock,patch
from vincent.app import Vincent
from vincent.config import Settings
from vincent.extraction import Extractor

class FakeResponder:
 async def decide(self,context,**kwargs):
  return {'decision':'reply','text':'you lost your glasses again','request_id':'fixture'}

class FakeChannel:
 id=2
 def __init__(self):self.sent=[];self.send_options=[]
 def get_partial_message(self,mid):return types.SimpleNamespace(id=mid)
 async def send(self,text,**kwargs):
  message=types.SimpleNamespace(id=200+len(self.sent),created_at=dt.datetime.now(dt.timezone.utc),content=text)
  async def reaction(emoji):pass
  message.add_reaction=reaction;self.sent.append(message);self.send_options.append(kwargs);return message

class FlowTests(unittest.IsolatedAsyncioTestCase):
 async def test_generated_mentions_ping_only_known_users(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False));channel=FakeChannel();now=time.time()
   class MentionResponder:
    async def decide(self,context,**kwargs):
     return {'decision':'reply','text':'@alice <@12> <@999> @everyone','request_id':'fixture'}
   app.responder=MentionResponder()
   app.store.message_event(kind='create',message_id=10,guild_id=1,channel_id=2,author_id=11,content='vin ping alice and bob',created_at=now,changed_at=now,metadata={'username':'alice','mentions':['12']})
   app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10,priority=20);item=app.queue.claim()
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)),patch.object(app,'get_channel',return_value=channel):await app.process_item(item)
   self.assertEqual(channel.sent[0].content,'<@11> <@12> @unknown @everyone')
   permissions=channel.send_options[0]['allowed_mentions'].to_dict()
   self.assertEqual({str(uid) for uid in permissions['users']},{'11','12'})
   self.assertNotIn('everyone',permissions['parse']);self.assertNotIn('roles',permissions['parse'])
   self.assertFalse(channel.send_options[0]['mention_author'])
   self.assertEqual(app.store.db.execute('SELECT send_text FROM queue').fetchone()[0],channel.sent[0].content)
   await app.close()
 async def test_ambiguous_names_are_not_turned_into_pings(self):
  from vincent.app import prepare_user_mentions
  context={'current_user_id':'11','recent_messages':[{'author_id':uid,'metadata':{'display_name':'Alex'}} for uid in ('11','12')]}
  text,allowed=prepare_user_mentions('hey @Alex',context)
  self.assertEqual(text,'hey @Alex');self.assertEqual(allowed.to_dict().get('users',[]),[])
 async def test_other_member_answering_vincents_prompt_is_queued(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),memory_enabled=False));ts=time.time()-16
   qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id='thread',message_id=7,priority=10)
   claim=app.queue.claim();app.queue.begin_send(qid,'prompt',lease_token=claim['lease_token']);app.queue.reconcile_sent(qid,8,sent_at=ts)
   app.store.message_event(kind='create',message_id=8,guild_id=1,channel_id=2,author_id=9,content="you need to admit the toaster won",created_at=ts,changed_at=ts,metadata={'author_bot':True})
   bot=types.SimpleNamespace(id=9,bot=True);channel=FakeChannel()
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=4,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[],content='okay i admit it, the toaster won',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):await app.on_message(message)
   row=app.store.db.execute("SELECT author_id,priority,reason,branch_id FROM queue WHERE author_id='4'").fetchone()
   self.assertEqual(tuple(row),('4',20,'channel_prompt_answer','thread'))
   await app.close()

 async def test_intervening_human_message_suppresses_cross_user_guess(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),memory_enabled=False));ts=time.time()-16
   qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id='thread',message_id=7,priority=10)
   claim=app.queue.claim();app.queue.begin_send(qid,'prompt',lease_token=claim['lease_token']);app.queue.reconcile_sent(qid,8,sent_at=ts)
   app.store.message_event(kind='create',message_id=8,guild_id=1,channel_id=2,author_id=9,content='are you serious?',created_at=ts,changed_at=ts,metadata={'author_bot':True})
   app.store.message_event(kind='create',message_id=9,guild_id=1,channel_id=2,author_id=5,content='unrelated conversation',created_at=ts+2,changed_at=ts+2,metadata={'author_bot':False})
   bot=types.SimpleNamespace(id=9,bot=True);channel=FakeChannel()
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=4,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[],content='nah',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):await app.on_message(message)
   self.assertEqual(app.store.db.execute("SELECT count(*) FROM queue WHERE author_id='4'").fetchone()[0],0)
   await app.close()
 async def test_typing_indicator_covers_model_generation_and_stops(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False));channel=FakeChannel();now=time.time();state=[]
   class Typing:
    async def __aenter__(self):state.append('started')
    async def __aexit__(self,*args):state.append('stopped')
   channel.typing=lambda:Typing()
   class Responder:
    async def decide(self,context,**kwargs):
     self_state=list(state)
     if self_state!=['started']:raise AssertionError(self_state)
     return {'decision':'reply','text':'hello','request_id':'fixture'}
   app.responder=Responder()
   app.store.message_event(kind='create',message_id=10,guild_id=1,channel_id=2,author_id=11,content='vin hello',created_at=now,changed_at=now)
   app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10,priority=20);item=app.queue.claim()
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)),patch.object(app,'get_channel',return_value=channel):
    await app.process_item(item)
   self.assertEqual(state,['started','stopped']);self.assertEqual(len(channel.sent),1);await app.close()

 async def test_typing_failure_does_not_block_response(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False));channel=FakeChannel();now=time.time()
   class BrokenTyping:
    async def __aenter__(self):raise OSError('unavailable')
    async def __aexit__(self,*args):raise AssertionError('not entered')
   channel.typing=lambda:BrokenTyping();app.responder=FakeResponder()
   app.store.message_event(kind='create',message_id=10,guild_id=1,channel_id=2,author_id=11,content='vin hello',created_at=now,changed_at=now)
   app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10,priority=20);item=app.queue.claim()
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)),patch.object(app,'get_channel',return_value=channel):await app.process_item(item)
   self.assertEqual(len(channel.sent),1);await app.close()
 async def test_member_intent_is_only_requested_for_welcomes(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory)
   normal=Vincent(Settings(database=base/'normal',channels=frozenset({2})))
   welcome=Vincent(Settings(database=base/'welcome',channels=frozenset({2}),welcome_channel_id=2))
   self.assertTrue(normal.intents.message_content);self.assertFalse(normal.intents.members)
   self.assertTrue(welcome.intents.message_content);self.assertTrue(welcome.intents.members)
   await normal.close();await welcome.close()
 async def test_uncached_reactor_is_verified_by_reconciliation_before_counting(self):
  from vincent.feedback import Feedback
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),memory_enabled=False))
   qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10)
   claim=app.queue.claim();app.queue.begin_send(qid,'hello',lease_token=claim['lease_token']);app.queue.reconcile_sent(qid,200)
   bot=types.SimpleNamespace(id=9,bot=True)
   payload=types.SimpleNamespace(channel_id=2,guild_id=1,user_id=12,message_id=200,emoji='👍',member=None)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_guild',return_value=None):
    await app.on_raw_reaction_add(payload)
   ledger=Feedback(app.store,9)
   self.assertEqual(ledger.votes(200),{})
   self.assertEqual(app.store.db.execute("SELECT count(*) FROM events WHERE kind='feedback_voter_unverified'").fetchone()[0],1)
   ledger.reconcile(200,[('12','👍')])
   self.assertEqual(ledger.votes(200),{'12':'positive'})
   await app.close()
 async def test_unavailable_channel_during_reconciliation_is_persistently_logged(self):
  import json
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),memory_enabled=False))
   qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10)
   claim=app.queue.claim();app.queue.begin_send(qid,'hello',lease_token=claim['lease_token']);app.queue.reconcile_sent(qid,200)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)),patch.object(app,'get_channel',return_value=None):
    await app.feedback_worker.coro(app)
   event=app.store.db.execute("SELECT message_id,payload FROM events WHERE kind='feedback_reconcile_error'").fetchone()
   self.assertEqual(event['message_id'],'200');self.assertEqual(json.loads(event['payload']),{'error':'channel_unavailable'})
   await app.close()
 async def test_unsolicited_reply_is_cancelled_if_quiet_hours_begin_during_generation(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),unsolicited_enabled=True,reactions_enabled=False,memory_enabled=False)
   app=Vincent(config);app.responder=FakeResponder();channel=FakeChannel();now=time.time()
   app.store.message_event(kind='create',message_id=10,guild_id=1,channel_id=2,author_id=11,content='bro what happened?',created_at=now,changed_at=now,metadata={'author_bot':False})
   qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10,priority=1,reason='observe')
   item=app.queue.claim()
   with patch.object(app,'get_channel',return_value=channel),patch.object(app.opportunities,'quiet',side_effect=[False,True]):
    await app.process_item(item)
   self.assertEqual(channel.sent,[])
   row=app.store.db.execute('SELECT status,reason FROM queue WHERE id=?',(qid,)).fetchone()
   self.assertEqual(tuple(row),('cancelled','unsolicited disabled or quiet hours'))
   await app.close()
 async def test_two_direct_users_are_not_lost(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);app.responder=FakeResponder();app.extractor=Extractor(app.store,app.memory,None);channel=FakeChannel()
   user=types.SimpleNamespace(id=9,bot=True)
   def incoming(mid,uid,text):
    return types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=uid,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[user],content=text,attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=channel):
    await app.on_message(incoming(10,11,'vin roast me'))
    await app.on_message(incoming(12,13,'vin tell me something'))
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app);await app.worker.coro(app)
    import asyncio
    await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(len(channel.sent),2)
   self.assertEqual({x['reference'].id for x in channel.send_options},{10,12})
   self.assertTrue(all(not x['mention_author'] for x in channel.send_options))
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=channel):
    follow=incoming(14,11,'unfortunately');follow.mentions=[]
    await app.on_message(follow)
   with app.store.lock:
    followup=app.store.db.execute("SELECT reason FROM queue WHERE author_id='11' AND status='pending'").fetchone()
   self.assertIsNotNone(followup);self.assertEqual(followup['reason'],'recent_participant_followup')
   self.assertEqual({r['author_id'] for r in app.store.db.execute("SELECT author_id FROM queue WHERE status='sent'")},{'11','13'})
   await app.close()
 async def test_direct_silence_gets_one_retry(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);channel=FakeChannel();user=types.SimpleNamespace(id=9,bot=True)
   class SilentThenReply:
    def __init__(self):self.calls=0
    async def decide(self,context,**kwargs):
     self.calls+=1
     return {'decision':'silence' if self.calls==1 else 'reply','text':'' if self.calls==1 else 'here','request_id':'test'}
   app.responder=SilentThenReply();app.extractor=Extractor(app.store,app.memory,None)
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[user],content='vin hello',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=channel):
    await app.on_message(message)
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app)
    import asyncio
    await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(app.responder.calls,2);self.assertEqual(len(channel.sent),1);await app.close()
 async def test_uncertain_send_recovers_only_matching_nonce(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);user=types.SimpleNamespace(id=9,bot=True);qid=app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id=10,message_id=10);claim=app.queue.claim();nonce=app.queue.begin_send(qid,'hello',lease_token=claim['lease_token'])
   class HistoryChannel:
    async def history(self,limit):
     yield types.SimpleNamespace(id=200,nonce=None,author=user,created_at=dt.datetime.now(dt.timezone.utc),content='hello')
     yield types.SimpleNamespace(id=201,nonce=nonce,author=user,created_at=dt.datetime.now(dt.timezone.utc),content='hello')
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=HistoryChannel()):await app.recover_uncertain()
   row=app.store.db.execute('SELECT status,response_message_id FROM queue WHERE id=?',(qid,)).fetchone()
   self.assertEqual(tuple(row),('sent','201'));await app.close()
 async def test_data_erasure_during_model_request_prevents_send(self):
  import asyncio
  from vincent.privacy import erase_user
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);channel=FakeChannel();user=types.SimpleNamespace(id=9,bot=True);started=asyncio.Event();release=asyncio.Event()
   class SlowResponder:
    async def decide(self,context,**kwargs):
     started.set();await release.wait();return {'decision':'reply','text':'private reply','request_id':'fixture'}
   app.responder=SlowResponder();app.extractor=Extractor(app.store,app.memory,None)
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[user],content='vin help me',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=channel):
    await app.on_message(message)
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app);await started.wait();erase_user(app.store,1,11);release.set();await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(channel.sent,[])
   self.assertEqual(app.store.db.execute('SELECT status FROM queue').fetchone()[0],'cancelled');await app.close()
 async def test_ingestion_never_downloads_attachment(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);channel=FakeChannel();user=types.SimpleNamespace(id=9,bot=True)
   class Attachment:
    url='https://example.invalid/image.png';content_type='image/png'
    async def read(self):raise AssertionError('media must not be downloaded')
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[],content='ordinary message',attachments=[Attachment()])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user):await app.on_message(message)
   metadata=app.store.db.execute('SELECT metadata FROM messages').fetchone()[0]
   self.assertIn('image/png',metadata);self.assertNotIn('base64',metadata);await app.close()
 async def test_rate_limit_keeps_direct_work_until_retry(self):
  import asyncio
  from vincent.llm import ProviderRateLimited
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);channel=FakeChannel();user=types.SimpleNamespace(id=9,bot=True)
   class RateLimitedOnce:
    def __init__(self):self.calls=0
    async def decide(self,context,**kwargs):
     self.calls+=1
     if self.calls==1:raise ProviderRateLimited(10)
     return {'decision':'reply','text':'back','request_id':'fixture'}
   app.responder=RateLimitedOnce();app.extractor=Extractor(app.store,app.memory,None)
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[user],content='vin hello',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user),patch.object(app,'get_channel',return_value=channel):
    await app.on_message(message)
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight));self.assertEqual(app.store.db.execute('SELECT status FROM queue').fetchone()[0],'pending')
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10,available_at=0')
    await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(len(channel.sent),1);self.assertEqual(app.responder.calls,2);await app.close()
 async def test_reply_to_uncached_bot_message_is_routed_directly(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False,group_direct=False)
   app=Vincent(config);channel=FakeChannel();bot=types.SimpleNamespace(id=9,bot=True)
   original=types.SimpleNamespace(id=7,author=bot,created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[],content='old reply',attachments=[])
   async def fetch(mid):return original
   channel.fetch_message=fetch
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=types.SimpleNamespace(message_id=7,channel_id=2,resolved=None),mentions=[],content='fair',attachments=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):await app.on_message(message)
   row=app.store.db.execute('SELECT priority,reason FROM queue').fetchone();self.assertEqual(tuple(row),(20,'reply_to_bot'));await app.close()
 async def test_help_command_is_local_and_does_not_enqueue(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Settings(database=Path(directory)/'test.sqlite',channels=frozenset({2}),reactions_enabled=False,memory_enabled=True)
   app=Vincent(config);channel=FakeChannel();user=types.SimpleNamespace(id=9,bot=True);replies=[]
   async def reply(text,**kwargs):replies.append(text)
   message=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[],content='!vin help',attachments=[],reply=reply)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=user):await app.on_message(message)
   self.assertIn('memory',replies[0]);self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM request_attempts').fetchone()[0],0);await app.close()
if __name__=='__main__':unittest.main()
