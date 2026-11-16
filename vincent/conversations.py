"""Explicit reply-chain traversal without guessing at unrelated timeline messages."""

def bot_ancestor(store,*,parent_id,channel_id,guild_id,bot_id,now,max_depth=5,max_age=120):
    current=str(parent_id or '');seen=set()
    with store.lock:
        reset=store.db.execute('SELECT reset_at FROM channel_context WHERE guild_id=? AND channel_id=?',(str(guild_id),str(channel_id))).fetchone()
        reset_at=reset['reset_at'] if reset else 0
        for _ in range(max_depth):
            if not current or current in seen:break
            seen.add(current)
            row=store.db.execute('SELECT message_id,author_id,reply_to,created_at,deleted FROM messages WHERE message_id=? AND channel_id=? AND guild_id=?',(current,str(channel_id),str(guild_id))).fetchone()
            if not row or row['deleted']:return None
            if row['created_at']<reset_at or now-row['created_at']<0 or now-row['created_at']>max_age:return None
            if row['author_id']==str(bot_id):return row['message_id']
            current=row['reply_to']
    return None
