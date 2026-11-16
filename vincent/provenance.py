"""Invalidate derived memories when their source message is edited or deleted."""
import json
import time
from .store import json_text


def invalidate_source(store,message_id,*,deleted):
    mid=str(message_id);now=time.time();removed=0
    with store.transaction() as db:
        responses={r['response_message_id'] for r in db.execute("SELECT q.response_message_id FROM queue q WHERE q.response_message_id IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(q.source_ids) s WHERE s.value=?)",(mid,)).fetchall()}
        responses.add(mid)
        if responses:
            for version in db.execute('SELECT id,evidence FROM prompt_versions').fetchall():
                try:cited={str(x) for x in json.loads(version['evidence']).get('source_ids',[])}
                except (TypeError,ValueError,AttributeError):cited=set()
                if cited & responses:
                    db.execute("UPDATE prompt_versions SET text='[redacted]',status='redacted',evidence='{}' WHERE id=?",(version['id'],))
                    db.execute("UPDATE prompt_experiments SET state='aborted',finished_at=?,outcome=? WHERE state='running' AND (candidate_id=? OR incumbent_id=?)",(now,json_text({'reason':'source invalidated'}),version['id'],version['id']))
        for row in db.execute("SELECT id,guild_id,user_id,source_ids FROM memories").fetchall():
            if mid not in {str(x) for x in json.loads(row['source_ids'])}:continue
            db.execute('DELETE FROM memories WHERE id=?',(row['id'],));removed+=1
            db.execute('INSERT INTO memory_events(memory_id,guild_id,user_id,action,occurred_at,details) VALUES(?,?,?,?,?,?)',(row['id'],row['guild_id'],row['user_id'],'source_invalidated',now,json_text({'source_id':mid,'deleted':deleted})))
        if deleted:db.execute('DELETE FROM memory_candidates WHERE message_id=?',(mid,))
        else:db.execute("UPDATE memory_candidates SET state='pending',created_at=? WHERE message_id=?",(now,mid))
    return removed
