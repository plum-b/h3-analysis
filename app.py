"""Page 1 - Index analysis map (BigQuery).

Page 1 shows one index metric at a time - Overall, Volume or Exclusivity - each
stored in its own BigQuery table with the schema ``h3_id`` / ``segment`` /
``<metric>`` and no hour column. Each ``(h3_id, segment)`` pair is repeated, so
the map averages within each pair first and then across the selected segments
(BigQuery does both steps).

Production reads BigQuery. A local CSV (uploaded, or the committed synthetic
``data/sample_index.csv`` that carries all three metric columns) is an explicit
development fallback; production does not depend on a production CSV.

Page 2 (``pages/2_Index_Analysis.py``) is the local-CSV index map and will later
become the day-part (morning / noon / evening) BigQuery version. See README.
"""

from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

from h3_analysis.bigquery_source import (
    BigQueryConfigError,
    build_index_query,
    build_segments_query,
    index_table_fqn,
    run_query,
)
from h3_analysis.colors import (
    METRIC_HELP,
    METRIC_LABELS,
    colors_for,
    format_value,
    legend_html,
)
from h3_analysis.config import load_local_env
from h3_analysis.data import (
    PAGE1_METRICS,
    DataValidationError,
    ValidationResult,
    aggregate_index_cells,
    collapse_index_duplicates,
    validate_aggregated_cells,
    validate_index_data,
)
from h3_analysis.mapping import (
    BASEMAP_OPTIONS,
    basemap_style,
    render_h3_map,
    segment_checkboxes,
)

st.set_page_config(page_title="H3 Analysis - Index", page_icon="🗺️", layout="wide")

# Local-development convenience; deployed environments inject the real vars.
load_local_env()

LOCAL_SAMPLE_FILE = "data/sample_index.csv"

SOURCE_BIGQUERY = "BigQuery (production)"
SOURCE_LOCAL = "Local CSV (development)"


@st.cache_data(show_spinner="Reading available segments from BigQuery...")
def load_segments(table_fqn: str) -> list[str]:
    frame = run_query(build_segments_query(table_fqn))
    return sorted(frame["segment"].dropna().astype(str).unique())


@st.cache_data(show_spinner="Querying the index from BigQuery...")
def load_bigquery_cells(
    table_fqn: str, metric: str, segments: tuple[str, ...]
) -> pd.DataFrame:
    sql, params = build_index_query(table_fqn, metric, list(segments))
    return run_query(sql, params)


@st.cache_data(show_spinner="Loading and validating local CSV...")
def load_local_index(contents: bytes, metric: str) -> ValidationResult:
    frame = pd.read_csv(io.BytesIO(contents))
    result = validate_index_data(frame, metric)
    return ValidationResult(
        data=collapse_index_duplicates(result.data, metric),
        removed_rows=result.removed_rows,
    )


_FALLBACK_HINT = (
    "To keep working offline, switch the sidebar data source to "
    f"“{SOURCE_LOCAL}”."
)


def _config_error(message: str) -> None:
    """Configuration is missing or malformed - point at the env vars."""
    st.error(
        f"**BigQuery is not configured.**\n\n{message}\n\n"
        "Copy the template and adjust it if the tables have moved:\n"
        "```bash\ncp .env.example .env\n```\n"
        f"{_FALLBACK_HINT}"
    )
    st.stop()


def _access_error(table_fqn: str, error: Exception) -> None:
    """Configuration resolved but the query failed - credentials or IAM."""
    detail = str(error)
    if "invalid_grant" in detail or "credential" in detail.lower():
        remedy = (
            "Your Application Default Credentials are missing or expired. "
            "Re-authenticate:\n"
            "```bash\ngcloud auth application-default login\n```"
        )
    else:
        remedy = (
            "Check IAM: the account needs `roles/bigquery.jobUser` on the "
            "project and `roles/bigquery.dataViewer` on the dataset. Verify "
            "with:\n```bash\npython3 scripts/check_bigquery.py\n```"
        )
    st.error(
        f"**Cannot read `{table_fqn}`.**\n\n{detail}\n\n{remedy}\n\n"
        f"{_FALLBACK_HINT}"
    )
    st.stop()


def bigquery_frame(metric: str) -> tuple[pd.DataFrame, list[str], str]:
    try:
        table_fqn = index_table_fqn(metric)
    except BigQueryConfigError as error:
        _config_error(str(error))

    st.sidebar.caption(f"Data source: BigQuery `{table_fqn}`")

    try:
        segment_values = load_segments(table_fqn)
    except BigQueryConfigError as error:
        _config_error(str(error))
    except Exception as error:  # google.auth / api_core errors
        _access_error(table_fqn, error)

    if not segment_values:
        st.warning("The BigQuery table returned no segments.")
        st.stop()

    selected_segments = segment_checkboxes(segment_values)
    try:
        raw = load_bigquery_cells(table_fqn, metric, tuple(selected_segments))
    except Exception as error:
        _access_error(table_fqn, error)
    validation = validate_aggregated_cells(raw, metric)
    if validation.removed_rows:
        st.warning(
            f"Excluded {validation.removed_rows:,} row(s) with an invalid H3 "
            f"index or {metric} value returned by BigQuery."
        )
    return validation.data, selected_segments, f"BigQuery `{table_fqn}`"


def local_frame(metric: str) -> tuple[pd.DataFrame, list[str], str]:
    uploaded = st.sidebar.file_uploader("Upload index CSV", type="csv")
    if uploaded is not None:
        contents, source_name = uploaded.getvalue(), uploaded.name
    elif os.path.exists(LOCAL_SAMPLE_FILE):
        with open(LOCAL_SAMPLE_FILE, "rb") as handle:
            contents = handle.read()
        source_name = f"{LOCAL_SAMPLE_FILE} (synthetic)"
    else:
        st.info(
            "Upload a CSV with `h3_id`, `segment` and a metric column to use "
            "the local fallback."
        )
        st.stop()

    st.sidebar.caption(f"Data source: {source_name}")
    validation = load_local_index(contents, metric)
    if validation.removed_rows:
        st.warning(
            f"Excluded {validation.removed_rows:,} invalid row(s). "
            "Check H3 indexes, segments, and metric values."
        )

    data = validation.data
    segment_values = sorted(data["segment"].unique().tolist())
    selected_segments = segment_checkboxes(segment_values)
    aggregated = aggregate_index_cells(data, selected_segments, metric)
    return aggregated, selected_segments, source_name


st.title("H3 Grid Analysis - UAE")
st.caption(
    "Page 1 of 2. Index analysis by location and audience segment. "
    "These datasets carry no hour column, so this page has no time filter."
)

metric = st.radio(
    "Index metric",
    PAGE1_METRICS,
    format_func=lambda name: METRIC_LABELS[name],
    horizontal=True,
    key="page1_metric",
)
st.caption(METRIC_HELP[metric])

default_source = (
    SOURCE_LOCAL
    if os.environ.get("H3_DATA_SOURCE", "").strip().lower() == "local"
    else SOURCE_BIGQUERY
)
source_mode = st.sidebar.radio(
    "Data source",
    (SOURCE_BIGQUERY, SOURCE_LOCAL),
    index=0 if default_source == SOURCE_BIGQUERY else 1,
    help="Production uses BigQuery. The local CSV is a development fallback only.",
)

try:
    if source_mode == SOURCE_BIGQUERY:
        cells, selected_segments, source_name = bigquery_frame(metric)
    else:
        cells, selected_segments, source_name = local_frame(metric)
except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
    st.error(f"Unable to use this data source: {error}")
    st.stop()

map_style = st.radio("Basemap", BASEMAP_OPTIONS, horizontal=True, key="basemap")
style_url = basemap_style(map_style)

st.subheader(f"{METRIC_LABELS[metric]} by H3 cell")

if cells.empty:
    st.info("No H3 cells match the selected segments.")
    render_h3_map(cells, "[255, 200, 100]", "", style_url)
    st.stop()

values = cells[metric]
cells = cells.assign(
    fill_color=colors_for(values, metric, map_style == "Dark"),
    metric_label=[format_value(value, metric) for value in values],
)

summary_columns = st.columns(4)
summary_columns[0].metric("Visible H3 cells", f"{len(cells):,}")
summary_columns[1].metric("Segments", f"{len(selected_segments):,}")
summary_columns[2].metric(
    "Median", format_value(float(values.median()), metric)
)
summary_columns[3].metric(
    "Maximum", format_value(float(values.max()), metric)
)

render_h3_map(
    cells,
    "fill_color",
    f"H3: {{h3_id}}\n{METRIC_LABELS[metric]}: {{metric_label}}",
    style_url,
)
st.markdown(
    legend_html(
        metric, float(values.min()), float(values.max()), map_style == "Dark"
    ),
    unsafe_allow_html=True,
)
st.caption(
    "Values are averaged across the selected segments for each H3 cell. "
    f"Data source: {source_name}."
)

st.download_button(
    "Download results",
    data=cells[["h3_id", metric]].to_csv(index=False).encode("utf-8"),
    file_name=f"h3-{metric}.csv",
    mime="text/csv",
)

with st.expander(f"{METRIC_LABELS[metric]} results"):
    st.dataframe(cells[["h3_id", metric]], width="stretch", hide_index=True)
