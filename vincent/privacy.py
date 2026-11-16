"""Local deletion of a member's records and derived information."""
import json
import time
from .store import json_text


def erase_user(store,guild,user):
    guild,user=str(guild),str(user);now=time.time()
    with store.transaction() as db:
        sources={r['message_id'] for r in db.execute('SELECT message_id FROM messages WHERE guild_id=? AND author_id=?',(guild,user)).fetchall()}
        # Preserve opaque erasure markers so a later Discord reaction snapshot
        # cannot restore votes the person already asked us to delete.
        feedback_guilds={r['guild_id'] for r in db.execute('SELECT DISTINCT q.guild_id FROM feedback f JOIN queue q ON q.response_message_id=f.message_id WHERE f.user_id=?',(user,)).fetchall()}
        feedback_guilds.add(guild)
        for gid in feedback_guilds:
            db.execute('INSERT INTO privacy_tombstones(guild_id,user_id,erased_at) VALUES(?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET erased_at=excluded.erased_at',(gid,user,now))
        responses={r['response_message_id'] for r in db.execute('SELECT response_message_id FROM queue WHERE guild_id=? AND author_id=? AND response_message_id IS NOT NULL',(guild,user)).fetchall()}
        sources.update(responses)
        # Learned guidance can contain information derived from rated replies; invalidate
        # any version citing an erased interaction rather than retaining its prose.
        for version in db.execute('SELECT id,evidence FROM prompt_versions').fetchall():
            try:
                evidence=json.loads(version['evidence']);cited={str(x) for x in evidence.get('source_ids',[])};raters={str(x) for x in evidence.get('rater_ids',[])}
            except (TypeError,ValueError,AttributeError): cited=set();raters=set()
            if cited & responses or user in raters:
                db.execute("UPDATE prompt_versions SET text='[redacted]',status='redacted',evidence='{}' WHERE id=?",(version['id'],))
                db.execute("UPDATE prompt_experiments SET state='aborted',finished_at=?,outcome=? WHERE state='running' AND (candidate_id=? OR incumbent_id=?)",(now,json_text({'reason':'source data erased'}),version['id'],version['id']))
        for mid in responses:
            db.execute('DELETE FROM feedback WHERE message_id=?',(mid,))
            db.execute('DELETE FROM feedback_events WHERE message_id=?',(mid,))
            db.execute('DELETE FROM feedback_clear_marks WHERE message_id=?',(mid,))
            db.execute("UPDATE messages SET content=NULL,deleted=1,metadata=? WHERE message_id=?",(json_text({'redacted':True}),mid))
            db.execute('DELETE FROM events WHERE message_id=?',(mid,))
        # Remove any derived memory supported by the deleted user's source messages.
        for row in db.execute('SELECT id,source_ids FROM memories').fetchall():
            try: refs={str(x) for x in json.loads(row['source_ids'])}
            except (TypeError,ValueError): refs=set()
            if refs & sources:db.execute('DELETE FROM memories WHERE id=?',(row['id'],))
        db.execute('DELETE FROM memory_events WHERE guild_id=? AND user_id=?',(guild,user))
        for source in sources:db.execute('DELETE FROM memory_events WHERE instr(details,?)>0',(source,))
        db.execute('DELETE FROM memories WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('DELETE FROM user_profiles WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('DELETE FROM profile_interactions WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('DELETE FROM profile_outgoing WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('DELETE FROM memory_candidates WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('DELETE FROM legacy_import_preferences WHERE guild_id=? AND user_id=?',(guild,user))
        db.execute('''INSERT INTO memory_preferences(guild_id,user_id,enabled,generation) VALUES(?,?,0,1)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET enabled=0,generation=memory_preferences.generation+1''',(guild,user))
        db.execute("UPDATE queue SET status='cancelled',reason='user data erased',send_text=NULL,updated_at=? WHERE guild_id=? AND author_id=? AND status IN ('pending','claimed','uncertain')",(now,guild,user))
        db.execute('DELETE FROM queue_sources WHERE queue_id IN (SELECT id FROM queue WHERE guild_id=? AND author_id=?)',(guild,user))
        db.execute('DELETE FROM queue_events WHERE queue_id IN (SELECT id FROM queue WHERE guild_id=? AND author_id=?)',(guild,user))
        db.execute("UPDATE queue SET send_text=NULL,source_ids='[]',author_id='redacted' WHERE guild_id=? AND author_id=?",(guild,user))
        db.execute('DELETE FROM opportunities WHERE guild_id=? AND author_id=?',(guild,user))
        db.execute('DELETE FROM feedback WHERE user_id=?',(user,))
        db.execute('DELETE FROM feedback_events WHERE user_id=?',(user,))
        db.execute("UPDATE messages SET content=NULL,deleted=1,metadata=?,author_id='redacted' WHERE guild_id=? AND author_id=?",(json_text({'redacted':True}),guild,user))
        db.execute('DELETE FROM events WHERE guild_id=? AND author_id=?',(guild,user))
        # Prompt snapshots contain full channel context. Remove any trace which contains this user
        # or a deleted source ID, conservatively deleting the whole snapshot rather than editing JSON.
        terms=[user,*sources]
        for term in terms:
            db.execute('DELETE FROM requests_log WHERE instr(payload,?)>0 OR instr(COALESCE(response,\'\'),?)>0',(term,term))
        db.execute('INSERT INTO events(event_key,kind,guild_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?)',(f'data_erased:{guild}:{now}','data_erased',guild,now,now,json_text({'scope':'user','metadata_redacted':True})))
    # Remove committed WAL pages where SQLite can obtain the checkpoint lock.
    with store.lock:
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
