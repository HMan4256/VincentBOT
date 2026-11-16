CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE TRIGGER IF NOT EXISTS memory_embedding_invalidate
AFTER UPDATE OF content, status ON memories
WHEN OLD.content != NEW.content OR OLD.status != NEW.status
BEGIN
    DELETE FROM memory_embeddings WHERE memory_id = NEW.id;
END;
