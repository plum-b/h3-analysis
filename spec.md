# H3 Analysis Platform Specification

## 1. Objective

Provide analysts with a simple web interface for exploring audience metrics by
H3 cell and two-hour time bucket. The initial geographic scope is the United
Arab Emirates. The application should support local CSV-based analysis now and
allow a future BigQuery data source without changing the user-facing workflow.

## 2. Current user workflow

1. The app loads `data/every_2_hours.csv`, or the user uploads a CSV.
2. The user includes or excludes audience segments using checkboxes.
3. The user selects an `hour_bucket` using a slider.
4. Rows are filtered and `user_count` is summed for each `h3_id`.
5. The map displays one colored H3 polygon per aggregated cell.
6. The user can switch between dark and alternate basemap styles.
7. The user can inspect the filtered aggregate in the raw-data expander.

## 3. Input data contract

Required columns:

| Field | Required format | Validation |
| --- | --- | --- |
| `h3_id` | Text | Non-empty valid H3 index |
| `hour_bucket` | Integer | One of `0, 2, 4, ..., 22` |
| `segment` | Text | Non-empty category |
| `user_count` | Number | Finite and greater than or equal to zero |

Duplicate rows are allowed. Their `user_count` values are summed after filtering.
Unknown extra columns are ignored. Invalid required values should be reported to
the user with the affected row count; the app should not silently place invalid
cells at an unrelated location.

## 4. Functional requirements

### Data loading

- Prefer an uploaded CSV when present.
- Otherwise load the default local CSV.
- If neither exists, show upload instructions and stop cleanly.
- Production data must remain outside version control.

### Filters

- Display one checkbox for each distinct `segment` value.
- Select all segments by default.
- Require at least one selected segment.
- Present sorted two-hour values in the hour slider.
- Keep filter behavior deterministic when an uploaded file changes.

### Aggregation

- Filter by selected segments and hour first.
- Group by `h3_id` and sum `user_count`.
- Use the aggregated value for color, tooltip, and optional elevation.

### Map

- Render cells with PyDeck `H3HexagonLayer`.
- Fit or center the view from displayed H3 cell centroids.
- Fall back to the UAE center (`24.0, 54.0`) for an empty result.
- Provide a dark basemap and one clearly labeled alternate basemap.
- Do not label a road basemap as terrain or satellite.
- Show `h3_id` and aggregated `user_count` in the tooltip.
- Preserve filters when the basemap changes.

### Error states

- Missing required columns: list the missing column names.
- Empty filtered result: show an informational message and the UAE fallback map.
- Invalid H3 values: report them and exclude them from centroid calculation/map.
- Non-numeric counts: report the issue instead of failing during aggregation.

## 4b. Index analysis map

A second map below the first analyses the index dataset. It reuses the H3
rendering, segment checkboxes, basemap choice, tooltips, and map controls.

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
- The dataset has no `hour_bucket` column, so this map ignores the two-hour
  slider. That limitation is stated in the UI.
- Missing columns, non-numeric values, negative values, and invalid H3 indexes
  are reported with the affected row count, as on the first map.

## 4c. Overall analysis index map

A third map below the index map shows `overall_index` from one of two CSVs:

- `data/analysis_indexed_filtered.csv` (Filtered) or `data/analysis_indexed.csv`
  (Full). A radio control switches datasets; the metric is always
  `overall_index`.
- Same schema and aggregation as §4b (average duplicates, then average across
  selected segments). No `hour_bucket`; the hour slider is ignored.
- Uses a teal-green linear sequential ramp, distinct from exclusivity (blue)
  and volume (orange).
- Reuses segment filters, basemap, KPIs, legend, download, and empty/error
  states from the index map.

## 5. Non-functional requirements

- Typical CSVs of tens of thousands of rows should become interactive within a
  few seconds on a standard analyst laptop.
- The application must not expose credentials or production CSVs through Git.
- UI labels should be understandable without knowledge of H3 internals.
- The app should run on Linux/WSL and Windows with Python 3.10 or newer.
- Dependencies should be reproducible and reviewed before deployment.

## 6. Current repository layout

```text
h3-analysis/
|-- app.py                    # Streamlit entry point
|-- h3_analysis/
|   |-- data.py               # Validation, formatting, and aggregation helpers
|   `-- colors.py             # Sequential ramps and scaling for the index maps
|-- tests/
|   |-- test_data.py          # Synthetic data unit tests
|   `-- test_index.py         # Index validation, aggregation, and color scales
|-- requirements.txt          # Python dependencies
|-- README.md                 # Setup and usage
|-- CLAUDE.md                 # Contributor and AI-agent guidance
|-- spec.md                   # Product and data behavior specification
|-- .streamlit/config.toml    # Local Streamlit defaults
|-- .claude/launch.json       # Development launch configuration
`-- data/
    |-- sample.csv            # Small synthetic example
    |-- every_2_hours.csv     # Local production export; ignored by Git
    |-- exclusivity_index.csv # Index export; ignored by Git
    |-- volume_index.csv      # Index export; ignored by Git
    |-- analysis_indexed_filtered.csv  # Overall index (filtered); ignored by Git
    `-- analysis_indexed.csv  # Overall index (full); ignored by Git
```

## 7. Planned evolution

### Near term

- Add a visible color legend and clearer hour labels such as `00:00-02:00`.
- Add browser-level tests for Streamlit widget interactions.

### Later

- Replace or complement CSV loading with parameterized BigQuery queries.
- Cache query results by hour and segment.
- Add company authentication and authorization.
- Deploy in a container behind HTTPS and a company domain.
- Add download/export of the currently filtered aggregate.

## 8. Acceptance criteria for the current version

- The default CSV opens without code changes.
- All discovered segments appear as independent checkboxes.
- Selecting an hour displays only that bucket's aggregate.
- Valid H3 cells render in their correct UAE locations.
- Changing the basemap does not alter the data or active filters.
- The app starts using the commands documented in `README.md`.
