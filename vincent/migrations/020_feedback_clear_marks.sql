CREATE TABLE IF NOT EXISTS feedback_clear_marks(
 message_id TEXT NOT NULL, emoji_scope TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(message_id,emoji_scope)
);
