import json,unittest
from vincent.store import Store
from vincent.feedback import Feedback
from vincent.adaptation import Adaptation
from vincent.queue import WorkQueue

class DraftGateway:
 def __init__(self):self.calls=0
 async def complete(self,**kwargs):
  self.calls+=1
  return json.dumps({'guidance':'Vary the form of a comeback based on the exact setup.','hypothesis':'Contextual variety improves responses'}),'req'

class AdaptationPipelineTests(unittest.IsolatedAsyncioTestCase):
 async def test_draft_requires_diverse_real_traceable_votes_and_stays_inactive(self):
  s=Store(':memory:');a=Adaptation(s);f=Feedback(s,99);q=WorkQueue(s);g=DraftGateway()
  with self.assertRaises(ValueError):await a.draft_from_feedback(g)
  self.assertEqual(g.calls,0)
  for i in range(12):
   item=q.enqueue(guild_id=1,channel_id=i+1,author_id=i+20,branch_id=i+1,message_id=i+1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id=str(100+i))
   s.message_event(kind='create',message_id=str(i+1),guild_id=1,channel_id=i+1,author_id=i+20,content='fictional request',created_at=i,changed_at=i)
   s.message_event(kind='create',message_id=str(100+i),guild_id=1,channel_id=i+1,author_id=99,content='fictional response',created_at=i,changed_at=i)
   f.update(100+i,(i%3)+1,'👍',True)
  proposal=await a.draft_from_feedback(g)
  self.assertEqual(g.calls,1);self.assertIsNone(a.active());self.assertEqual(s.db.execute('SELECT status FROM prompt_versions WHERE id=?',(proposal,)).fetchone()[0],'proposed')
  with self.assertRaisesRegex(ValueError,'already used'):await a.draft_from_feedback(g)
  self.assertEqual(g.calls,1)
  from vincent.experiments import Experiments
  experiments=Experiments(s);evaluation={'human_reviewed':True,'scenario_count':8,'passed_categories':['roast','targeting','boundaries','repetition']}
  f.update(100,1,'👍',False)
  with self.assertRaisesRegex(ValueError,'feedback has changed'):experiments.start(proposal,evaluation)
  with self.assertRaisesRegex(ValueError,'feedback has changed'):a.activate(proposal,approved=True)
  f.update(100,1,'👍',True);experiment=experiments.start(proposal,evaluation)
  f.update(100,1,'👍',False)
  self.assertEqual(experiments.assess(experiment,auto_promote=True),'rejected_stale_evidence')
  self.assertEqual(s.db.execute('SELECT state FROM prompt_experiments WHERE id=?',(experiment,)).fetchone()[0],'rejected')
  s.close()
 async def test_feedback_removed_during_proposal_invalidates_candidate(self):
  s=Store(':memory:');a=Adaptation(s);f=Feedback(s,99);q=WorkQueue(s)
  for i in range(12):
   item=q.enqueue(guild_id=1,channel_id=i+1,author_id=i+20,branch_id=i+1,message_id=i+1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id=str(100+i))
   s.message_event(kind='create',message_id=str(i+1),guild_id=1,channel_id=i+1,author_id=i+20,content='fictional request',created_at=i,changed_at=i)
   s.message_event(kind='create',message_id=str(100+i),guild_id=1,channel_id=i+1,author_id=99,content='fictional response',created_at=i,changed_at=i)
   f.update(100+i,(i%3)+1,'👍',True)
  class ChangingGateway:
   async def complete(self,**kwargs):
    f.update(100,1,'👍',False)
    return json.dumps({'guidance':'Vary responses to fit the context.','hypothesis':'variety'}),'req'
  with self.assertRaises(ValueError):await a.draft_from_feedback(ChangingGateway())
  self.assertEqual(s.db.execute('SELECT count(*) FROM prompt_versions').fetchone()[0],0);s.close()
 async def test_request_edited_during_proposal_invalidates_candidate(self):
  s=Store(':memory:');a=Adaptation(s);f=Feedback(s,99);q=WorkQueue(s)
  for i in range(12):
   item=q.enqueue(guild_id=1,channel_id=i+1,author_id=i+20,branch_id=i+1,message_id=i+1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id=str(100+i))
   s.message_event(kind='create',message_id=str(i+1),guild_id=1,channel_id=i+1,author_id=i+20,content='fictional request',created_at=i,changed_at=i)
   s.message_event(kind='create',message_id=str(100+i),guild_id=1,channel_id=i+1,author_id=99,content='fictional response',created_at=i,changed_at=i)
   f.update(100+i,(i%3)+1,'👍',True)
  class EditingGateway:
   async def complete(self,**kwargs):
    s.message_event(kind='edit',message_id='1',guild_id=1,channel_id=1,author_id=20,content='corrected request',created_at=0,changed_at=99)
    return json.dumps({'guidance':'Use context specific reactions.','hypothesis':'fit'}),'req'
  with self.assertRaises(ValueError):await a.draft_from_feedback(EditingGateway())
  self.assertEqual(s.db.execute('SELECT count(*) FROM prompt_versions').fetchone()[0],0);s.close()
 async def test_rated_response_edited_during_proposal_invalidates_candidate(self):
  s=Store(':memory:');a=Adaptation(s);f=Feedback(s,99);q=WorkQueue(s)
  for i in range(12):
   item=q.enqueue(guild_id=1,channel_id=i+1,author_id=i+20,branch_id=i+1,message_id=i+1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id=str(100+i))
   s.message_event(kind='create',message_id=str(i+1),guild_id=1,channel_id=i+1,author_id=i+20,content='fictional request',created_at=i,changed_at=i)
   s.message_event(kind='create',message_id=str(100+i),guild_id=1,channel_id=i+1,author_id=99,content='fictional response',created_at=i,changed_at=i)
   f.update(100+i,(i%3)+1,'👍',True)
  class EditingGateway:
   async def complete(self,**kwargs):
    s.message_event(kind='edit',message_id='100',guild_id=1,channel_id=1,author_id=99,content='edited response',created_at=0,changed_at=99)
    return json.dumps({'guidance':'Use context specific reactions.','hypothesis':'fit'}),'req'
  with self.assertRaises(ValueError):await a.draft_from_feedback(EditingGateway())
  self.assertEqual(s.db.execute('SELECT count(*) FROM prompt_versions').fetchone()[0],0);s.close()
if __name__=='__main__':unittest.main()
