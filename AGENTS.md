# AGENTS.md

The authoritative contributor and AI-agent guidance for this repository lives in
[`CLAUDE.md`](CLAUDE.md). Read it first. This file only highlights what agents
most often need.

## One-line summary

A Streamlit app with **exactly two H3 index-analysis map pages**:

- **Page 1 — `app.py`** — index map. Filter: audience `segment` only (no time
  column). A radio switches the metric between `overall_index`, `volume_index`
  and `exclusivity_index`; **each metric is its own BigQuery table**
  (`h3_id` / `segment` / `<metric>`; each pair is **repeated ~8x, unevenly**).
  The map averages **in two steps** — within each `(h3_id, segment)` pair, then
  across the selected segments. A single `AVG ... GROUP BY h3_id` is wrong: it
  weights by duplicate count and shifted 48.6% of cells on the live data. **Data source is BigQuery**;
  `data/sample_index.csv` (or an upload) is a development fallback only.
- **Page 2 — `pages/2_Index_Analysis.py`** — day-part index map. Filters:
  audience `segment` **and** `hour_bucket` (day-part). Same three metrics, each
  its own **`*_day_sections` BigQuery table**
  (`h3_id` / `segment` / `<metric>` / `hour_bucket`). Unlike Page 1, each
  `(h3_id, segment, hour_bucket)` triple appears **exactly once**, so it
  averages in **one step** — do not copy Page 1's per-pair CTE here. **Data
  source is BigQuery**; `data/sample_index_day_sections.csv` (or an upload) is a
  development fallback only.

Never render two maps on one page. Index values are **averaged, never summed**;
never join the metric tables/files. The former `data/map_3` "Overall analysis
index" CSV map and the hourly `user_count` map have been removed.

⚠️ Page 2's column is named `hour_bucket` but holds **day-part labels**
(`Morning`, `Noon`, `After noon`, `Night`, `Other`) — not the old numeric
0/2/…/22 hour buckets. Always filter to exactly one day-part before
aggregating.

## BigQuery rules

- Config only. Page 1: `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` +
  `BIGQUERY_OVERALL_INDEX_TABLE` / `BIGQUERY_VOLUME_INDEX_TABLE` /
  `BIGQUERY_EXCLUSIVITY_INDEX_TABLE`. Page 2: the same with a `_DAY_SECTIONS`
  infix (`BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE`, …). Either accepts a
  per-metric `BIGQUERY_<METRIC>[_DAY_SECTIONS]_TABLE_FQN`. Never hard-code a
  project, dataset, table, credential, or SQL value. Values live in
  `.env.example` → `cp .env.example .env` (project `maddictdata`, dataset
  `OOH_Analysis`). Verify with `python3 scripts/check_bigquery.py` (checks both
  pages).
- Filters travel as `@segments` (and `@hour_bucket` on Page 2); never
  interpolate user input into SQL. Only the validated FQN and the allow-listed
  metric name reach SQL text.
- Aggregate in BigQuery. Page 1 is **two-step** (per-pair CTE, because pairs
  repeat ~8x); Page 2 is **one-step** (`GROUP BY h3_id`, because triples do
  not repeat). Do not swap these.
- Application Default Credentials only. Cache results with `st.cache_data`.
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
