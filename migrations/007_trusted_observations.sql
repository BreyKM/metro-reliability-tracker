BEGIN;

-- The trust flag changes what every row means, so start over. The next
-- metro-build-observations run rebuilds every date from the first prediction.
TRUNCATE stop_observation, schedule_match;

ALTER TABLE stop_observation ADD COLUMN trusted boolean NOT NULL;
ALTER TABLE schedule_match ADD COLUMN trusted integer NOT NULL;

COMMENT ON COLUMN stop_observation.trusted IS
    'Every stop of this trip that dropped out of the feed in the same poll was predicted within 2 minutes of when it dropped (decision #28)';

INSERT INTO schema_migration (version) VALUES ('007');

COMMIT;