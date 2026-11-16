CREATE TABLE IF NOT EXISTS queue_sources(
 message_id TEXT PRIMARY KEY, queue_id INTEGER NOT NULL,
 FOREIGN KEY(queue_id) REFERENCES queue(id) ON DELETE CASCADE
);
INSERT OR IGNORE INTO queue_sources(message_id,queue_id)
 SELECT CAST(source.value AS TEXT),q.id FROM queue q,json_each(q.source_ids) source ORDER BY q.id;
