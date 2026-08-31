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
- **Page 2 (`pages/2_Index_Analysis.py`) — day-part index map.** A radio
  switches the metric between the same three names; each is a separate
  `*_day_sections` BigQuery table carrying an extra `hour_bucket` (day-part)
  column. Filters: audience segment **and** day-part. Data source: **BigQuery**.
  A local CSV (upload or the committed synthetic
  `data/sample_index_day_sections.csv`) is a development fallback only.

Only one map is shown per page. Index values are **averaged, never summed**.

### 1a. Current phase and roadmap

- **Both pages are implemented on BigQuery**: Page 1 on the three `*_filtered`
  index tables, Page 2 on the three `*_filtered_day_sections` tables.
- Add further tables/pages only when their approved schema is supplied. The old
  CSV "Overall analysis index" (`data/map_3`) map and the hourly `user_count`
  map have been removed; `overall_index` is now a BigQuery metric on both pages
  (from different tables).

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

### 3b. CSV contract (development fallbacks)

Page 1 fallback (`data/sample_index.csv`, or an upload):

| Field | Required format | Validation |
| --- | --- | --- |
| `h3_id` | Text | Non-empty valid H3 index |
| `segment` | Text | Non-empty category |
| `<metric>` | Number | Finite and ≥ 0; column named after the metric |

Page 2 fallback (`data/sample_index_day_sections.csv`, or an upload) adds:

| Field | Required format | Validation |
| --- | --- | --- |
| `hour_bucket` | Text | Non-empty day-part label |

`data/sample_index.csv` carries all three metric columns (45 cells,
resolution 8). `data/sample_index_day_sections.csv` carries all three plus
`hour_bucket` (250 cells, **resolution 9**, matching the live Page 2 tables).
Because the two files differ in resolution and columns, they are **not**
interchangeable — Page 2 must never fall back to `data/sample_index.csv`
(see §4b-i).

Repeated key rows are averaged (never summed); the datasets are never joined.
Unknown extra columns are ignored. Invalid values are reported with the affected
row count; invalid cells are excluded, never placed at an unrelated location.

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

## 4b. Page 2 — day-part index analysis map

A second **page** analyses the day-section BigQuery dataset. It reuses the
shared H3 rendering and sidebar segment checkboxes (`h3_analysis/mapping.py`),
and adds a day-part control.

### Source and schema (verified against the live tables)

- One fully qualified table per metric, from configuration:
  `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` +
  `BIGQUERY_{OVERALL,VOLUME,EXCLUSIVITY}_INDEX_DAY_SECTIONS_TABLE`, or a
  per-metric `BIGQUERY_<METRIC>_DAY_SECTIONS_TABLE_FQN` override. Confirmed
  values: project `maddictdata`, dataset `OOH_Analysis`, tables
  `h3_analysis_indexed_filtered_day_sections` (overall),
  `h3_analysis_volume_index_filtered_day_sections`,
  `h3_analysis_exclusivity_index_filtered_day_sections`.
- Schema: `h3_id` STRING, `segment` STRING, `<metric>` FLOAT, `hour_bucket`
  STRING. The `hour_bucket` column holds **day-part labels, not hours**:
  `Morning`, `Noon`, `After noon`, `Night`, `Other`.
- Measured: ~601k rows and ~60.2k distinct cells per table; 3 segments × 5
  day-parts; all `h3_id` values valid **resolution-9** cells inside the UAE
  (lat ≈ 22.67–26.04, lon ≈ 51.62–56.37); no NULL or negative metric values.
- **Each `(h3_id, segment, hour_bucket)` triple appears exactly once** (max
  repeat count = 1), unlike Page 1's ~8x repeated pairs.

### Filters and aggregation

- The metric control switches between all three index metrics; the selected
  metric drives the cell color, tooltip value, legend, and title.
- Segment checkboxes behave as on Page 1; at least one is required.
- A day-part radio selects exactly one `hour_bucket`. The page must always
  filter to a single day-part before aggregating — averaging across day-parts
  would silently reproduce Page 1's numbers instead of a time-of-day view.
- Aggregation runs in BigQuery in **one step**:
  `AVG(<metric>) ... WHERE segment IN UNNEST(@segments) AND hour_bucket = @hour_bucket GROUP BY h3_id`.
  A Page-1-style two-step per-pair CTE is wrong here: with no duplicate triples
  it averages single-row groups, adding cost for an identical result.
- Segment values travel as `@segments` (ARRAY<STRING>) and the day-part as
  `@hour_bucket` (scalar). No user value is interpolated into SQL. Results are
  cached with `st.cache_data` keyed by table FQN, metric, segments and day-part.

### Presentation and errors

- Each metric has its own single-hue sequential ramp, shared with Page 1: blue
  for exclusivity, orange for volume, teal-green for overall.
- `exclusivity_index` and `overall_index` use a linear scale; `volume_index`
  spans about four orders of magnitude and uses a log scale.
- The ramp direction follows the basemap; percentile clipping (p2/p98) keeps a
  few extreme cells from flattening it.
- Missing configuration/permissions produce an actionable in-app message, not a
  traceback, with a local-CSV fallback offered — as on Page 1.
- Missing columns, non-numeric values, negative values, blank day-parts, and
  invalid H3 indexes are reported with the affected row count.

### 4b-i. Fixed defect — few cells, some in the ocean

Page 2 previously read `data/map_2/exclusivity_index.csv` /
`volume_index.csv`. That directory is empty and Git-ignored, so the page fell
through to `data/sample_index.csv`, a **synthetic 45-cell, resolution-8** file —
it was never connected to the real dataset. The two reported symptoms follow
directly: "only a few cells" (45 vs ~60.2k), and "cells in the ocean" (a
resolution-8 hexagon covers ~7x the area of a resolution-9 one, so a coastal
cell visibly overhangs the shoreline).

Investigated and ruled out: H3 ID validity, centroid/coordinate handling, and
the shared map rendering were all correct (and are shared with the known-good
Page 1); there is no join in this path and none should be added.

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
|   `-- 2_Index_Analysis.py   # Page 2: day-part index map (BigQuery, 3 *_day_sections tables)
|-- h3_analysis/
|   |-- bigquery_source.py    # Streamlit-free BigQuery config + query builders (both pages)
|   |-- data.py               # Validation and aggregation helpers (index + day-section)
|   |-- colors.py             # Sequential ramps and scaling for the index metrics
|   |-- mapping.py            # Shared PyDeck H3 rendering, basemaps, segment checkboxes
|   `-- config.py             # Loads the Git-ignored local .env (identifiers only)
|-- scripts/
|   `-- check_bigquery.py     # Verifies config, permissions, and schemas for both pages
|-- tests/
|   |-- test_index.py         # Index + day-section validation, aggregation, colors, map_3 removal
|   |-- test_bigquery.py      # FQN config, parameterized queries (both pages), aggregated validation
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
    |-- sample_index.csv       # Committed synthetic Page 1 fallback (all 3 metrics, res 8)
    |-- sample_index_day_sections.csv  # Committed synthetic Page 2 fallback (+ hour_bucket, res 9)
    `-- map_2/*.csv            # Legacy local index exports; ignored by Git, no longer read
```

## 7. Planned evolution

- Add further BigQuery tables/pages only when their approved schema and purpose
  are provided.
- Company authentication/authorization via Cloud Run auth + IAP (see README).
- Browser-level tests for Streamlit widget interactions.

## 8. Acceptance criteria for the current version

- Page 1 queries its three BigQuery tables from configuration with no code
  changes, aggregating (`AVG`) server-side in two steps, with the segment filter
  bound as a query parameter.
- Page 2 queries its three `*_day_sections` BigQuery tables from configuration,
  aggregating (`AVG`) server-side in one step, with the segment filter and the
  day-part both bound as query parameters.
- Missing BigQuery config/permissions show an actionable message on **both**
  pages; the local fallback still works on both.
- Each page's metric radio switches Overall / Volume / Exclusivity, each from
  its own table.
- Page 2 has a day-part filter covering every `hour_bucket` value in its
  tables, and always aggregates within exactly one day-part.
- All discovered segments appear as independent checkboxes on each page.
- Valid H3 cells render in their correct UAE locations, at the resolution the
  source table actually uses (9 for the live tables). Page 2 renders tens of
  thousands of cells; a count in the tens means it fell back to synthetic data.
- Changing the basemap does not alter the data or active filters.
- The former CSV "Overall analysis index" map and the hourly `user_count` map,
  with their data, helpers, and tests, are gone.
- The app starts using the commands documented in `README.md`.
