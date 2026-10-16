BEGIN;

-- Counts from parsing a snapshot. "Rows" are stop updates for TripUpdates
-- and vehicles rows for Vehicles. Null when the poll failed before parsing.
ALTER TABLE poll
    ADD COLUMN row_count       integer,
    ADD COLUMN skipped_trips   integer,
    ADD COLUMN skipped_rows    integer,
    ADD COLUMN duplicate_rows  integer;

INSERT INTO schema_migration (version) VALUES ('003');

COMMIT;