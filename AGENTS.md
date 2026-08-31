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
- **Page 2 — `pages/2_Index_Analysis.py`** — local-CSV index map
  (`exclusivity_index` / `volume_index`). Placeholder for the future day-part
  (morning / noon / evening) BigQuery version.

Never render two maps on one page. Index values are **averaged, never summed**;
never join the metric tables/files. The former `data/map_3` "Overall analysis
index" CSV map and the hourly `user_count` map have been removed.

## BigQuery rules

- Config only: `BIGQUERY_PROJECT_ID` + `BIGQUERY_DATASET` +
  `BIGQUERY_OVERALL_INDEX_TABLE` / `BIGQUERY_VOLUME_INDEX_TABLE` /
  `BIGQUERY_EXCLUSIVITY_INDEX_TABLE`, or a per-metric
  `BIGQUERY_<METRIC>_TABLE_FQN`. Never hard-code a project, dataset, table,
  credential, or SQL value. Values live in `.env.example` → `cp .env.example .env`
  (project `maddictdata`, dataset `OOH_Analysis`). Verify with
  `python3 scripts/check_bigquery.py`.
- The segment filter travels as `@segments`; never interpolate user input into
  SQL. Only the validated FQN and the allow-listed metric name reach SQL text.
- Aggregate in BigQuery: `SELECT h3_id, AVG(<metric>) ... GROUP BY h3_id`.
- Application Default Credentials only. Cache results with `st.cache_data`.
- Query construction lives in `h3_analysis/bigquery_source.py` (Streamlit-free,
  unit tested in `tests/test_bigquery.py`).

## Verification

Run `python3 -m unittest discover -s tests`, a syntax check, and start the app
with `H3_DATA_SOURCE=local`. See the full checklist in `CLAUDE.md`.

## Project subagents

Reusable specialist prompts live in `.cursor/agents/`
(`streamlit-ux-engineer`, `h3-data-engineer`, `demo-qa-reviewer`). Use the
smallest relevant specialist during implementation and invoke the QA reviewer
after changes are complete.
