BEGIN;

-- Leave half of each new page free, so a poll's updates fit on the same page as the rows
-- they replace and skip the index (HOT updates, decision #36).
ALTER TABLE stop_prediction SET (fillfactor = 50);

INSERT INTO schema_migration (version) VALUES ('010');

COMMIT;