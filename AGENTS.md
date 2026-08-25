# AGENTS.md

## Project purpose

This repository contains a Streamlit application for exploring time-bucketed H3
data on a map, initially focused on the UAE. The app reads a local CSV or a CSV
uploaded through the sidebar, filters it by audience segment and two-hour bucket,
aggregates the metric by H3 cell, and displays the result with PyDeck.

## Runtime

- Python 3.10+
- Streamlit for the application UI
- pandas for loading, filtering, and aggregating data
- h3 for H3 validation and cell centroids
- pydeck/deck.gl for map rendering

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

The default input is `data/map_1/every_2_hours.csv`. It is intentionally
ignored by Git because production data may be large or sensitive. Uploaded
files must use the same logical fields:

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | string | Valid H3 cell index |
| `hour_bucket` | integer | Start hour of a two-hour bucket: 0, 2, ..., 22 |
| `segment` | string | Audience/category used by the checkboxes |
| `user_count` | numeric | Metric aggregated by H3 cell |

Never commit production exports, credentials, personal data, or Streamlit
secrets. Keep a small synthetic CSV for development and tests.

### Index dataset

`data/map_2/exclusivity_index.csv` and `data/map_2/volume_index.csv` feed the
second map.

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | string | Valid H3 cell index |
| `segment` | string | Same audience values as the hourly export |
| `exclusivity_index` or `volume_index` | numeric | Finite and >= 0 |

These files have **no `hour_bucket` column**, so the second map has no time
filter. Each cell/segment pair repeats up to twelve times — the count of
two-hour buckets — with nothing to distinguish the rows, so the values are
averaged rather than summed; summing would push a normalized index outside its
range. Do not join the two index files on `h3_id` and `segment`: the duplicate
keys turn the join into a cartesian product (1.7M rows becomes 16.4M).

### Overall analysis index

`data/map_3/analysis_indexed_filtered.csv` and `data/map_3/analysis_indexed.csv`
feed the third map. Schema matches the index exports, with metric column
`overall_index` (finite and >= 0). Same averaging rules and no hour filter;
the UI switches between Filtered and Full datasets rather than between metrics.
Do not join the two analysis files.

## Implementation conventions

- Keep `app.py` as the entry point.
- Put reusable data validation, transformation, and map helpers into small
  modules if `app.py` grows substantially.
- Cache file loading and expensive transformations with `st.cache_data` when
  appropriate.
- Validate required columns, numeric values, hour buckets, and H3 indexes before
  rendering; show actionable Streamlit errors instead of raw tracebacks.
- Aggregate `user_count` after all active filters are applied.
- Preserve the UAE fallback center, but derive the normal map center from the
  currently displayed H3 cells.
- Do not add a map-provider token to source code. Use `.streamlit/secrets.toml`
  or environment variables for secrets.
- A true satellite or terrain basemap requires a compatible provider and may
  require an API token. CARTO Voyager is a road basemap, not terrain imagery.

## Verification checklist

Before considering a change complete:

1. Run a Python syntax check or test suite.
2. Start the Streamlit app with the synthetic sample data.
3. Confirm every segment checkbox works.
4. Confirm all hour buckets can be selected.
5. Confirm H3 cells appear in the UAE and tooltips show the correct values.
6. Confirm each basemap option visibly changes the map and browser/server logs
   contain no new errors.

## Project subagents

Reusable specialist prompts live in `.cursor/agents/`:

- `streamlit-ux-engineer`: performance, layout, controls, maps, and demo polish
- `h3-data-engineer`: schema validation, H3 correctness, and aggregation
- `demo-qa-reviewer`: independent final review and pre-demo verification

Use the smallest relevant specialist during implementation and invoke the QA
reviewer after changes are complete.
