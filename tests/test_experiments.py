import unittest
from vincent.store import Store
from vincent.adaptation import Adaptation
from vincent.experiments import Experiments
from vincent.feedback import Feedback
from vincent.queue import WorkQueue

class ExperimentTests(unittest.TestCase):
 def setUp(self):
  self.s=Store(':memory:');self.a=Adaptation(self.s);self.e=Experiments(self.s);self.q=WorkQueue(self.s);self.f=Feedback(self.s,999)
  self.candidate=self.a.propose('Use sharper context-specific roasts.',{'sample_size':10,'distinct_raters':3,'hypothesis':'better','source_ids':['1']})
  self.evaluation={'human_reviewed':True,'scenario_count':8,'passed_categories':['roast','targeting','boundaries','repetition']}
 def tearDown(self):self.s.close()
 def test_requires_offline_review_and_preserves_assignments(self):
  with self.assertRaises(ValueError):self.e.start(self.candidate,{})
  experiment=self.e.start(self.candidate,self.evaluation);qid=self.q.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id=4,message_id=5)
  first=self.e.assign(qid);self.assertEqual(self.e.assign(qid),first)
  with self.assertRaises(ValueError):self.e.finish(experiment,promote=True)
  self.assertEqual(self.s.db.execute('SELECT state FROM prompt_experiments WHERE id=?',(experiment,)).fetchone()[0],'running')
 def test_canary_promotion_requires_diverse_votes_and_keeps_version_history(self):
  ex=self.e.start(self.candidate,self.evaluation)
  for i in range(40):
   variant='candidate' if i<20 else 'control';qid=self.q.enqueue(guild_id=1,channel_id=i,author_id=i,branch_id=i,message_id=i);claim=self.q.claim();self.q.finish(qid,'sent',lease_token=claim['lease_token'],response_message_id=1000+i)
   with self.s.transaction() as db:db.execute('INSERT INTO prompt_assignments(queue_id,experiment_id,variant,prompt_version,assigned_at) VALUES(?,?,?,?,?)',(qid,ex,variant,self.candidate if variant=='candidate' else None,0))
   self.f.update(1000+i,i%5,'👍',True)
  result=self.e.finish(ex,promote=True);self.assertEqual(result['candidate']['positive'],20);self.assertEqual(self.a.active()['id'],self.candidate)
  self.assertEqual(self.s.db.execute('SELECT state FROM prompt_experiments WHERE id=?',(ex,)).fetchone()[0],'promoted')
 def test_assessment_rejects_large_negative_canary_regression(self):
  ex=self.e.start(self.candidate,self.evaluation)
  for i in range(20):
   variant='candidate' if i<10 else 'control';qid=self.q.enqueue(guild_id=1,channel_id=i,author_id=i,branch_id=i,message_id=i);claim=self.q.claim();self.q.finish(qid,'sent',lease_token=claim['lease_token'],response_message_id=2000+i)
   with self.s.transaction() as db:db.execute('INSERT INTO prompt_assignments(queue_id,experiment_id,variant,prompt_version,assigned_at) VALUES(?,?,?,?,?)',(qid,ex,variant,self.candidate if variant=='candidate' else None,0))
   self.f.update(2000+i,i%3,'👎' if variant=='candidate' else '👍',True)
  self.assertEqual(self.e.assess(ex),'rejected_regression');self.assertIsNone(self.a.active())
if __name__=='__main__':unittest.main()
