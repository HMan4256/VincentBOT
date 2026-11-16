import unittest
from vincent.store import Store
from vincent.memory import Memory
from vincent.extraction import Extractor
from vincent.provenance import invalidate_source

class ProvenanceTests(unittest.TestCase):
 def test_edited_source_removes_derived_memory_and_reopens_candidate(self):
  s=Store(':memory:');m=Memory(s);e=Extractor(s,m,None)
  s.message_event(kind='create',message_id=1,guild_id=2,channel_id=3,author_id=4,content='i enjoy playing chess',created_at=1,changed_at=1)
  e.collect(2,4,1);m.apply_user_proposals(2,4,0,[{'kind':'interest','content':'enjoys chess','source_ids':['1'],'source_type':'self_report','confidence':1}])
  with s.transaction() as db:db.execute("UPDATE memory_candidates SET state='processed'")
  s.message_event(kind='edit',message_id=1,guild_id=2,channel_id=3,author_id=4,content='i enjoy playing checkers',created_at=1,changed_at=2)
  self.assertEqual(invalidate_source(s,1,deleted=False),1);self.assertEqual(m.search(2,4,'chess'),[])
  self.assertEqual(s.db.execute('SELECT state FROM memory_candidates').fetchone()[0],'pending')
  self.assertEqual(invalidate_source(s,1,deleted=True),0);self.assertEqual(s.db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0],0);s.close()
 def test_invalidated_source_redacts_guidance_derived_from_its_reply(self):
  from vincent.queue import WorkQueue
  from vincent.adaptation import Adaptation
  s=Store(':memory:');q=WorkQueue(s);a=Adaptation(s)
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='old request',created_at=1,changed_at=1)
  item=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=1,message_id=1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id='10')
  version=a.propose('Keep responses grounded in the setup.',{'sample_size':10,'distinct_raters':3,'hypothesis':'fit','source_ids':['10']});a.activate(version,approved=True)
  invalidate_source(s,'1',deleted=True)
  self.assertIsNone(a.active());self.assertEqual(s.db.execute('SELECT status FROM prompt_versions WHERE id=?',(version,)).fetchone()[0],'redacted');s.close()
if __name__=='__main__':unittest.main()
