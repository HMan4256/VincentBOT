CREATE TABLE IF NOT EXISTS queue_events(id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL, occurred_at REAL NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(queue_id) REFERENCES queue(id));
CREATE INDEX IF NOT EXISTS queue_events_queue ON queue_events(queue_id,occurred_at);
