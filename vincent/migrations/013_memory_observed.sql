ALTER TABLE memories ADD COLUMN observed_at REAL;
UPDATE memories SET observed_at=created_at WHERE observed_at IS NULL;
