import unittest
from vincent.store import Store
from vincent.router import Router
from vincent.conversations import bot_ancestor

class ConversationTests(unittest.TestCase):
 def test_recent_bot_chain_is_candidate_but_old_or_unrelated_is_not(self):
  s=Store(':memory:')
  for mid,author,parent,t in [('1','9',None,10),('2','4','1',11),('3','5',None,12)]:
   s.message_event(kind='create',message_id=mid,guild_id='7',channel_id='8',author_id=author,content='hello',created_at=t,changed_at=t,reply_to=parent)
  a=bot_ancestor(s,parent_id='2',channel_id='8',guild_id='7',bot_id='9',now=20)
  self.assertEqual(a,'1');self.assertIsNone(bot_ancestor(s,parent_id='2',channel_id='8',guild_id='7',bot_id='9',now=200))
  r=Router('9').route({'message_id':'4','author_id':'6','created_at':20,'content':'fair','reply_to':'2'},referenced={'author_id':'4'},bot_ancestor=a)
  self.assertEqual((r.action,r.reason),('candidate','active_bot_branch'));s.close()
if __name__=='__main__':unittest.main()
