BEGIN;

CREATE TABLE schema_migration (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE poll (
    poll_id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed              text        NOT NULL CHECK (feed IN ('trip_updates', 'vehicles')),
    requested_at      timestamptz NOT NULL,
    duration_ms       integer,
    http_status       smallint,
    header_timestamp  timestamptz,
    entity_count      integer,
    bytes             integer,
    skipped_unchanged boolean     NOT NULL DEFAULT false,
    error             text
);

CREATE INDEX poll_feed_requested_at_idx ON poll (feed, requested_at);

CREATE TABLE stop_prediction (
    service_date            date        NOT NULL,
    trip_id                 text        NOT NULL,
    stop_sequence           integer     NOT NULL,
    stop_id                 text        NOT NULL,
    route_id                text        NOT NULL,
    direction_id            smallint,
    vehicle_id              text,
    scheduled_arrival       timestamptz NOT NULL,
    first_predicted_arrival timestamptz NOT NULL,
    first_delay_seconds     integer     NOT NULL,
    predicted_arrival       timestamptz NOT NULL,
    delay_seconds           integer     NOT NULL,
    first_seen_at           timestamptz NOT NULL,
    last_seen_at            timestamptz NOT NULL,
    update_count            integer     NOT NULL DEFAULT 1 CHECK (update_count >= 1),
    first_poll_id           bigint      NOT NULL REFERENCES poll (poll_id),
    last_poll_id            bigint      NOT NULL REFERENCES poll (poll_id),
    PRIMARY KEY (service_date, trip_id, stop_sequence),
    CHECK (last_seen_at >= first_seen_at)
);

INSERT INTO schema_migration (version) VALUES ('001');

COMMIT;