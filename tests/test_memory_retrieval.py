import json
import tempfile
import time
import unittest
from pathlib import Path

from vincent.app import Vincent
from vincent.config import Settings
from vincent.context import build_context
from vincent.memory import Memory
from vincent.store import Store


class RetrievalTests(unittest.TestCase):
 def setUp(self):
  self.store=Store(':memory:');self.memory=Memory(self.store,clock=lambda:1000)
 def tearDown(self):self.store.close()
 def personal(self,guild,user,content,kind='interest'):
  self.store.db.execute("INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at) VALUES('user',?,?,?,?,?,'self_report',.9,1,1)",(str(guild),str(user),kind,content,'[]'))
 def message(self,mid,user,text,created,reply_to=None):
  self.store.message_event(kind='create',message_id=mid,guild_id='1',channel_id='2',author_id=user,content=text,created_at=created,changed_at=created,reply_to=reply_to)
 def test_matching_inflections_topics_and_recall_respects_scope(self):
  self.personal(1,3,'enjoys listening to music')
  self.personal(1,4,'enjoys listening to music')
  self.personal(9,3,'enjoys listening to music')
  self.assertEqual(len(self.memory.search(1,3,'what songs do I enjoy?')),1)
  self.assertEqual(len(self.memory.search(1,3,'what do you remember about me?')),1)
  self.assertEqual(self.memory.search(1,5,'songs'),[])
  self.memory.set_enabled(1,3,False)
  self.assertEqual(self.memory.search(1,3,'songs'),[])
 def test_context_uses_same_person_and_reply_parent_not_unrelated_speaker(self):
  self.personal(1,3,'enjoys chess')
  self.message('1','3','I played chess earlier',10)
  self.message('2','4','my cat is loud',11)
  self.message('3','3','vin what do you think?',12)
  item={'source_ids':json.dumps(['3']),'guild_id':'1','channel_id':'2','author_id':'3','priority':20,'reason':'direct'}
  self.assertEqual(self.memory.search(1,3,'vin what do you think?'),[])
  self.assertEqual(len(build_context(self.store,self.memory,item)['private_memories']),1)
  self.personal(1,3,'has a cat')
  contents=[m['content'] for m in build_context(self.store,self.memory,item)['private_memories']]
  self.assertNotIn('has a cat',contents)
 def test_shared_identity_survives_and_irrelevant_memories_are_filtered(self):
  self.memory.put_shared(scope='global',kind='identity',content='the owner is HMAN')
  self.memory.put_shared(scope='server',guild='1',kind='shared_bit',content='chess rivalry')
  self.memory.put_shared(scope='server',guild='2',kind='shared_bit',content='chess secret')
  results=self.memory.shared(1,query='chess')
  self.assertEqual([r['content'] for r in results],['the owner is HMAN','chess rivalry'])
  self.assertEqual([r['kind'] for r in self.memory.shared(1,query='unrelated')],['identity'])
 def test_old_memories_are_not_cut_off_at_200(self):
  self.personal(1,3,'likes astronomy')
  for i in range(210):self.personal(1,3,'unrelated topic '+str(i))
  self.assertEqual(self.memory.search(1,3,'astronomy')[0]['content'],'likes astronomy')


class GroupMemoryTests(unittest.IsolatedAsyncioTestCase):
 async def test_personal_memory_requeues_separately_without_sending_group_request(self):
  with tempfile.TemporaryDirectory() as directory:
   app=Vincent(Settings(database=Path(directory)/'db',channels=frozenset({2}),reactions_enabled=False,group_direct=True))
   now=time.time()
   for mid,uid,content in [(10,11,'vin chess?'),(12,13,'vin hi')]:
    app.store.message_event(kind='create',message_id=mid,guild_id=1,channel_id=2,author_id=uid,content=content,created_at=now,changed_at=now)
    app.queue.enqueue(guild_id=1,channel_id=2,author_id=uid,branch_id=mid,message_id=mid,priority=20)
   app.store.db.execute("INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at) VALUES('user','1','11','interest','likes chess','[]','self_report',.9,1,1)")
   items=[app.queue.claim(),app.queue.claim()]
   await app.process_group(items)
   self.assertEqual([tuple(r) for r in app.store.db.execute('SELECT status,no_group FROM queue ORDER BY id')],[('pending',1),('pending',1)])
   self.assertEqual(app.budget.status()['local_used'],0)
   await app.close()
