"""Process the current memory backlog now; the regular worker keeps its schedule."""
import asyncio
import json
import os
import time
import uuid

import httpx
from dotenv import load_dotenv

from .budget import Budget, BudgetExceeded
from .config import Settings
from .extraction import Extractor
from .llm import Gateway, ModelError, ProviderRateLimited
from .memory import Memory
from .store import Store


def snapshot_pending(store, cutoff):
    with store.lock:
        return [r['message_id'] for r in store.db.execute('''SELECT c.message_id FROM memory_candidates c
            JOIN messages m ON m.message_id=c.message_id
            LEFT JOIN memory_preferences p ON p.guild_id=c.guild_id AND p.user_id=c.user_id
            WHERE c.state='pending' AND c.created_at<=? AND m.deleted=0
              AND COALESCE(p.enabled,1)=1 AND COALESCE(p.auto_enabled,1)=1
              AND c.generation=COALESCE(p.generation,0)
            ORDER BY c.interaction DESC,c.created_at''',(cutoff,)).fetchall()]


def claim(store, message_ids, state):
    with store.transaction() as db:
        claimed=[]
        for mid in message_ids:
            if db.execute("UPDATE memory_candidates SET state=? WHERE message_id=? AND state='pending'",(state,mid)).rowcount:
                claimed.append(mid)
        return claimed


def release(store, state):
    with store.transaction() as db:
        return db.execute("UPDATE memory_candidates SET state='pending' WHERE state=?",(state,)).rowcount


async def run():
    load_dotenv(os.getenv('VINCENT_ENV_FILE','.env'))
    settings=Settings.from_env()
    if not settings.api_key: raise ValueError('OPENROUTER_API_KEY is missing')
    store=Store(settings.database);cutoff=time.time();initial=snapshot_pending(store,cutoff)
    processed=0;created=0;reason=None
    batch_size=15
    print(json.dumps({'event':'start','messages':len(initial),'batch_size':batch_size}),flush=True)
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            extractor=Extractor(store,Memory(store),Gateway(store,Budget(store,settings),settings,client))
            # Wait for a scheduled extraction already in progress. Individual
            # batches are then claimed so later worker runs select other rows.
            while True:
                with store.lock:
                    busy=store.db.execute("SELECT 1 FROM request_attempts WHERE kind='memory' AND status='reserved' AND reserved_at>? LIMIT 1",(time.time()-300,)).fetchone()
                if not busy:break
                await asyncio.sleep(5)
            chunks=asyncio.Queue()
            for offset in range(0,len(initial),batch_size):chunks.put_nowait(initial[offset:offset+batch_size])
            stopped=asyncio.Event()
            async def worker():
                nonlocal processed,created,reason
                while not stopped.is_set():
                    try: batch=chunks.get_nowait()
                    except asyncio.QueueEmpty:return
                    state='manual:'+uuid.uuid4().hex
                    ids=claim(store,batch,state)
                    if not ids:continue
                    try:
                        retries=0
                        while True:
                            try:
                                added=await extractor.run_batch(minimum=1,maximum=30,candidate_state=state)
                                created+=added
                                with store.lock:
                                    done=store.db.execute('SELECT count(*) FROM memory_candidates WHERE message_id IN ('+','.join('?' for _ in ids)+") AND state='processed'",ids).fetchone()[0]
                                processed+=done
                                print(json.dumps({'event':'batch','reviewed':processed,'of':len(initial),'new_memories':created}),flush=True)
                                break
                            except (BudgetExceeded,ProviderRateLimited) as exc:
                                if exc.retry_after<=30 and retries<2:
                                    retries+=1;await asyncio.sleep(exc.retry_after);continue
                                reason=f'{type(exc).__name__}: {exc}';stopped.set();break
                            except (ModelError,ValueError,TypeError,json.JSONDecodeError) as exc:
                                if len(ids)>1 and (isinstance(exc,(ValueError,TypeError)) or 'empty content' in str(exc)):
                                    midpoint=len(ids)//2
                                    chunks.put_nowait(ids[:midpoint]);chunks.put_nowait(ids[midpoint:])
                                    print(json.dumps({'event':'split','messages':len(ids),'reason':type(exc).__name__}),flush=True)
                                    break
                                if retries<1:
                                    retries+=1;await asyncio.sleep(2);continue
                                reason=f'{type(exc).__name__}: {exc}';stopped.set();break
                    finally:
                        release(store,state)
            workers=[asyncio.create_task(worker()) for _ in range(2)]
            try:await asyncio.gather(*workers)
            finally:
                for task in workers:
                    if not task.done():task.cancel()
                await asyncio.gather(*workers,return_exceptions=True)
    except Exception as exc:
        reason=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        remaining=len(snapshot_pending(store,cutoff));store.close()
        print(json.dumps({'event':'finished','reviewed_by_manual_run':processed,'new_memories':created,'remaining_from_backlog':remaining,'reason':reason}),flush=True)
    return 1 if reason else 0


def main():
    try:return asyncio.run(run())
    except KeyboardInterrupt:return 130

if __name__=='__main__':raise SystemExit(main())
