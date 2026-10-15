# Netflix UK Top 10 for Stremio

A tiny catalog-only Stremio addon that publishes the current Netflix UK Top 10 for:

- Series
- Movies

The catalog is refreshed automatically from the public FlixPatrol Netflix UK chart and resolves titles to normal IMDb IDs through Cinemeta.

## Manifest

Raw GitHub manifest:

https://raw.githubusercontent.com/Ricks-77/Netflix-UK-top-10-for-Strenio/main/docs/manifest.json

## What it does

- Keeps the Netflix UK ranking order.
- Updates automatically every day.
- Keeps the previous good catalog if the source or title resolution breaks.
- Requires no MDBList, Trakt, TMDB API key or paid service.

## Freshness and failure behaviour

The updater tries the dated, direct FlixPatrol Netflix UK daily chart first,
then its Jina mirror. The chart heading must identify Netflix, United Kingdom,
and today's date in Europe/London. Both lists must contain ten distinct titles
in positions 1–10; both catalogs must resolve ten distinct metadata IDs.
Only after both resolve are the catalogs and `rankings.json` published together.

A local chart is a fallback only after live retrieval fails, and only when it
has today's date, complete lists and source provenance. Undated, future and
older local charts never count as a successful refresh. Before today's chart
is available, retaining yesterday's catalog is expected, but the attempt fails
rather than claiming it is current.

`docs/status.json` separates the attempt timestamp from `netflix.rankings_date`
and `last_success_at`. `refresh_success`, `state`, `stale` and `error` describe
failures while preserving the last verified Netflix catalog. Legacy statuses
without a chart date remain explicitly unknown/stale until a successful refresh.
IMDb refresh failures also return a nonzero exit code without hiding Netflix
status. Actions commits the status even when the refresh fails, then marks the
run failed. Concurrent updates are serialised. Pull requests run offline tests
without refreshing or pushing catalogs.

Run regression tests with `python -m unittest discover -s tests -v` after
`pip install -r requirements.txt`.
