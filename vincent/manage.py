"""Local administrator commands; never starts Discord or calls a model."""
import argparse,json
from .config import Settings
from .store import Store
from .adaptation import Adaptation
from .experiments import Experiments


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',default='data/vincent-v2.sqlite3');sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('status');start=sub.add_parser('canary-start');start.add_argument('version',type=int);start.add_argument('--review',required=True);start.add_argument('--percent',type=int,default=10)
 show=sub.add_parser('canary-results');show.add_argument('experiment',type=int)
 end=sub.add_parser('canary-finish');end.add_argument('experiment',type=int);end.add_argument('--promote',action='store_true')
 rollback=sub.add_parser('activate');rollback.add_argument('version',type=int)
 sub.add_parser('core-only')
 sub.add_parser('uncertain-list')
 resolve=sub.add_parser('resolve-sent');resolve.add_argument('queue_id',type=int);resolve.add_argument('message_id',type=int)
 discard=sub.add_parser('discard-uncertain');discard.add_argument('queue_id',type=int)
 a=p.parse_args();s=Store(a.database);adapt=Adaptation(s);ex=Experiments(s)
 try:
  if a.command=='status':
   with s.lock:
    proposals=[dict(r) for r in s.db.execute('SELECT id,status,hash,created_at FROM prompt_versions ORDER BY id DESC LIMIT 20')]
    experiments=[dict(r) for r in s.db.execute('SELECT id,candidate_id,incumbent_id,state,created_at,finished_at FROM prompt_experiments ORDER BY id DESC LIMIT 10')]
   print(json.dumps({'active':adapt.active(),'versions':proposals,'experiments':experiments},indent=2))
  elif a.command=='canary-start':
   with open(a.review,encoding='utf8') as f:review=json.load(f)
   print('Experiment:',ex.start(a.version,review,canary_percent=a.percent))
  elif a.command=='canary-results':print(json.dumps(ex.results(a.experiment),indent=2))
  elif a.command=='canary-finish':print(json.dumps(ex.finish(a.experiment,promote=a.promote),indent=2))
  elif a.command=='activate':adapt.activate(a.version,approved=True);print('Activated version',a.version)
  elif a.command=='core-only':
   adapt.core_only()
   print('Returned to core-only guidance')
  elif a.command=='uncertain-list':
   with s.lock:rows=[dict(r) for r in s.db.execute("SELECT id,guild_id,channel_id,send_nonce,updated_at FROM queue WHERE status='uncertain' ORDER BY updated_at")]
   print(json.dumps(rows,indent=2))
  elif a.command=='resolve-sent':
   from .queue import WorkQueue
   if not WorkQueue(s).reconcile_sent(a.queue_id,a.message_id):raise ValueError('No matching uncertain item')
   print('Recorded verified Discord message ID')
  elif a.command=='discard-uncertain':
   with s.transaction() as db:
    changed=db.execute("UPDATE queue SET status='cancelled',reason='manual discard',send_text=NULL WHERE id=? AND status='uncertain'",(a.queue_id,)).rowcount
   if not changed:raise ValueError('No matching uncertain item')
   print('Discarded uncertain item without resending')
 finally:s.close()
if __name__=='__main__':main()
