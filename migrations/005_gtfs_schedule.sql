BEGIN;

CREATE TABLE gtfs_feed (
    feed_id         integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_version    text,
    sha256          text        Not NULL UNIQUE,
    source          text        NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    effective_from  date        NOT NULL,
    feed_start_date date,
    feed_end_date   date
);

CREATE TABLE gtfs_route (
    feed_id    integer  NOT NULL REFERENCES gtfs_feed (feed_id),
    route_id   text     NOT NULL,
    short_name text,
    long_name  text,
    route_type smallint NOT NULL,
    PRIMARY KEY (feed_id, route_id)
);

CREATE TABLE gtfs_stop (
    feed_id   integer NOT NULL REFERENCES gtfs_feed (feed_id),
    stop_id   text    NOT NULL,
    name      text,
    latitude  double precision,
    longitude double precision,
    PRIMARY KEY (feed_id, stop_id)
);

CREATE TABLE gtfs_trip (
    feed_id      integer  NOT NULL,
    trip_id      text     NOT NULL,
    route_id     text     NOT NULL,
    service_id   text     NOT NULL,
    direction_id smallint,
    headsign     text,
    shape_id     text,
    block_id     text,
    PRIMARY KEY (feed_id, trip_id),
    FOREIGN KEY (feed_id, route_id) REFERENCES gtfs_route (feed_id, route_id)
);

CREATE TABLE gtfs_stop_time (
    feed_id           integer NOT NULL,
    trip_id           text    NOT NULL,
    stop_sequence     integer NOT NULL,
    stop_id           text    NOT NULL,
    arrival_seconds   integer NOT NULL,
    departure_seconds integer NOT NULL,
    timepoint         boolean,
    PRIMARY KEY (feed_id, trip_id, stop_sequence),
    FOREIGN KEY (feed_id, trip_id) REFERENCES gtfs_trip (feed_id, trip_id),
    FOREIGN KEY (feed_id, stop_id) REFERENCES gtfs_stop (feed_id, stop_id)
);

CREATE TABLE gtfs_calendar (
    feed_id    integer NOT NULL REFERENCES gtfs_feed (feed_id),
    service_id text    NOT NULL,
    monday     boolean NOT NULL,
    tuesday    boolean NOT NULL,
    wednesday  boolean NOT NULL,
    thursday   boolean NOT NULL,
    friday     boolean NOT NULL,
    saturday   boolean NOT NULL,
    sunday     boolean NOT NULL,
    start_date date    NOT NULL,
    end_date   date    NOT NULL,
    PRIMARY KEY (feed_id, service_id)
);

CREATE TABLE gtfs_calendar_date (
    feed_id        integer  NOT NULL REFERENCES gtfs_feed (feed_id),
    service_id     text     NOT NULL,
    date           date     NOT NULL,
    exception_type smallint NOT NULL CHECK (exception_type IN (1, 2)),
    PRIMARY KEY (feed_id, service_id, date)
);

-- GTFS times count from "noon minus 12 hours" on the service date, which is
-- midnight except on the two days a year the clocks change.
CREATE FUNCTION service_day_start(service_date date) RETURNS timestamptz
    LANGUAGE sql STABLE
    RETURN ((service_date + time '12:00') AT TIME ZONE 'America/Chicago') - interval '12 hours';

-- The schedule version in effect on a service date: the newest one whose
-- effective_from is on or before it.
CREATE FUNCTION gtfs_feed_for(service_date date) RETURNS integer
    LANGUAGE sql STABLE
    RETURN (
        SELECT feed_id FROM gtfs_feed
        WHERE effective_from <= service_date
        ORDER BY effective_from DESC, feed_id DESC
        LIMIT 1
    );

INSERT INTO schema_migration (version) VALUES ('005');

COMMIT;