BEGIN;

-- Finding the trips that serve a stop (the routes list and the leave-by query) otherwise
-- reads every stop time in the schedule.
CREATE INDEX gtfs_stop_time_stop ON gtfs_stop_time (feed_id, stop_id);

INSERT INTO schema_migration (version) VALUES ('009');

COMMIT;