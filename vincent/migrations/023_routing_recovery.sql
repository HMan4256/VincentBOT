ALTER TABLE messages ADD COLUMN routing_state TEXT NOT NULL DEFAULT 'pending';
UPDATE messages SET routing_state='done';
CREATE INDEX IF NOT EXISTS messages_pending_routing ON messages(routing_state,created_at);
CREATE TABLE IF NOT EXISTS profile_interactions(
 message_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
 occurred_at REAL NOT NULL
);
