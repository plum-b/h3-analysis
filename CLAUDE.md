# CLAUDE.md

## Project purpose

This repository contains a Streamlit application for exploring H3 audience data
on a map, focused on the UAE. It has **exactly two map pages**, each on its own
Streamlit page because they use different data and analysis logic:

- **Page 1 — `pages/1_Two-Hour_Index_Analysis.py` — "Two-Hour Index
  Analysis".** Filters by audience segment **and one two-hour period of the
  day**. A radio switches the metric between `overall_index`, `volume_index`
  and `exclusivity_index`; each metric is a **separate BigQuery table** with
  schema `h3_id` / `segment` / `<metric>` / `hour_bucket`, where `hour_bucket`
  is an **INT64 two-hour period** (0, 2, 4 … 22). Each `(h3_id, segment)` pair
  carries ~8.4 rows because it spans the twelve periods; each
  `(h3_id, segment, hour_bucket)` triple appears exactly once. The map filters
  to one period, then averages in **two steps**: within each
  `(h3_id, segment, hour_bucket)` group, then across the selected segments.
  **Data source is BigQuery.** A local CSV (upload or the synthetic
  `data/sample_index_two_hours.csv`) exists only as an explicit development
  fallback; production never reads a production CSV.
- **Page 2 — `pages/2_Day-Part_Index_Analysis.py` — "Day-Part Index
  Analysis".** Filters by audience segment **and day-part**. A radio switches the metric
  between the same three names (`overall_index`, `volume_index`,
  `exclusivity_index`); each metric is a **separate `*_day_sections` BigQuery
  table** with schema `h3_id` / `segment` / `<metric>` / `hour_bucket`. Unlike
  Page 1, each `(h3_id, segment, hour_bucket)` triple is **not repeated**, so
  the map averages in **one step** across the selected segments. **Data source
  is BigQuery.** A local CSV (upload or the synthetic
  `data/sample_index_day_sections.csv`) is a development fallback only.

Never show two maps on one page. Index values are **averaged, never summed**.
Never join the metric tables/files on `h3_id` + `segment`.

### Current phase and future plan

- **Both pages are now implemented on BigQuery**: Page 1 on the three
  `*_filtered` index tables sliced to one two-hour period, Page 2 on the three
  `*_filtered_day_sections` tables filtered to one day-part.
- The two pages read the same column name, `hour_bucket`, with different types
  and meanings: INT64 two-hour periods on Page 1, STRING day-part labels on
  Page 2. Never mix them, and never add the two-hour slicer to Page 2.
- Add further tables/pages only when their approved schema and purpose are
  provided. The former CSV "Overall analysis index" (`data/map_3`) map and the
  hourly `user_count` map have been **removed**; `overall_index` now exists as a
  BigQuery metric on both pages (from different tables).

## Runtime

- Python 3.10+
- Streamlit for the application UI
- pandas for loading, filtering, and aggregating data
- h3 for H3 validation and cell centroids
- pydeck/deck.gl for map rendering
- google-cloud-bigquery for the Page 1 production data source (ADC only)

Run the app from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

On PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Data contract

### Page 1 — BigQuery (three two-hour index tables)

Production reads configuration-driven fully qualified BigQuery tables, one per
metric. Never hard-code a project id, dataset, table, credentials, or SQL
filter values.

| Env var | Meaning |
| --- | --- |
| `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` | shared project + dataset |
| `BIGQUERY_OVERALL_INDEX_TABLE` | table for `overall_index` |
| `BIGQUERY_VOLUME_INDEX_TABLE` | table for `volume_index` |
| `BIGQUERY_EXCLUSIVITY_INDEX_TABLE` | table for `exclusivity_index` |
| `BIGQUERY_<METRIC>_TABLE_FQN` | optional per-metric full-FQN override |
| `BIGQUERY_BILLING_PROJECT` | project the query **jobs** are billed to; defaults to `BIGQUERY_PROJECT_ID` |
| `H3_BASEMAP_STYLE_URL` | MapLibre style.json URL for the detailed basemap |
| `H3_DATA_SOURCE=local` | default the sidebar to the local CSV fallback |

Never construct `bigquery.Client()` without a project. The billing project is
resolved by `bigquery_source.billing_project()`; leaving it to Application
Default Credentials bills jobs to whatever project the local `gcloud` config
happens to point at, producing a `bigquery.jobs.create` permission error that
names a project appearing nowhere in this repository — while the tables
themselves are perfectly readable.

The committed `.env.example` carries **placeholders only** - the repository is
public, so the real project, dataset and table names are not in Git. Get them
from the dataset owner. The template shape is (project `your-gcp-project`,
dataset `your_dataset`, tables `overall_index_table`,
`volume_index_table`, `exclusivity_index_table`).
Developers `cp .env.example .env`; `h3_analysis/config.py` loads that file
without overriding real environment variables. `.env` is Git-ignored and holds
identifiers only — never credentials.

Each table's columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | STRING | Valid H3 cell index |
| `segment` | STRING | Audience/category used by the checkboxes |
| `<metric>` | numeric ≥ 0 | Column named exactly `overall_index` / `volume_index` / `exclusivity_index` |
| `hour_bucket` | INT64 | **Two-hour period** of the day: 0, 2, 4 … 22 |

Measured facts (BigQuery, read-only), identical on all three tables:

- ~1.26M rows, 3 segments, and exactly **twelve** `hour_bucket` values
  (0, 2, 4 … 22); no NULLs.
- Each `(h3_id, segment, hour_bucket)` triple appears **exactly once**
  (max repeat count = 1). The ~8.4 rows per `(h3_id, segment)` pair are the
  periods that pair appears in — not undifferentiated duplicates.
- One period covers ~39.5k cells for a single segment.

Rules:

- The segment filter is always passed as a query parameter
  (`@segments` ARRAY<STRING>) and the period as `@two_hour_period`
  (INT64 scalar), validated by `coerce_two_hour_period` before it is bound.
  Never interpolate a user-controlled value into SQL. Only the validated table
  FQN and the metric name (checked against `PAGE1_METRICS`) reach the SQL text.
- Always filter to **exactly one** two-hour period before aggregating. The
  slicer offers whatever periods the table contains — the 0/2/…/22 domain is
  never hard-coded.
- Aggregate in BigQuery in **two steps**: `AVG` grouped by
  `(h3_id, segment, hour_bucket)` in a CTE, then `AVG` of that grouped by
  `h3_id`. Never collapse this into one `AVG ... GROUP BY h3_id` — that is a
  row-weighted average in which a segment contributing more rows dominates the
  cell. Return only `h3_id` and the metric.
- Cache query results with `st.cache_data` keyed by table FQN + metric +
  segments + period.
- Credentials: Application Default Credentials only. Local dev uses
  `gcloud auth application-default login`; Cloud Run uses its attached service
  account. Never commit a service-account JSON key.
- Show an actionable in-app message when config or permissions are missing.

### Page 1 — local development fallback

The committed synthetic `data/sample_index_two_hours.csv` (columns `h3_id`,
`segment`, `hour_bucket`, `overall_index`, `volume_index`,
`exclusivity_index`; 250 resolution-9 UAE cells x 3 segments x 12 two-hour
periods), or a sidebar CSV upload with the same columns. Production must not
depend on a production CSV.

`data/sample_index.csv` has **no `hour_bucket` column** and is a 45-cell
resolution-8 sample — it no longer satisfies Page 1 and must not be pointed at
either page.

Never commit production exports, credentials, personal data, or Streamlit
secrets.

### Page 2 — BigQuery (three day-section index tables)

Same configuration pattern as Page 1, with a `_DAY_SECTIONS` infix so the two
pages' variables never collide.

| Env var | Meaning |
| --- | --- |
| `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` | shared project + dataset (same as Page 1) |
| `BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE` | table for `overall_index` |
| `BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE` | table for `volume_index` |
| `BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE` | table for `exclusivity_index` |
| `BIGQUERY_<METRIC>_DAY_SECTIONS_TABLE_FQN` | optional per-metric full-FQN override |

`.env.example` shows the shape with placeholders (project `your-gcp-project`, dataset
`your_dataset`, tables `overall_index_day_sections_table`,
`volume_index_day_sections_table`,
`exclusivity_index_day_sections_table`).

Each table's columns — **verified against the live tables**:

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | STRING | Valid H3 cell index, **resolution 9** |
| `segment` | STRING | Audience/category (`Families`, `HNWI`, `Potential Car Buyers`) |
| `<metric>` | FLOAT ≥ 0 | Column named exactly `overall_index` / `volume_index` / `exclusivity_index` |
| `hour_bucket` | STRING | Day-part: `Morning`, `Noon`, `After noon`, `Night`, `Other` |

Note the column name is `hour_bucket` even though the values are **day-part
labels, not hours** — do not assume the old numeric 0/2/…/22 hour buckets.

Measured facts (BigQuery, read-only):

- ~601k rows and ~60.2k distinct `h3_id` per table; 3 segments × 5 day-parts.
- All sampled `h3_id` values are valid resolution-9 cells inside the UAE
  (lat ≈ 22.67–26.04, lon ≈ 51.62–56.37).
- No NULL and no negative metric values in any of the three tables.
- **Each `(h3_id, segment, hour_bucket)` triple appears exactly once**
  (max repeat count = 1). This is the key difference from Page 1, whose pairs
  repeat ~8x.

Rules:

- Segment filter is `@segments` (ARRAY<STRING>) and the day-part is
  `@hour_bucket` (scalar STRING). Never interpolate user values into SQL. Only
  the validated table FQN and the metric name (checked against `PAGE2_METRICS`)
  reach the SQL text.
- Aggregate in BigQuery in **one step**: `AVG(<metric>) ... WHERE segment IN
  UNNEST(@segments) AND hour_bucket = @hour_bucket GROUP BY h3_id`. Do **not**
  copy Page 1's two-step per-pair CTE here — with no duplicate triples it would
  only average single-row groups, adding cost and confusion for an identical
  result.
- Always filter to exactly one day-part before aggregating. Averaging across
  day-parts would silently reproduce Page 1's numbers instead of a
  time-of-day view.
- Cache with `st.cache_data` keyed by table FQN + metric + segments + day-part.
- Same ADC credential and actionable-error rules as Page 1.

### Page 2 — local development fallback

The committed synthetic `data/sample_index_day_sections.csv` (columns `h3_id`,
`segment`, `hour_bucket`, `overall_index`, `volume_index`,
`exclusivity_index`; 250 resolution-9 UAE cells × 3 segments × 5 day-parts), or
a sidebar CSV upload with the same columns.

**Do not** point Page 2 back at `data/map_2/*.csv` or `data/sample_index.csv`:
those carry no `hour_bucket`, only two metrics, and `data/sample_index.csv` is a
45-cell **resolution-8** sample. See "Page 2 investigation" below.

### Page 2 investigation — why the old map showed few cells, some in the ocean

Root cause: **Page 2 was never connected to its real data.** It read
`data/map_2/exclusivity_index.csv` / `volume_index.csv`, but that directory is
empty (and `.gitignore`d), so `index_path_for()` silently fell through to
`data/sample_index.csv` — a committed **synthetic** file. The map was therefore
rendering placeholder data, not the day-section dataset.

That explains both symptoms:

- **"Only a few H3 cells"** — `data/sample_index.csv` holds just **45 distinct
  cells** (178 rows), versus ~60.2k distinct cells in the real tables.
- **"Some cells in the ocean"** — the sample is **resolution 8**, while the real
  data is **resolution 9**. A resolution-8 hexagon covers ~7x the area of a
  resolution-9 one, so a coarse cell centred near the coast visibly spills over
  the shoreline. The H3 IDs themselves were valid and their centroids correct;
  nothing was wrong with the geometry, centroid handling, or any join.

Ruled out during the investigation:

- H3 validity/geometry: every sampled ID in both the CSV and the live tables is
  a valid cell; `h3.cell_to_latlng` centroids land inside the UAE.
- Coordinate handling: `h3_analysis/mapping.py` is shared with the known-good
  Page 1 and was already correct — no lat/lon swap, no CRS issue.
- Joins: there is **no join** in the Page 2 path (and none should be added);
  the metric column travels with `h3_id` in one table.

The fix was to connect Page 2 to its own three `*_day_sections` BigQuery
tables, add the `hour_bucket` day-part filter, and replace the misleading
fallback with a resolution-9 synthetic file that matches the real schema.

## Implementation conventions

- Keep `app.py` as the entry point. It is a thin router: it calls
  `st.set_page_config` once and registers both pages with `st.navigation`, which
  is what sets their sidebar labels. Page bodies live in `pages/` and must not
  call `st.set_page_config` themselves. Streamlit names the entry script in the
  sidebar after its *filename*, so while Page 1 lived in `app.py` the navigation
  read "app" whatever `page_title` said.
- Reusable data-access and helper logic lives outside the UI:
  `h3_analysis/bigquery_source.py` (Streamlit-free, unit tested),
  `h3_analysis/data.py` (validation/aggregation),
  `h3_analysis/colors.py`, `h3_analysis/mapping.py` (shared PyDeck rendering
  plus the shared sidebar `segment_checkboxes`).
- Both pages share `render_h3_map` / `map_center` / `segment_checkboxes`; keep
  them identical rather than forking per-page copies.
- Cache file loading and expensive transformations with `st.cache_data` when
  appropriate.
- Validate required columns, numeric values, and H3 indexes before rendering;
  show actionable Streamlit errors instead of raw tracebacks.
- Aggregate the metric (`AVG`) after the segment filter is applied; never sum
  index values.
- Preserve the UAE fallback center, but derive the normal map center from the
  currently displayed H3 cells.
- Do not add a map-provider token to source code. Use `.streamlit/secrets.toml`
  or environment variables for secrets.
- The default basemap is OpenFreeMap Liberty (`H3_BASEMAP_STYLE_URL` overrides
  it): an OpenMapTiles style with building footprints, 28 road classes, POI and
  place labels, and a Natural Earth shaded-relief source, so it carries real
  relief rather than being a road basemap relabelled as terrain. It needs no
  token. CARTO Voyager remains the always-reachable fallback.
- Streamlit renders deck.gl from JSON and requires `mapStyle` to be a style
  **URL string**. Passing an inline MapLibre style object fails in the browser
  with `e.mapStyle?.indexOf is not a function`, so basemap choices must resolve
  to URLs.
- Resolution-9 cells tile a city solidly. Keep the H3 layer near `opacity=0.45`
  so streets and buildings stay readable underneath.

## Verification checklist

Before considering a change complete:

0. When BigQuery behaviour changed, run `python3 scripts/check_bigquery.py`
   (needs `gcloud auth application-default login`).
1. Run a Python syntax check and the test suite
   (`python3 -m unittest discover -s tests`).
2. Start the Streamlit app with the synthetic local fallback
   (`H3_DATA_SOURCE=local`).
3. Confirm every segment checkbox works on both pages.
4. Confirm the metric radio switches between all three index metrics on **both**
   pages.
4b. Confirm the Page 1 two-hour slicer moves through all twelve periods and that
   the cell count/values change with it, and that Page 2 has no such slicer.
5. Confirm the Page 2 day-part radio switches between all five day-parts and
   that the cell count/values change with it.
6. Confirm H3 cells appear in the UAE and tooltips show the correct values.
   Page 2 should render tens of thousands of cells, not tens — a suspiciously
   small count means it fell back to synthetic data.
7. Confirm each basemap option visibly changes the map and browser/server logs
   contain no new errors.
8. Confirm **both** pages show an actionable error (not a traceback) when
   BigQuery config or permissions are missing.

## Project subagents

Reusable specialist prompts live in `.cursor/agents/`:

- `streamlit-ux-engineer`: performance, layout, controls, maps, and demo polish
- `h3-data-engineer`: schema validation, H3 correctness, and aggregation
- `demo-qa-reviewer`: independent final review and pre-demo verification

Use the smallest relevant specialist during implementation and invoke the QA
reviewer after changes are complete.
