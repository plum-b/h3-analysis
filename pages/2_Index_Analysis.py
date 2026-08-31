"""Page 2 - Index analysis map (local CSV placeholder).

This page reads local CSVs (``data/map_2/exclusivity_index.csv`` and
``data/map_2/volume_index.csv``) or the committed synthetic
``data/sample_index.csv``. It has no hour filter because those exports carry no
time column.

Roadmap: Page 2 becomes the *day-part* (morning / noon / evening / ...) BigQuery
version of the index analysis, backed by its own three tables, once their
approved schema is provided. See ``README.md``.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from h3_analysis.colors import (
    METRIC_HELP,
    METRIC_LABELS,
    colors_for,
    format_value,
    legend_html,
)
from h3_analysis.data import (
    INDEX_METRICS,
    DataValidationError,
    ValidationResult,
    aggregate_index_cells,
    collapse_index_duplicates,
    validate_index_data,
)
from h3_analysis.mapping import BASEMAP_OPTIONS, basemap_style, render_h3_map

st.set_page_config(page_title="H3 Analysis - Index", page_icon="🧭", layout="wide")

MAP_2_DIR = "data/map_2"
INDEX_FILES = {metric: f"{MAP_2_DIR}/{metric}.csv" for metric in INDEX_METRICS}
LOCAL_SAMPLE_FILE = "data/sample_index.csv"


def index_path_for(metric: str) -> str | None:
    """Prefer the per-metric export, fall back to the synthetic sample."""
    if os.path.exists(INDEX_FILES[metric]):
        return INDEX_FILES[metric]
    if os.path.exists(LOCAL_SAMPLE_FILE):
        return LOCAL_SAMPLE_FILE
    return None


@st.cache_data(show_spinner="Loading and validating index data...")
def load_index_data(path: str, metric: str, modified_ns: int) -> ValidationResult:
    """Load one index export, keeping only the columns this metric needs."""
    del modified_ns
    wanted = {"h3_id", "segment", metric}
    frame = pd.read_csv(path, usecols=lambda column: column in wanted)
    result = validate_index_data(frame, metric)
    return ValidationResult(
        data=collapse_index_duplicates(result.data, metric),
        removed_rows=result.removed_rows,
    )


st.title("Index analysis")
st.caption(
    "Page 2 of 2. How each segment is distributed across the grid. These "
    "exports carry no hour column, so this page has no two-hour period filter."
)

index_metric = st.radio(
    "Index metric",
    INDEX_METRICS,
    format_func=lambda metric: METRIC_LABELS[metric],
    horizontal=True,
    key="index_metric",
)
st.caption(METRIC_HELP[index_metric])

index_path = index_path_for(index_metric)

if index_path is None:
    st.info(f"Add `{INDEX_FILES[index_metric]}` to enable this page.")
    st.stop()

try:
    index_validation = load_index_data(
        index_path, index_metric, os.stat(index_path).st_mtime_ns
    )
except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
    st.error(f"Unable to use {index_path}: {error}")
    st.stop()

if index_validation.removed_rows:
    st.warning(
        f"Excluded {index_validation.removed_rows:,} invalid row(s) from "
        f"{index_metric}."
    )

data = index_validation.data

st.sidebar.subheader("Segments")
segment_values = sorted(data["segment"].unique().tolist())
select_all = st.sidebar.checkbox("Select all", value=True)
selected_segments = [
    segment
    for segment in segment_values
    if st.sidebar.checkbox(
        segment, value=select_all, key=f"segment_{select_all}_{segment}"
    )
]
if not selected_segments:
    st.warning("Select at least one segment to display the map.")
    st.stop()

map_style = st.radio("Basemap", BASEMAP_OPTIONS, horizontal=True, key="basemap")
style_url = basemap_style(map_style)

index_cells = aggregate_index_cells(data, selected_segments, index_metric)

st.subheader(f"{METRIC_LABELS[index_metric]} by H3 cell")

if index_cells.empty:
    st.info("No H3 cells match the selected segments.")
    st.stop()

values = index_cells[index_metric]
index_cells = index_cells.assign(
    fill_color=colors_for(values, index_metric, map_style == "Dark"),
    metric_label=[format_value(value, index_metric) for value in values],
)

summary_columns = st.columns(3)
summary_columns[0].metric("Visible H3 cells", f"{len(index_cells):,}")
summary_columns[1].metric(
    "Median", format_value(float(values.median()), index_metric)
)
summary_columns[2].metric(
    "Maximum", format_value(float(values.max()), index_metric)
)

render_h3_map(
    index_cells,
    "fill_color",
    f"H3: {{h3_id}}\n{METRIC_LABELS[index_metric]}: {{metric_label}}",
    style_url,
)
st.markdown(
    legend_html(
        index_metric,
        float(values.min()),
        float(values.max()),
        map_style == "Dark",
    ),
    unsafe_allow_html=True,
)
st.caption(
    "Values are averaged across the repeated rows for each cell and segment, "
    "then across the selected segments."
)

st.download_button(
    "Download index results",
    data=index_cells[["h3_id", index_metric]].to_csv(index=False).encode("utf-8"),
    file_name=f"h3-{index_metric}.csv",
    mime="text/csv",
    key="download_index",
)

with st.expander(f"{METRIC_LABELS[index_metric]} results"):
    st.dataframe(
        index_cells[["h3_id", index_metric]],
        width="stretch",
        hide_index=True,
    )
