# CLAUDE.md

## Project purpose

This repository contains a Streamlit application for exploring H3 audience data
on a map, focused on the UAE. It has **exactly two map pages**, each on its own
Streamlit page because they use different data and analysis logic:

- **Page 1 — `app.py` — index-analysis map.** Filters by audience segment only
  (no time column). A radio switches the metric between `overall_index`,
  `volume_index` and `exclusivity_index`; each metric is a **separate BigQuery
  table** with schema `h3_id` / `segment` / `<metric>`; each `(h3_id, segment)`
  pair is **repeated** (~8x on the live tables, unevenly). The map averages in
  **two steps**: within each pair, then across the selected segments. **Data source is BigQuery.** A local CSV (upload or the
  synthetic `data/sample_index.csv`) exists only as an explicit development
  fallback; production never reads a production CSV.
- **Page 2 — `pages/2_Index_Analysis.py` — index-analysis map (day-part
  placeholder).** Segment filter only; metric switches `exclusivity_index` /
  `volume_index`. Reads local CSVs (`data/map_2/` or `data/sample_index.csv`).

Never show two maps on one page. Index values are **averaged, never summed**.
Never join the metric tables/files on `h3_id` + `segment`.

### Current phase and future plan

- Immediate implementation: BigQuery for **Page 1 only** (the three
  `*_filtered` index tables).
- Future: migrate Page 2 to its own three BigQuery tables that add a day-part
  (morning / noon / evening / …) dimension, giving Page 2 a time-of-day filter.
  Add further tables/pages only when their approved schema and purpose are
  provided. The former CSV "Overall analysis index" (`data/map_3`) map and the
  hourly `user_count` map have been **removed**; `overall_index` now exists only
  as a Page 1 BigQuery metric.

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

### Page 1 — BigQuery (three index tables)

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
| `H3_DATA_SOURCE=local` | default the sidebar to the local CSV fallback |

Current values live in the committed `.env.example` (project `maddictdata`,
dataset `OOH_Analysis`, tables `h3_analysis_indexed_filtered`,
`h3_analysis_volume_index_filtered`, `h3_analysis_exclusivity_index_filtered`).
Developers `cp .env.example .env`; `h3_analysis/config.py` loads that file
without overriding real environment variables. `.env` is Git-ignored and holds
identifiers only — never credentials.

Each table's columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | STRING | Valid H3 cell index |
| `segment` | STRING | Audience/category used by the checkboxes |
| `<metric>` | numeric ≥ 0 | Column named exactly `overall_index` / `volume_index` / `exclusivity_index` |

There is **no time / hour column**. Each `(h3_id, segment)` pair is **repeated**
— ~8.4 rows per pair on the live tables, and the count varies by pair, so the
duplication is uneven.

Rules:

- The segment filter is always passed as a query parameter
  (`@segments` ARRAY<STRING>). Never interpolate a user-controlled value into
  SQL. Only the validated table FQN and the metric name (checked against
  `PAGE1_METRICS`) reach the SQL text.
- Aggregate in BigQuery in **two steps**: `AVG` grouped by `(h3_id, segment)` in
  a CTE, then `AVG` of that grouped by `h3_id`. Never collapse this into one
  `AVG ... GROUP BY h3_id` — that is a weighted average dominated by whichever
  pair carries more duplicate rows; measured on the live tables it changed 48.6%
  of cells, by up to 108% relative. Return only `h3_id` and the metric.
- Cache query results with `st.cache_data` keyed by table FQN + metric +
  segments.
- Credentials: Application Default Credentials only. Local dev uses
  `gcloud auth application-default login`; Cloud Run uses its attached service
  account. Never commit a service-account JSON key.
- Show an actionable in-app message when config or permissions are missing.

### Page 1 — local development fallback

The committed synthetic `data/sample_index.csv` (columns `h3_id`, `segment`,
`overall_index`, `volume_index`, `exclusivity_index`), or a sidebar CSV upload
with `h3_id`, `segment` and the active metric column. Production must not depend
on a production CSV.

Never commit production exports, credentials, personal data, or Streamlit
secrets.

### Page 2 — Index dataset (local CSV)

`data/map_2/exclusivity_index.csv` and `data/map_2/volume_index.csv` feed the
second page.

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | string | Valid H3 cell index |
| `segment` | string | Audience values shared with Page 1 |
| `exclusivity_index` or `volume_index` | numeric | Finite and >= 0 |

`data/sample_index.csv` also satisfies this contract. These files have **no time
column**. A cell/segment pair may repeat with nothing to distinguish the rows,
so values are averaged (`collapse_index_duplicates`) rather than summed; summing
would push a normalized index outside its range. Do not join the index files on
`h3_id` and `segment`: the duplicate keys turn the join into a cartesian
product (1.7M rows becomes 16.4M).

## Implementation conventions

- Keep `app.py` as the entry point and Page 1. Additional pages live in
  `pages/`.
- Reusable data-access and helper logic lives outside the UI:
  `h3_analysis/bigquery_source.py` (Streamlit-free, unit tested),
  `h3_analysis/data.py` (validation/aggregation),
  `h3_analysis/colors.py`, `h3_analysis/mapping.py` (shared PyDeck rendering).
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
- A true satellite or terrain basemap requires a compatible provider and may
  require an API token. CARTO Voyager is a road basemap, not terrain imagery.

## Verification checklist

Before considering a change complete:

0. When BigQuery behaviour changed, run `python3 scripts/check_bigquery.py`
   (needs `gcloud auth application-default login`).
1. Run a Python syntax check and the test suite
   (`python3 -m unittest discover -s tests`).
2. Start the Streamlit app with the synthetic local fallback
   (`H3_DATA_SOURCE=local`).
3. Confirm every segment checkbox works on both pages.
4. Confirm the Page 1 metric radio switches between all three index metrics.
5. Confirm H3 cells appear in the UAE and tooltips show the correct values.
6. Confirm each basemap option visibly changes the map and browser/server logs
   contain no new errors.
7. Confirm Page 1 shows an actionable error (not a traceback) when BigQuery
   config or permissions are missing.

## Project subagents

Reusable specialist prompts live in `.cursor/agents/`:

- `streamlit-ux-engineer`: performance, layout, controls, maps, and demo polish
- `h3-data-engineer`: schema validation, H3 correctness, and aggregation
- `demo-qa-reviewer`: independent final review and pre-demo verification

Use the smallest relevant specialist during implementation and invoke the QA
reviewer after changes are complete.
