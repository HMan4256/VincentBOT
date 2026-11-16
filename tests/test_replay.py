import json,tempfile,unittest
from pathlib import Path
from evaluation.replay_routing import replay,BOT

class ReplayTests(unittest.TestCase):
 def test_replay_routes_historical_explicit_and_followup(self):
  def msg(mid,author,text,seconds,ref=None,bot=False):
   return {'id':str(mid),'channel_id':'1','timestamp':f'2026-11-15T12:00:{seconds:02d}+00:00','content':text,'author':{'id':str(author),'bot':bot},'message_reference':{'message_id':str(ref)} if ref else None,'mentions':[]}
  data=[msg(1,'11','hi vin',0),msg(2,BOT,'hi',1,1,True),msg(3,'11','unfortunately',2),msg(4,'12','unrelated',3,1)]
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'archive.json';p.write_text(json.dumps(data));r=replay(p,'2026-11-15')
  self.assertEqual(r['counts']['reason_name_mentioned'],1);self.assertEqual(r['counts']['reason_recent_participant_followup'],1);self.assertEqual(r['counts']['reason_reply_to_other'],1)
if __name__=='__main__':unittest.main()

class QueueReplayTests(unittest.TestCase):
 def test_busy_requests_respect_call_budget_without_network(self):
  from evaluation.replay_queue import simulate
  def msg(mid,author,seconds):
   return {'id':str(mid),'channel_id':'1','timestamp':f'2026-11-15T12:00:{seconds:02d}+00:00','content':'vin please help me','author':{'id':str(author)},'mentions':[],'message_reference':None}
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'archive.json';p.write_text(json.dumps([msg(1,11,0),msg(2,12,1),msg(3,13,2)]))
   report=simulate(p,'2026-11-15',latency=3,day_limit=2,minute_limit=10)
  self.assertEqual(report['counts']['candidate_messages'],3)
  self.assertEqual(report['counts']['model_calls'],2)
  self.assertEqual(report['queue_statuses']['expired'],1)
