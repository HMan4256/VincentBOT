"""Synthetic memory evaluation; explicit --openrouter uses one provider request."""
import argparse,asyncio,json,tempfile
from pathlib import Path
import httpx
from dotenv import load_dotenv
from vincent.budget import Budget
from vincent.config import Settings
from vincent.extraction import Extractor
from vincent.llm import Gateway
from vincent.memory import Memory
from vincent.store import Store

ROWS=[('10','i enjoy playing chess every weekend'),('10','my friend loves collecting rare stamps'),('10','i am 19 years old'),('10','vin your chess skills are terrible'),('10','vin you play worse than my toaster'),('11','the server game night is friday'),('10','yes our server game night is friday'),('11','i am learning to paint landscapes')]

async def evaluate(gateway_factory,store):
 memory=Memory(store);extractor=Extractor(store,memory,gateway_factory(store));
 for i,(uid,text) in enumerate(ROWS,1):
  store.message_event(kind='create',message_id=str(i),guild_id='1',channel_id='2',author_id=uid,content=text,created_at=i,changed_at=i)
  extractor.collect(1,uid,i,interaction=True)
 await extractor.run_batch()
 records=[dict(r) for r in store.db.execute('SELECT scope,user_id,kind,content,source_ids,observed_at FROM memories ORDER BY id')]
 def has(scope,user,kind,word):return any(r['scope']==scope and r['user_id']==user and r['kind']==kind and word in r['content'].lower() for r in records)
 checks={'chess_attributed_to_speaker':has('user','10','interest','chess'),
         'age_attributed_to_speaker':has('user','10','age','19'),
         'painting_attributed_to_speaker':has('user','11','interest','paint'),
         'shared_event_supported':any(r['scope']=='server' and 'friday' in r['content'].lower() for r in records),
         'friends_hobby_not_attributed_to_user':not any(r['scope']=='user' and r['user_id']=='10' and 'stamp' in r['content'].lower() for r in records)}
 return {'checks':checks,'records':len(records),'note':'One small synthetic batch, not a representative accuracy benchmark.'}

async def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--openrouter',action='store_true');p.add_argument('--dotenv',default='.env');args=p.parse_args()
 if not args.openrouter:p.error('This script requires --openrouter; it sends synthetic test messages to the configured provider')
 load_dotenv(args.dotenv);cfg=Settings.from_env()
 with tempfile.TemporaryDirectory() as d:
  store=Store(Path(d)/'test.sqlite')
  async with httpx.AsyncClient(timeout=35) as client:
   try:
    result=await evaluate(lambda db:Gateway(db,Budget(db,cfg),cfg,client),store)
    result['request_attempts']=store.db.execute('SELECT count(*) FROM request_attempts').fetchone()[0]
    print(json.dumps(result,indent=2))
   finally:store.close()
if __name__=='__main__':asyncio.run(main())
