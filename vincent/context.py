"""Build traceable channel context without losing the queued request."""
import json
import uuid
from .profiles import Profiles


def clear_channel(store,guild,channel,*,actor,now):
    with store.transaction() as db:
        db.execute('INSERT INTO channel_context(guild_id,channel_id,reset_at) VALUES(?,?,?) ON CONFLICT(guild_id,channel_id) DO UPDATE SET reset_at=excluded.reset_at',(str(guild),str(channel),now))
        db.execute("UPDATE queue SET status='cancelled',reason='context cleared',updated_at=? WHERE guild_id=? AND channel_id=? AND status IN ('pending','claimed')",(now,str(guild),str(channel)))
        db.execute("UPDATE opportunities SET state='cleared' WHERE guild_id=? AND channel_id=? AND state='pending'",(str(guild),str(channel)))
        db.execute("INSERT INTO events(event_key,kind,guild_id,channel_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?)",(f'context_clear:{uuid.uuid4().hex}','context_cleared',str(guild),str(channel),str(actor),now,now,'{}'))


def build_context(store,memory,item,*,memory_enabled=True,recent_limit=20,parent_depth=3):
    sources=json.loads(item['source_ids']);channel=item['channel_id'];guild=item['guild_id']
    with store.lock:
        reset=store.db.execute('SELECT reset_at FROM channel_context WHERE guild_id=? AND channel_id=?',(guild,channel)).fetchone()
        reset_at=reset['reset_at'] if reset else 0
        recent=store.db.execute('SELECT message_id,author_id,content,reply_to,created_at,changed_at,metadata FROM messages WHERE channel_id=? AND guild_id=? AND deleted=0 AND created_at>=? ORDER BY created_at DESC LIMIT ?', (channel,guild,reset_at,recent_limit)).fetchall()
        # Always fetch the queued request and its explicit ancestors, even in busy channels.
        collected={r['message_id']:dict(r) for r in recent}
        # Every included message can affect the model, not only the target branch.
        input_versions={r['message_id']:r['changed_at'] for r in recent}
        ancestors=set()
        for mid in sources:
            cursor=mid
            for _ in range(parent_depth+1):
                if not cursor:break
                row=store.db.execute('SELECT message_id,author_id,content,reply_to,created_at,changed_at,metadata FROM messages WHERE message_id=? AND channel_id=? AND guild_id=? AND deleted=0 AND created_at>=?',(str(cursor),channel,guild,reset_at)).fetchone()
                if not row:break
                collected[row['message_id']]=dict(row);input_versions[row['message_id']]=row['changed_at'];ancestors.add(row['message_id']);cursor=row['reply_to']
    messages=sorted(collected.values(),key=lambda r:(r['created_at'],r['message_id']))
    for row in messages:row['metadata']=json.loads(row['metadata'])
    live_sources=[mid for mid in sources if mid in collected]
    if not live_sources:return None
    source_time=max(collected[mid]['created_at'] for mid in live_sources)
    # Short follow-ups often refer to a recent message or explicit reply parent.
    # Do not use unrelated speakers' intervening messages as a user's interests.
    same_author=[r for r in messages if r['author_id']==item['author_id'] and source_time-120<=r['created_at']<=source_time and r['message_id'] not in live_sources][-3:]
    query_rows={r['message_id']:r for r in same_author}
    query_rows.update({r['message_id']:r for r in messages if r['message_id'] in ancestors})
    query=' '.join(r['content'] or '' for r in query_rows.values())
    shared=memory.shared(guild,query=query) if memory_enabled else []
    private=memory.search(guild,item['author_id'],query) if memory_enabled else []
    generation=memory.generation(guild,item['author_id']) if memory_enabled else None
    return {'_input_versions':input_versions,'_memory_generation':generation,
            '_memory_versions':{r['id']:r['updated_at'] for r in shared+private},
            'mode':'DIRECT' if item['priority']>=20 else 'WELCOME' if item['reason']=='welcome' else 'OBSERVE' if item['reason']=='observe' else 'FOLLOWUP_CANDIDATE',
            'current_user_id':item['author_id'],'source_message_ids':live_sources,'recent_messages':messages,
            'shared_memories':shared,'private_memories':private,
            'private_profile':Profiles(store).get(guild,item['author_id']) if memory_enabled else None}
