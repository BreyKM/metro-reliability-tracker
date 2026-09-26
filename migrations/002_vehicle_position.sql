BEGIN;

CREATE TABLE vehicle_position (
    vehicle_id    text        NOT NULL,
    reported_at   timestamptz NOT NULL,
    trip_id       text        NOT NULL,
    route_id      text        NOT NULL,
    latitude      real        NOT NULL,
    longitude     real        NOT NULL,
    delay_seconds integer,
    is_monitored  boolean     NOT NULL,
    poll_id       bigint      NOT NULL REFERENCES poll (poll_id),
    PRIMARY KEY (vehicle_id, reported_at, trip_id)
);

INSERT INTO schema_migration (version) VALUES ('002');

COMMIT;