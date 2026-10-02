-- Dispatch ownership is independent of the worker's queued/running state.
ALTER TABLE chat_turn ADD COLUMN dispatch_started BOOLEAN NOT NULL DEFAULT FALSE;
