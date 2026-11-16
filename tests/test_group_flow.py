import asyncio,datetime as dt,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import PropertyMock,patch
import httpx
from vincent.app import Vincent
from vincent.config import Settings
from vincent.extraction import Extractor
from vincent.llm import Gateway
from vincent.response import Responder,parse_group

class Channel:
 id=2
 def __init__(self):self.sent=[]
 def get_partial_message(self,mid):return types.SimpleNamespace(id=mid)
 async def send(self,text,**kwargs):
  msg=types.SimpleNamespace(id=200+len(self.sent),content=text,created_at=dt.datetime.now(dt.timezone.utc),reference_id=kwargs['reference'].id)
  self.sent.append(msg);return msg

class GroupTests(unittest.IsolatedAsyncioTestCase):
 async def test_deletion_outside_one_members_window_invalidates_entire_group(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,memory_enabled=False));channel=Channel();started=asyncio.Event();release=asyncio.Event();now=time.time()
   class WaitingResponder:
    async def decide_group(self,contexts):
     self.contexts=contexts;started.set();await release.wait()
     return {x['queue_id']:{'decision':'reply','text':'reply'} for x in contexts},None
   responder=WaitingResponder();app.responder=responder
   for i in range(26):
    mid=10+i;uid=11 if i==0 else 13 if i==25 else 99
    app.store.message_event(kind='create',message_id=mid,guild_id=1,channel_id=2,author_id=uid,content='message '+str(i),created_at=now-30+i,changed_at=now-30+i)
   for mid,uid in ((10,11),(35,13)):
    app.queue.enqueue(guild_id=1,channel_id=2,author_id=uid,branch_id=mid,message_id=mid,priority=20)
   items=[app.queue.claim(),app.queue.claim()]
   with patch.object(app,'get_channel',return_value=channel):
    task=asyncio.create_task(app.process_group(items));await started.wait()
    second=responder.contexts[1]['conversation']['recent_messages']
    self.assertNotIn('10',{m['message_id'] for m in second})
    await app._apply_delete(2,1,10);release.set();await task
   self.assertEqual(channel.sent,[])
   self.assertEqual([tuple(r) for r in app.store.db.execute('SELECT status FROM queue ORDER BY id')],[('cancelled',),('pending',)])
   await app.close()
 async def test_two_users_share_one_request_get_separate_targeted_replies(self):
  with tempfile.TemporaryDirectory() as directory:
   cfg=Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,group_direct=True)
   app=Vincent(cfg);channel=Channel();bot=types.SimpleNamespace(id=9,bot=True);requests=[];typing=[]
   class Typing:
    async def __aenter__(self):typing.append('started')
    async def __aexit__(self,*args):typing.append('stopped')
   channel.typing=lambda:Typing()
   def handler(req):
    self.assertEqual(typing,['started'])
    payload=json.loads(req.content);requests.append(payload);data=json.loads(payload['messages'][1]['content'])
    self.assertEqual(len(data['requests']),2)
    self.assertTrue(all(x['conversation']['private_memories']==[] and x['conversation']['private_profile'] is None for x in data['requests']))
    responses=[{'queue_id':x['queue_id'],'decision':'reply','text':'answer for '+x['conversation']['current_user_id']} for x in data['requests']]
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'responses':responses})}}]})
   async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    app.responder=Responder(Gateway(app.store,app.budget,cfg,client),app.adaptation);app.extractor=Extractor(app.store,app.memory,None)
    with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
     for mid,uid in [(10,11),(12,13)]:
      msg=types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=uid,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[bot],content='vin please answer me',attachments=[])
      await app.on_message(msg)
     with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
     await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight))
    self.assertEqual(len(requests),1);self.assertEqual({m.reference_id for m in channel.sent},{10,12})
    self.assertEqual(typing,['started','stopped'])
    self.assertEqual({m.content for m in channel.sent},{'answer for 11','answer for 13'})
    self.assertEqual(app.store.db.execute('SELECT count(*) FROM group_request_members').fetchone()[0],2)
    self.assertEqual(app.budget.status()['local_used'],1)
   await app.close()
 async def test_partial_group_response_leaves_omitted_person_pending(self):
  from vincent.response import GroupFormatError
  with tempfile.TemporaryDirectory() as directory:
   cfg=Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,group_direct=True)
   app=Vincent(cfg);channel=Channel();bot=types.SimpleNamespace(id=9,bot=True)
   class Partial:
    async def decide_group(self,contexts):return {contexts[0]['queue_id']:{'decision':'reply','text':'only first'}},None
   app.responder=Partial();app.extractor=Extractor(app.store,app.memory,None)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
    for mid,uid in [(10,11),(12,13)]:
     msg=types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=uid,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[bot],content='vin answer me please',attachments=[]);await app.on_message(msg)
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(len(channel.sent),1);remaining=app.store.db.execute("SELECT status,no_group FROM queue WHERE author_id='13'").fetchone();self.assertEqual(tuple(remaining),('pending',1));await app.close()
 async def test_invalid_group_output_requeues_each_without_grouping(self):
  with tempfile.TemporaryDirectory() as directory:
   cfg=Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,group_direct=True)
   app=Vincent(cfg);channel=Channel();bot=types.SimpleNamespace(id=9,bot=True)
   def handler(req):
    answer={'responses':[{'queue_id':'99999','decision':'reply','text':'wrong item'}]}
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})
   async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    app.responder=Responder(Gateway(app.store,app.budget,cfg,client),app.adaptation);app.extractor=Extractor(app.store,app.memory,None)
    with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
     for mid,uid in [(10,11),(12,13)]:
      msg=types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=uid,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[bot],content='vin answer me please',attachments=[]);await app.on_message(msg)
     with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
     await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight))
    self.assertEqual(channel.sent,[])
    self.assertEqual([tuple(r) for r in app.store.db.execute('SELECT status,no_group FROM queue ORDER BY id')],[('pending',1),('pending',1)])
    self.assertEqual(app.store.db.execute('SELECT count(*) FROM group_request_members').fetchone()[0],2);self.assertEqual(app.budget.status()['local_used'],1)
   await app.close()
 async def test_erasure_during_group_call_discards_all_affected_contexts(self):
  from vincent.privacy import erase_user
  with tempfile.TemporaryDirectory() as directory:
   cfg=Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,group_direct=True)
   app=Vincent(cfg);channel=Channel();bot=types.SimpleNamespace(id=9,bot=True);started=asyncio.Event();release=asyncio.Event()
   class WaitingResponder:
    async def decide_group(self,contexts):
     started.set();await release.wait()
     return {x['queue_id']:{'decision':'reply','text':'reply'} for x in contexts},None
   app.responder=WaitingResponder();app.extractor=Extractor(app.store,app.memory,None)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
    for mid,uid in [(10,11),(12,13)]:
     msg=types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=uid,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[bot],content='vin answer me please',attachments=[]);await app.on_message(msg)
    with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
    await app.worker.coro(app);await started.wait();erase_user(app.store,1,11);release.set();await asyncio.gather(*tuple(app.inflight))
   self.assertEqual(channel.sent,[])
   rows=[tuple(r) for r in app.store.db.execute('SELECT status FROM queue ORDER BY id')];self.assertEqual(rows,[('cancelled',),('pending',)])
   await app.close()
 def test_rejects_unknown_or_duplicate_queue_ids(self):
  with self.assertRaises(ValueError):parse_group('{"responses":[{"queue_id":"9","decision":"reply","text":"hi"}]}',['1'])
  with self.assertRaises(ValueError):parse_group('{"responses":[{"queue_id":"1","decision":"reply","text":"a"},{"queue_id":"1","decision":"reply","text":"b"}]}',['1'])
if __name__=='__main__':unittest.main()
