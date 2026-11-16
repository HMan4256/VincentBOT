CREATE TABLE IF NOT EXISTS group_request_members(
 request_id TEXT NOT NULL, queue_id INTEGER NOT NULL,
 PRIMARY KEY(request_id,queue_id),
 FOREIGN KEY(request_id) REFERENCES request_attempts(request_id),
 FOREIGN KEY(queue_id) REFERENCES queue(id)
);
