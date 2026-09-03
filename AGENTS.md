# AGENTS.md

The authoritative contributor and AI-agent guidance for this repository lives in
[`CLAUDE.md`](CLAUDE.md). Read it first. This file only highlights what agents
most often need.

## One-line summary

A Streamlit app with **exactly two H3 index-analysis map pages**:

- **Page 1 — `pages/1_Two-Hour_Index_Analysis.py`** — two-hour index map.
  Filters: exactly one audience `segment` **and** exactly one two-hour period.
  A radio switches the metric between `overall_index` and `volume_index`;
  **each metric is its own BigQuery table**
  (`h3_id` / `segment` / `<metric>` / `hour_bucket`, where `hour_bucket` is the
  **INT64** two-hour period 0, 2, 4 … 22). Each `(h3_id, segment, hour_bucket)`
  triple appears exactly once — the ~8.4 rows per `(h3_id, segment)` pair are
  the twelve periods. The map averages **in two steps** — within each
  `(h3_id, segment, hour_bucket)` group, then across the selected segment(s) —
  so a segment contributing more rows cannot dominate a cell. These tables have
  **no `Week_part` column**; never add a Weekday/Weekend selector here. **Data
  source is BigQuery**; `data/sample_index_two_hours.csv` (or an upload) is a
  development fallback only.
- **Page 2 — `pages/2_Day-Part_Index_Analysis.py`** — day-part index map.
  Filters: one audience `segment`, one `hour_bucket` (day-part) **and** one
  `Week_part` (`Weekday` / `Weekend`), always applied together. Same two
  metrics, each its own **`*_day_sections` BigQuery table**
  (`h3_id` / `segment` / `<metric>` / `hour_bucket` / `Week_part`). Unlike
  Page 1, each `(h3_id, segment, hour_bucket, Week_part)` row appears **exactly
  once**, so it averages in **one step** — do not copy Page 1's per-pair CTE
  here. **Data source is BigQuery**; `data/sample_index_day_sections.csv` (or an
  upload) is a development fallback only.

Never render two maps on one page. Index values are **averaged, never summed**;
never join the metric tables/files. The former `data/map_3` "Overall analysis
index" CSV map and the hourly `user_count` map have been removed, and so has the
**exclusivity index metric** — no `exclusivity_index` key, table or query. Note
the display names are separate: `overall_index` is *labelled* "Exclusivity
index" in `METRIC_LABELS` while its table and column keep the overall_index
name.

Segments are picked **one at a time** with the shared sidebar radio
(`h3_analysis.mapping.segment_radio`): no checkboxes, no "select all".

⚠️ Page 2's column is named `hour_bucket` but holds **day-part labels**
(`Morning`, `Noon`, `After noon`, `Night`, `Other`) — not the old numeric
0/2/…/22 hour buckets. Always filter to exactly one day-part before
aggregating. `Week_part` is a separate, capitalised column on those same
tables; filter to exactly one week-part as well.

## BigQuery rules

- Config only. Page 1: `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` +
  `BIGQUERY_OVERALL_INDEX_TABLE` / `BIGQUERY_VOLUME_INDEX_TABLE`.
  Page 2: the same with a `_DAY_SECTIONS`
  infix (`BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE`, …). Either accepts a
  per-metric `BIGQUERY_<METRIC>[_DAY_SECTIONS]_TABLE_FQN`. Never hard-code a
  project, dataset, table, credential, or SQL value. Values live in
  `.env.example` holds placeholders only; `cp .env.example .env` then fill in
  the real names, which are not committed (project `your-gcp-project`, dataset
  `your_dataset`), with jobs billed to `BIGQUERY_BILLING_PROJECT` or
  `BIGQUERY_PROJECT_ID` — never to the ambient `gcloud` default.
  Verify with `python3 scripts/check_bigquery.py` (checks both
  pages).
- Filters travel as `@segments` (the one selected segment) plus
  `@two_hour_period` on Page 1, or `@hour_bucket` **and** `@week_part` on
  Page 2; never interpolate user input into SQL. Only the validated FQN and the
  allow-listed metric name reach SQL text.
- Aggregate in BigQuery. Page 1 is **two-step** (per-pair CTE, because pairs
  repeat ~8x); Page 2 is **one-step** (`GROUP BY h3_id`, because
  `(h3_id, segment, hour_bucket, Week_part)` rows do not repeat). Do not swap
  these.
- Credentials: a `[gcp_service_account]` secret (`st.secrets`) when configured
  - required on Streamlit Community Cloud, which has no metadata server -
  otherwise Application Default Credentials. Never read a key from the repo.
  Cache results with `st.cache_data`.
- Query construction lives in `h3_analysis/bigquery_source.py` (Streamlit-free,
  unit tested in `tests/test_bigquery.py`).

## Gotcha: Page 2 "few cells / cells in the ocean"

If Page 2 renders only a handful of cells, some spilling into the sea, it is
almost certainly falling back to synthetic data rather than a geometry bug. The
old code read the empty, `.gitignore`d `data/map_2/` and silently fell back to
`data/sample_index.csv` — 45 cells at **resolution 8**, while the real data is
~60.2k cells at **resolution 9** (coarser hexes near the coast visibly overhang
the shoreline). Check the sidebar's reported data source first. Full write-up in
`CLAUDE.md` → "Page 2 investigation".

## Verification

Run `python3 -m unittest discover -s tests`, a syntax check, and start the app
with `H3_DATA_SOURCE=local`. See the full checklist in `CLAUDE.md`.

## Project subagents

Reusable specialist prompts live in `.cursor/agents/`
(`streamlit-ux-engineer`, `h3-data-engineer`, `demo-qa-reviewer`). Use the
smallest relevant specialist during implementation and invoke the QA reviewer
after changes are complete.
