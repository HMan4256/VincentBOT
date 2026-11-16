import unittest
import json
from vincent.context import build_context
from vincent.memory import Memory
from vincent.semantic_memory import SemanticMemory
from vincent.store import Store


class RealSemanticMemoryTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.store=Store(':memory:')
  try:cls.engine=SemanticMemory(cls.store)
  except (ImportError, OSError, ValueError) as exc:
   cls.store.close();raise unittest.SkipTest('Local semantic model unavailable: '+str(exc))
  cls.memory=Memory(cls.store);cls.memory.semantic=cls.engine
 @classmethod
 def tearDownClass(cls):cls.store.close()
 def setUp(self):
  self.store.db.execute('DELETE FROM memories')
 def add(self,content,user='1'):
  return self.store.db.execute("INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at) VALUES('user','1',?,'fact',?,'[]','self_report',.9,1,1)",(user,content)).lastrowid
 def test_semantic_paraphrases_rank_relevant_facts_above_distractors(self):
  facts=['I live in Prague','I have two cats','I love playing chess','my favourite music is metal','I work as a nurse']
  for fact in facts:self.add(fact)
  questions=['Which city am I from?','How many pets do I own?','Want to play a board game?','What bands do I listen to?','What is my profession?']
  for expected,question in zip(facts,questions):
   with self.subTest(question=question):
    self.assertEqual(self.memory.search('1','1',question)[0]['content'],expected)
 def test_cached_vectors_follow_correction_deletion_and_scope(self):
  mid=self.add('I have two cats')
  self.add('I live in Prague',user='2')
  self.assertEqual(self.memory.search('1','1','How many pets do I own?')[0]['id'],mid)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_embeddings').fetchone()[0],1)
  self.store.db.execute('UPDATE memories SET content=? WHERE id=?',('I live in London',mid))
  self.assertEqual(self.memory.search('1','1','Which city am I from?')[0]['content'],'I live in London')
  self.store.db.execute('DELETE FROM memories WHERE id=?',(mid,))
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_embeddings').fetchone()[0],0)
  self.assertEqual(self.memory.search('1','1','Which city am I from?'),[])
 def test_multilingual_query(self):
  self.add('I live in Prague')
  self.add('I have two cats')
  self.assertEqual(self.memory.search('1','1','V jakém městě bydlím?')[0]['content'],'I live in Prague')
 def test_semantic_result_reaches_reply_context(self):
  self.add('I live in Prague')
  self.store.message_event(kind='create',message_id='99',guild_id='1',channel_id='2',author_id='1',content='Which city am I from?',created_at=10,changed_at=10)
  item={'source_ids':json.dumps(['99']),'guild_id':'1','channel_id':'2','author_id':'1','priority':20,'reason':'direct'}
  context=build_context(self.store,self.memory,item)
  self.assertEqual(context['private_memories'][0]['content'],'I live in Prague')
  self.assertIn(context['private_memories'][0]['id'],context['_memory_versions'])
