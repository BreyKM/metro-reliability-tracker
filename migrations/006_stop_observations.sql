BEGIN;

CREATE TABLE stop_observation (
    service_date      date        NOT NULL,
    trip_id           text        NOT NULL,
    stop_sequence     integer     NOT NULL,
    feed_id           integer     NOT NULL,
    route_id          text        NOT NULL,
    direction_id      smallint,
    stop_id           text        NOT NULL,
    scheduled_arrival timestamptz NOT NULL,
    predicted_arrival timestamptz NOT NULL,
    delay_seconds     integer     NOT NULL,
    lead_seconds      integer     NOT NULL,
    date_shifted      boolean     NOT NULL,
    PRIMARY KEY (service_date, trip_id, stop_sequence),
    FOREIGN KEY (feed_id, trip_id, stop_sequence) REFERENCES gtfs_stop_time (feed_id, trip_id, stop_sequence)
);

COMMENT ON COLUMN stop_observation.service_date IS
    'Service date after correcting METRO''s start_date (decision #26)';
COMMENT ON COLUMN stop_observation.lead_seconds IS
    'Predicted arrival minus when METRO made its last prediction; negative if made after';
COMMENT ON COLUMN stop_observation.date_shifted IS
    'Stored by METRO under the next day and moved back to its service date';

CREATE TABLE schedule_match (
    service_date date        PRIMARY KEY,
    feed_id      integer     REFERENCES gtfs_feed (feed_id),
    exact        integer     NOT NULL,
    shifted      integer     NOT NULL,
    unmatched    integer     NOT NULL,
    built_at     timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migration (version) VALUES ('006');

COMMIT;