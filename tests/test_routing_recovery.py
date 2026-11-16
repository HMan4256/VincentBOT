import asyncio,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,PropertyMock,patch
from vincent.app import Vincent
from vincent.config import Settings
from vincent.extraction import Extractor

class RoutingRecoveryTests(unittest.IsolatedAsyncioTestCase):
 async def test_missing_reference_lookups_run_concurrently(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-15
   for mid,ref in [('10','8'),('11','9')]:
    app.store.message_event(kind='create',message_id=mid,guild_id='1',channel_id='2',author_id=mid,content='really?',created_at=ts,changed_at=ts,reply_to=ref,metadata={'mentions':[]})
   bot=types.SimpleNamespace(id=7,bot=True);both_started=asyncio.Event();release=asyncio.Event();started=[]
   async def fetch(mid):
    started.append(mid)
    if len(started)==2:both_started.set()
    await release.wait()
    return types.SimpleNamespace(id=mid,author=bot,content='what?',created_at=types.SimpleNamespace(timestamp=lambda:ts-2),reference=None,mentions=[],attachments=[],embeds=[],stickers=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=types.SimpleNamespace(fetch_message=fetch)):
    task=asyncio.create_task(app.recover_unrouted())
    try:await asyncio.wait_for(both_started.wait(),timeout=1)
    finally:release.set()
    self.assertEqual(await task,2)
   self.assertEqual(set(started),{8,9});self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],2)
   await app.close()
 async def test_lookup_that_outlives_freshness_does_not_extend_request_ttl(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));clock=[time.time()];ts=clock[0]-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='really?',created_at=ts,changed_at=ts,reply_to='8',metadata={'mentions':[]})
   bot=types.SimpleNamespace(id=9,bot=True)
   original=types.SimpleNamespace(id=8,author=bot,content='what?',created_at=types.SimpleNamespace(timestamp=lambda:ts-2),reference=None,mentions=[],attachments=[],embeds=[],stickers=[])
   async def slow_fetch(_):clock[0]+=110;return original
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=types.SimpleNamespace(fetch_message=slow_fetch)),patch('vincent.app.time.time',side_effect=lambda:clock[0]):
    self.assertEqual(await app.recover_unrouted(),0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
   self.assertEqual(app.store.db.execute("SELECT routing_state FROM messages WHERE message_id='10'").fetchone()[0],'done')
   self.assertEqual(app.store.db.execute("SELECT count(*) FROM events WHERE kind='recovery_expired'").fetchone()[0],1)
   await app.close()
 async def test_old_create_does_not_receive_a_fresh_queue_lifetime(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-130;bot=types.SimpleNamespace(id=9,bot=True)
   msg=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=types.SimpleNamespace(id=2),author=types.SimpleNamespace(id=11,bot=False),created_at=types.SimpleNamespace(timestamp=lambda:ts),reference=None,mentions=[bot],content='vin answer',attachments=[],embeds=[],stickers=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):await app.on_message(msg)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
   self.assertEqual(app.store.db.execute("SELECT count(*) FROM events WHERE kind='route_expired'").fetchone()[0],1)
   await app.close()
 async def test_edit_can_turn_ignored_message_into_direct_request_without_duplicates(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-15
   bot=types.SimpleNamespace(id=9,bot=True);author=types.SimpleNamespace(id=11,bot=False)
   msg=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=types.SimpleNamespace(id=2),author=author,created_at=types.SimpleNamespace(timestamp=lambda:ts),reference=None,mentions=[],content='ordinary conversation',attachments=[],embeds=[],stickers=[])
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):
    await app.on_message(msg)
    self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
    msg.content='vin please answer';msg.mentions=[bot];msg.edited_at=types.SimpleNamespace(timestamp=lambda:ts+1)
    await app.on_message_edit(None,msg)
    self.assertEqual(await app.recover_unrouted(),1)
    msg.content='vin please answer this';msg.edited_at=types.SimpleNamespace(timestamp=lambda:ts+2)
    await app.on_message_edit(None,msg)
    self.assertEqual(await app.recover_unrouted(),0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],1)
   self.assertEqual(app.store.db.execute('SELECT status FROM queue').fetchone()[0],'pending')
   await app.close()
 async def test_delayed_create_cannot_execute_command_from_before_edit(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-15
   app.store.message_event(kind='edit',message_id='10',guild_id='1',channel_id='2',author_id='11',content='vin can you answer?',created_at=ts,changed_at=ts+3,metadata={'mentions':['9']})
   user=types.SimpleNamespace(id=11,bot=False)
   msg=types.SimpleNamespace(id=10,guild=types.SimpleNamespace(id=1),channel=types.SimpleNamespace(id=2),author=user,created_at=types.SimpleNamespace(timestamp=lambda:ts),reference=None,mentions=[],content='!vin data forget',attachments=[],embeds=[],stickers=[],reply=AsyncMock())
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)):
    await app.on_message(msg)
    msg.reply.assert_not_awaited()
    self.assertEqual(await app.recover_unrouted(),1)
   row=app.store.db.execute("SELECT content,deleted FROM messages WHERE message_id='10'").fetchone()
   self.assertEqual(tuple(row),('vin can you answer?',0))
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],1)
   await app.close()
 async def test_unknown_deletion_tombstone_blocks_delayed_create_and_edit(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));now=time.time()
   await app._apply_delete(2,1,123)
   args=dict(message_id=123,guild_id=1,channel_id=2,author_id=11,content='deleted content',created_at=now-10,metadata={})
   self.assertFalse(app.store.message_event(kind='create',changed_at=now-10,**args))
   self.assertFalse(app.store.message_event(kind='edit',changed_at=now+1,**args))
   row=app.store.db.execute('SELECT author_id,content,deleted,routing_state FROM messages WHERE message_id=?',('123',)).fetchone()
   self.assertEqual(tuple(row),('unknown',None,1,'done'))
   self.assertNotIn('deleted content',str([tuple(r) for r in app.store.db.execute('SELECT payload FROM events')]))
   await app.close()
 async def test_logged_request_is_recovered_once_after_crash(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=True));app.extractor=Extractor(app.store,app.memory,None)
   ts=time.time()-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='vin please answer me',created_at=ts,changed_at=ts,metadata={'mentions':['9'],'author_bot':False})
   bot=types.SimpleNamespace(id=9,bot=True)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot):
    self.assertEqual(await app.recover_unrouted(),1);self.assertEqual(await app.recover_unrouted(),0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],1)
   self.assertEqual(app.store.db.execute('SELECT incoming_count FROM user_profiles').fetchone()[0],1)
   self.assertEqual(app.store.db.execute('SELECT routing_state FROM messages').fetchone()[0],'done');await app.close()
 async def test_previously_enqueued_message_does_not_duplicate_after_recovery(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=True));app.extractor=Extractor(app.store,app.memory,None);ts=time.time()-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='vin please answer me',created_at=ts,changed_at=ts,metadata={'mentions':['9']})
   app.queue.enqueue(guild_id=1,channel_id=2,author_id=11,branch_id='user:11',message_id=10)
   app.profiles.incoming(1,11,ts,message_id=10)
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)):
    await app.recover_unrouted()
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],1);self.assertEqual(app.store.db.execute('SELECT incoming_count FROM user_profiles').fetchone()[0],1);await app.close()
 async def test_missing_reply_reference_is_fetched_and_recovers_direct_request(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='really?',created_at=ts,changed_at=ts,reply_to='8',metadata={'mentions':[]})
   bot=types.SimpleNamespace(id=9,bot=True)
   original=types.SimpleNamespace(id=8,author=bot,content='what?',created_at=types.SimpleNamespace(timestamp=lambda:ts-2),reference=None,mentions=[],attachments=[],embeds=[],stickers=[])
   channel=types.SimpleNamespace(fetch_message=AsyncMock(return_value=original))
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
    self.assertEqual(await app.recover_unrouted(),1)
   self.assertEqual(app.store.db.execute('SELECT priority FROM queue').fetchone()[0],20)
   channel.fetch_message.assert_awaited_once_with(8)
   await app.close()
 async def test_deletion_during_reference_fetch_cannot_recreate_queue_work(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=True));app.extractor=Extractor(app.store,app.memory,None);ts=time.time()-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='really?',created_at=ts,changed_at=ts,reply_to='8',metadata={'mentions':[]})
   started=asyncio.Event();release=asyncio.Event();bot=types.SimpleNamespace(id=9,bot=True)
   original=types.SimpleNamespace(id=8,author=bot,content='what?',created_at=types.SimpleNamespace(timestamp=lambda:ts-2),reference=None,mentions=[],attachments=[],embeds=[],stickers=[])
   async def fetch(_):started.set();await release.wait();return original
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=types.SimpleNamespace(fetch_message=fetch)):
    task=asyncio.create_task(app.recover_unrouted());await started.wait()
    await app._apply_delete(2,1,10);release.set()
    self.assertEqual(await task,0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM user_profiles').fetchone()[0],0)
   self.assertIsNone(app.store.db.execute("SELECT 1 FROM messages WHERE message_id='8'").fetchone())
   await app.close()
 async def test_unavailable_reference_remains_pending_until_retry(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),memory_enabled=False));ts=time.time()-15
   app.store.message_event(kind='create',message_id='10',guild_id='1',channel_id='2',author_id='11',content='really?',created_at=ts,changed_at=ts,reply_to='8',metadata={'mentions':[]})
   with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=types.SimpleNamespace(id=9,bot=True)),patch.object(app,'get_channel',return_value=None):
    self.assertEqual(await app.recover_unrouted(),0)
   self.assertEqual(app.store.db.execute('SELECT routing_state FROM messages').fetchone()[0],'pending')
   await app.close()
if __name__=='__main__':unittest.main()
