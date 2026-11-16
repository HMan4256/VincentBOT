import json,unittest
from evaluation.memory_smoke import evaluate
from vincent.store import Store

class MemoryEvaluationTests(unittest.IsolatedAsyncioTestCase):
 async def test_conservative_filter_blocks_a_false_friend_hobby(self):
  s=Store(':memory:')
  class Gateway:
   async def complete(self,**kwargs):
    return json.dumps({'proposals':[{'user_id':'10','kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9},{'user_id':'10','kind':'interest','content':'collects stamps','source_ids':['2'],'source_type':'self_report','confidence':.5}]}),'req'
  result=await evaluate(lambda _:Gateway(),s)
  self.assertTrue(result['checks']['chess_attributed_to_speaker'])
  self.assertTrue(result['checks']['friends_hobby_not_attributed_to_user']);s.close()
if __name__=='__main__':unittest.main()
