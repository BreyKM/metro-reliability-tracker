BEGIN;

ALTER TABLE poll ADD COLUMN no_fix_rows integer;

COMMENT ON COLUMN poll.header_timestamp IS
    'When the feed''s data was generated: the GTFS-RT header timestamp for trip_updates, '
    'the newest VehicleReportTime for vehicles.';
COMMENT ON COLUMN poll.no_fix_rows IS
    'Vehicles rows at latitude 0, longitude 0 (no GPS fix), left out of vehicle_position.';

INSERT INTO schema_migration (version) VALUES ('004');

COMMIT;