# H3 Analysis Platform Specification

## 1. Objective

Provide analysts with a simple web interface for exploring audience **index**
metrics by H3 cell and audience segment. The initial geographic scope is the
United Arab Emirates.

The application has **exactly two map pages**, each on its own Streamlit page
because they use different data sources and analysis logic:

- **Page 1 (`app.py`) — index map.** A radio switches the metric between
  `overall_index`, `volume_index` and `exclusivity_index`; each is a separate
  BigQuery table. Filter: audience segment only (no time column). Data source:
  **BigQuery**. A local CSV (upload or the committed synthetic
  `data/sample_index.csv`) is an explicit development fallback only.
- **Page 2 (`pages/2_Index_Analysis.py`) — index map.** `exclusivity_index` /
  `volume_index` from local CSVs (`data/map_2/` or `data/sample_index.csv`).
  Placeholder for the future day-part BigQuery version.

Only one map is shown per page. Index values are **averaged, never summed**.

### 1a. Current phase and roadmap

- Immediate implementation: BigQuery for **Page 1 only**, using the three
  `*_filtered` index tables.
- Future: migrate Page 2 to its own three BigQuery tables that add a day-part
  (morning / noon / evening / …) dimension and a time-of-day filter. Add further
  tables/pages only when their approved schema is supplied. The old CSV "Overall
  analysis index" (`data/map_3`) map and the hourly `user_count` map have been
  removed; `overall_index` is now only a Page 1 BigQuery metric.

## 2. Page 1 user workflow

1. The user picks a metric (Overall / Volume / Exclusivity).
2. The app queries that metric's BigQuery table (or, in the local fallback,
   loads `data/sample_index.csv` / an upload).
3. The user includes or excludes audience segments using checkboxes.
4. The metric is averaged per `h3_id` across the selected segments (in BigQuery).
5. The map displays one colored H3 polygon per aggregated cell, with a
   per-metric sequential legend.
6. The user can switch between dark and alternate basemap styles.
7. The user can download the result and inspect it in the raw-data expander.

## 3. Input data contract

### 3a. Page 1 BigQuery source

- One fully qualified table per metric, from configuration:
  `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` +
  `BIGQUERY_{OVERALL,VOLUME,EXCLUSIVITY}_INDEX_TABLE`, or a per-metric
  `BIGQUERY_<METRIC>_TABLE_FQN` override. No project, dataset, table, credential,
  or SQL value is hard-coded. Confirmed values: project `maddictdata`, dataset
  `OOH_Analysis`, tables `h3_analysis_indexed_filtered` (overall),
  `h3_analysis_volume_index_filtered`, `h3_analysis_exclusivity_index_filtered`.
- Resolution order is real environment variables, then the Git-ignored `.env`
  (template: `.env.example`). `scripts/check_bigquery.py` validates the wiring.
- Each table's schema: `h3_id` STRING, `segment` STRING, and one numeric column
  named exactly after the metric (`overall_index` / `volume_index` /
  `exclusivity_index`), FLOAT. No time column. Each `(h3_id, segment)` pair is
  **repeated** — ~8.4 rows per pair on the live tables, unevenly distributed.
- The segment filter is bound as `@segments` (ARRAY<STRING>). User values are
  never interpolated into SQL. Only the validated table FQN and the allow-listed
  metric name reach the SQL text.
- Aggregation runs in BigQuery in **two steps**: `AVG` grouped by
  `(h3_id, segment)`, then `AVG` of that grouped by `h3_id`. Only `h3_id` and
  the average are returned. A single-step `AVG ... GROUP BY h3_id` is incorrect
  here — it weights each pair by its duplicate-row count and, measured against
  the live tables, changed 48.6% of cells by up to 108% relative. Results are
  cached with `st.cache_data` keyed by table FQN, metric and segments.
- Credentials come from Application Default Credentials (local:
  `gcloud auth application-default login`; Cloud Run: attached service account).
- Missing configuration or permissions produce an actionable in-app message,
  not a traceback. The sidebar offers a local-CSV fallback.

### 3b. CSV contract (Page 1 fallback and Page 2)

| Field | Required format | Validation |
| --- | --- | --- |
| `h3_id` | Text | Non-empty valid H3 index |
| `segment` | Text | Non-empty category |
| `<metric>` | Number | Finite and ≥ 0; column named after the metric |

`data/sample_index.csv` carries all three metric columns. Repeated
`(h3_id, segment)` rows are averaged (never summed); the datasets are never
joined. Unknown extra columns are ignored. Invalid values are reported with the
affected row count; invalid cells are excluded, never placed at an unrelated
location.

## 4. Functional requirements

### Data loading (Page 1)

- Default data source is BigQuery (see §3a).
- The sidebar can switch to the local fallback (or `H3_DATA_SOURCE=local`).
- Local fallback order: uploaded CSV, then the committed synthetic
  `data/sample_index.csv`; otherwise show instructions and stop cleanly.
- Production data must remain outside version control.

### Filters

- A metric radio (Overall / Volume / Exclusivity) drives which table is queried.
- Display one checkbox for each distinct `segment` value; select all by default;
  require at least one selected segment.
- There is no time filter on Page 1.

### Aggregation

- Filter by selected segments first.
- Average within each `(h3_id, segment)` pair, then across segments per `h3_id`.
  Never sum index values, and never average the raw rows in one step.
- Use the aggregated value for color, tooltip, and legend.

### Map

- Render cells with PyDeck `H3HexagonLayer`.
- Center the view from displayed H3 cell centroids.
- Fall back to the UAE center (`24.0, 54.0`) for an empty result.
- Provide a dark basemap and one clearly labeled alternate basemap.
- Do not label a road basemap as terrain or satellite.
- Show `h3_id` and the metric value in the tooltip.
- Preserve filters when the basemap changes.

### Error states

- Missing configuration/permissions: actionable message, offer local fallback.
- Missing required columns (CSV): list the missing column names.
- Empty result: informational message and the UAE fallback map.
- Invalid H3 values: report them and exclude them from centroid/map.
- Non-numeric / negative metric values: report the affected row count.

## 4b. Page 2 — index analysis map (day-part placeholder)

A second **page** analyses the local-CSV index dataset. It reuses the shared H3
rendering (`h3_analysis/mapping.py`), has its own segment checkboxes and basemap
choice, tooltips, and map controls. It will become the day-part BigQuery version
(with a morning/noon/evening filter) once those tables are provided.

- The metric control switches between `exclusivity_index` and `volume_index`.
  The selected metric drives the cell color, tooltip value, legend, and title.
- Each metric has its own single-hue sequential ramp so the two are never read
  against a shared scale: blue for exclusivity, orange for volume.
- `exclusivity_index` is near-symmetric and uses a linear scale.
  `volume_index` spans about four orders of magnitude and uses a log scale; a
  linear scale would collapse nearly every cell onto one step.
- The ramp direction follows the basemap. On the dark basemap high values take
  the light end, because the darkest steps would otherwise sink into the
  background.
- Percentile clipping (p2/p98) keeps a few extreme cells from flattening the ramp.
- The dataset has no time column, so this page has no time filter. That
  limitation is stated in the UI.
- Repeated cell/segment rows are averaged, never summed; the index datasets are
  never joined.
- Missing columns, non-numeric values, negative values, and invalid H3 indexes
  are reported with the affected row count, as on Page 1.

## 5. Non-functional requirements

- Page 1 BigQuery queries aggregate server-side and are cached; a filter change
  should return within a few seconds.
- The application must not expose credentials or production data through Git.
- UI labels should be understandable without knowledge of H3 internals.
- The app should run on Linux/WSL and Windows with Python 3.10 or newer.
- Dependencies should be reproducible and reviewed before deployment.

## 6. Current repository layout

```text
h3-analysis/
|-- app.py                    # Page 1: index map (BigQuery, 3 metric tables)
|-- pages/
|   `-- 2_Index_Analysis.py   # Page 2: index map (local CSV, day-part placeholder)
|-- h3_analysis/
|   |-- bigquery_source.py    # Streamlit-free BigQuery config + query builders
|   |-- data.py               # Validation and aggregation helpers
|   |-- colors.py             # Sequential ramps and scaling for the index metrics
|   |-- mapping.py            # Shared PyDeck H3 rendering and basemaps
|   `-- config.py             # Loads the Git-ignored local .env (identifiers only)
|-- scripts/
|   `-- check_bigquery.py     # Verifies config, permissions, and table schemas
|-- tests/
|   |-- test_index.py         # Index validation, aggregation, colors, map_3 removal
|   |-- test_bigquery.py      # FQN config, parameterized queries, aggregated validation
|   `-- test_config.py        # .env loading precedence and .env.example resolution
|-- .env.example              # Committed config template; .env is ignored
|-- Dockerfile                # Cloud Run image (binds $PORT)
|-- .dockerignore
|-- .github/workflows/deploy-cloud-run.yml  # WIF deploy, no SA key
|-- requirements.txt
|-- README.md  CLAUDE.md  AGENTS.md  spec.md
|-- .streamlit/config.toml
|-- .claude/launch.json
`-- data/
    |-- sample_index.csv       # Committed synthetic fallback (all 3 metric columns)
    `-- map_2/*.csv            # Local index exports; ignored by Git
```

## 7. Planned evolution

- Migrate Page 2 to its own three BigQuery tables adding a day-part dimension
  and a morning/noon/evening filter.
- Add further BigQuery tables/pages only when their approved schema and purpose
  are provided.
- Company authentication/authorization via Cloud Run auth + IAP (see README).
- Browser-level tests for Streamlit widget interactions.

## 8. Acceptance criteria for the current version

- Page 1 queries the three BigQuery tables from configuration with no code
  changes, aggregating (`AVG`) server-side, with the segment filter bound as a
  query parameter.
- Missing BigQuery config/permissions show an actionable message; the local
  fallback still works.
- Page 1's metric radio switches Overall / Volume / Exclusivity, each from its
  own table.
- Page 2 is a separate page with no time filter, averaging repeated rows.
- All discovered segments appear as independent checkboxes on each page.
- Valid H3 cells render in their correct UAE locations.
- Changing the basemap does not alter the data or active filters.
- The former CSV "Overall analysis index" map and the hourly `user_count` map,
  with their data, helpers, and tests, are gone.
- The app starts using the commands documented in `README.md`.
