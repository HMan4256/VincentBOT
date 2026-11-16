import asyncio,datetime as dt,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import PropertyMock,patch
import httpx
from vincent.app import Vincent
from vincent.budget import Budget
from vincent.config import Settings
from vincent.extraction import Extractor,EXTRACTION_PROMPT
from vincent.feedback import Feedback
from vincent.llm import Gateway
from vincent.response import Responder

class Channel:
 id=2
 def __init__(self):self.sent=[]
 def get_partial_message(self,mid):return types.SimpleNamespace(id=mid)
 async def send(self,text,**kwargs):
  item=types.SimpleNamespace(id=200,content=text,created_at=dt.datetime.now(dt.timezone.utc),reactions_added=[])
  async def add_reaction(emoji):item.reactions_added.append(emoji)
  item.add_reaction=add_reaction;self.sent.append(item);return item

class FullPipelineTests(unittest.IsolatedAsyncioTestCase):
 async def test_queue_model_reactions_and_memory_share_one_budgeted_pipeline(self):
  with tempfile.TemporaryDirectory() as directory:
   settings=Settings(database=Path(directory)/'v2.db',channels=frozenset({2}),api_key='secret',reactions_enabled=True,memory_enabled=True)
   app=Vincent(settings);channel=Channel();bot=types.SimpleNamespace(id=9,bot=True)
   def handler(request):
    payload=json.loads(request.content);system=payload['messages'][0]['content']
    if system==EXTRACTION_PROMPT:
     batch=json.loads(payload['messages'][1]['content']);answer={'proposals':[{'user_id':'11','kind':'interest','content':'likes playing chess','source_ids':[batch[0]['message_id']],'source_type':'self_report','confidence':.9}],'server_proposals':[]}
    else:answer={'decision':'reply','text':'your chess pieces are filing complaints'}
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}],'usage':{'total_tokens':42}})
   async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    gateway=Gateway(app.store,app.budget,settings,client);app.responder=Responder(gateway,app.adaptation);app.extractor=Extractor(app.store,app.memory,gateway)
    with patch.object(Vincent,'user',new_callable=PropertyMock,return_value=bot),patch.object(app,'get_channel',return_value=channel):
     for mid in range(10,18):
      msg=types.SimpleNamespace(id=mid,guild=types.SimpleNamespace(id=1),channel=channel,author=types.SimpleNamespace(id=11,bot=False),created_at=dt.datetime.now(dt.timezone.utc),reference=None,mentions=[bot],content='vin i like playing chess',attachments=[])
      await app.on_message(msg)
     with app.store.transaction() as db:db.execute('UPDATE queue SET updated_at=updated_at-10')
     await app.worker.coro(app);await asyncio.gather(*tuple(app.inflight))
     self.assertEqual(len(channel.sent),1);self.assertEqual(channel.sent[0].reactions_added,['👍','👎'])
     payload=types.SimpleNamespace(channel_id=2,guild_id=1,user_id=12,message_id=200,emoji='👍',member=types.SimpleNamespace(bot=False))
     await app.on_raw_reaction_add(payload)
     self.assertEqual(Feedback(app.store,9).votes(200),{'12':'positive'})
     self.assertEqual(await app.extractor.run_batch(),1)
   self.assertEqual(len(app.memory.search(1,11,'chess')),1)
   self.assertEqual(app.budget.status()['local_used'],2)
   self.assertEqual(app.store.db.execute('SELECT count(*) FROM requests_log').fetchone()[0],2)
   await app.close()
if __name__=='__main__':unittest.main()
