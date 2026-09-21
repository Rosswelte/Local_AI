ALTER TABLE jobs ADD COLUMN memory_guard INTEGER NOT NULL DEFAULT 0 CHECK (memory_guard IN (0, 1));
