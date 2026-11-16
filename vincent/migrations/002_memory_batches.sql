CREATE TABLE IF NOT EXISTS memory_candidates(message_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, user_id TEXT NOT NULL, generation INTEGER NOT NULL, created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
CREATE INDEX IF NOT EXISTS candidate_pending ON memory_candidates(state,created_at);
