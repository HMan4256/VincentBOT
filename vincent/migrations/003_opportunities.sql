CREATE TABLE IF NOT EXISTS opportunities (
 channel_id TEXT NOT NULL, branch_id TEXT NOT NULL, guild_id TEXT NOT NULL,
 message_id TEXT NOT NULL, author_id TEXT NOT NULL, due_at REAL NOT NULL,
 updated_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 PRIMARY KEY(channel_id,branch_id)
);
CREATE TABLE IF NOT EXISTS channel_activity (
 channel_id TEXT PRIMARY KEY, last_auto_sent REAL NOT NULL DEFAULT 0
);
