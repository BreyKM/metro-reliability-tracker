# Scripts

One-off checks used to learn the feeds before building the collector. Not part of the app. Findings are in [FEED_NOTES.md](../FEED_NOTES.md).

- `inspect_feed.py`: fetch one TripUpdates snapshot, count which fields are present, save the raw file.
- `inspect_vehicles.py`: fetch one Vehicles response, count rows and duplicates, measure position age.
- `compare_snapshots.py`: compare two saved TripUpdates files to see trips start, end, and drop passed stops.
- `probe_cadence.py`: poll TripUpdates repeatedly to measure update interval and find the rate limit. Makes ~30 requests over 5 minutes; don't run it while the collector is running.