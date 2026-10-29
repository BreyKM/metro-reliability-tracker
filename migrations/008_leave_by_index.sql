BEGIN;

-- The leave-by query reads trusted visits to two stops on one route.
CREATE INDEX stop_observation_trusted_stop_route
    ON stop_observation (stop_id, route_id)
    WHERE trusted;

INSERT INTO schema_migration (version) VALUES ('008');

COMMIT;