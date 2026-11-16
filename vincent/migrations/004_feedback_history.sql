CREATE TABLE IF NOT EXISTS feedback_events(
 id INTEGER PRIMARY KEY, message_id TEXT NOT NULL, user_id TEXT NOT NULL,
 emoji TEXT NOT NULL, active INTEGER NOT NULL, occurred_at REAL NOT NULL,
 source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_event_message ON feedback_events(message_id,occurred_at);
