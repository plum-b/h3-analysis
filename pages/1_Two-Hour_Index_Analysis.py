"""Page 1 - Two-hour index analysis map (BigQuery).

Page 1 shows one index metric at a time - Overall or Volume - each stored in
its own BigQuery table with the schema ``h3_id`` / ``segment`` / ``<metric>`` /
``hour_bucket``, where ``hour_bucket`` is the INT64 **two-hour period** of the
day (0, 2, ... 22 on the live tables). The page shows exactly one period at a
time, chosen with the two-hour slicer, for exactly one audience segment chosen
with the sidebar radio.

Each ``(h3_id, segment, hour_bucket)`` triple appears exactly once, so a
``(h3_id, segment)`` pair carries ~8.4 rows only because it spans the twelve
periods. Aggregation is still done in two steps - within each
``(h3_id, segment, hour_bucket)`` group, then across the selected segment(s) -
so the query is unchanged by the selector holding one segment rather than
several.

Page 1's tables carry no ``Week_part`` column, so there is no Weekday/Weekend
selector here; that split exists only on Page 2's day-section tables.

Production reads BigQuery. A local CSV (uploaded, or the committed synthetic
``data/sample_index_two_hours.csv``, which carries both metric columns and
the twelve two-hour periods) is an explicit development fallback; production
does not depend on a production CSV.

Page 2 (``pages/2_Day-Part_Index_Analysis.py``) is the day-part view, filtered
on the STRING ``hour_bucket`` labels held by its own tables. ``app.py`` is the
entry point that registers both pages with ``st.navigation``. See README.
"""

from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

from h3_analysis.bigquery_source import (
    BigQueryConfigError,
    BigQueryCredentialsError,
    build_index_query,
    build_segments_query,
    build_two_hour_periods_query,
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
from h3_analysis.config import load_local_env, prime_streamlit_secrets
from h3_analysis.data import (
    PAGE1_METRICS,
    DataValidationError,
    ValidationResult,
    aggregate_two_hour_index_cells,
    collapse_two_hour_duplicates,
    validate_aggregated_cells,
    validate_two_hour_index_data,
)
from h3_analysis.mapping import (
    BASEMAP_OPTIONS,
    basemap_style,
    is_dark_basemap,
    render_h3_map,
    segment_radio,
)

# Streamlit Cloud passes configuration through st.secrets, which only reaches
# os.environ once the secrets are read; .env is the local-development
# convenience, and deployed environments inject the real vars.
# ``app.py`` owns st.set_page_config; a page script must not call it again.
prime_streamlit_secrets()
load_local_env()

LOCAL_SAMPLE_FILE = "data/sample_index_two_hours.csv"

SOURCE_BIGQUERY = "BigQuery (production)"
SOURCE_LOCAL = "Local CSV (development)"


def period_label(period: int) -> str:
    """Render a two-hour period as the clock window it covers."""
    return f"{int(period):02d}:00 - {(int(period) + 2) % 24:02d}:00"


def two_hour_slicer(periods: list) -> int:
    """Pick exactly one two-hour period.

    A slider reads as time-of-day and makes stepping through the day one period
    at a time obvious, which a dropdown does not.
    """
    if len(periods) == 1:
        st.caption(f"Two-hour period: {period_label(periods[0])}")
        return periods[0]
    return st.select_slider(
        "Two-hour period",
        options=periods,
        value=periods[0],
        format_func=period_label,
        key="page1_two_hour_period",
        help="The map shows one two-hour period at a time.",
    )


@st.cache_data(show_spinner="Reading available segments from BigQuery...")
def load_segments(table_fqn: str) -> list:
    frame = run_query(build_segments_query(table_fqn))
    return sorted(frame["segment"].dropna().astype(str).unique())


@st.cache_data(show_spinner="Reading available two-hour periods from BigQuery...")
def load_two_hour_periods(table_fqn: str) -> list:
    frame = run_query(build_two_hour_periods_query(table_fqn))
    periods = pd.to_numeric(frame["hour_bucket"], errors="coerce").dropna()
    return sorted({int(period) for period in periods})


@st.cache_data(show_spinner="Querying the index from BigQuery...")
def load_bigquery_cells(
    table_fqn: str, metric: str, segment: str, two_hour_period: int
) -> pd.DataFrame:
    """One segment, one two-hour period - the segment still travels as the
    ``@segments`` array parameter, so nothing user-controlled reaches the SQL
    text."""
    sql, params = build_index_query(
        table_fqn, metric, [segment], two_hour_period
    )
    return run_query(sql, params)


@st.cache_data(show_spinner="Loading and validating local CSV...")
def load_local_index(contents: bytes, metric: str) -> ValidationResult:
    frame = pd.read_csv(io.BytesIO(contents))
    result = validate_two_hour_index_data(frame, metric)
    return ValidationResult(
        data=collapse_two_hour_duplicates(result.data, metric),
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


def _credentials_error(error: Exception) -> None:
    """No usable Google Cloud credentials - ADC locally, a secret on Cloud.

    Streamlit Community Cloud runs outside Google Cloud, so Application
    Default Credentials cannot reach the metadata server there; the message
    carried by the error names the secret keys to add.
    """
    st.error(
        f"**BigQuery authentication is not configured.**\n\n{error}\n\n"
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
    elif "jobs.create" in detail:
        remedy = (
            "The job was billed to the wrong project. Jobs run in "
            "`BIGQUERY_BILLING_PROJECT`, or `BIGQUERY_PROJECT_ID` when that is "
            "unset - not in whichever project `gcloud` happens to default to. "
            "Confirm the account holds `roles/bigquery.jobUser` there:\n"
            "```bash\npython3 scripts/check_bigquery.py\n```"
        )
    else:
        remedy = (
            "Check IAM: the account needs `roles/bigquery.jobUser` on the "
            "billing project and `roles/bigquery.dataViewer` on the dataset. "
            "Verify with:\n```bash\npython3 scripts/check_bigquery.py\n```"
        )
    st.error(
        f"**Cannot read `{table_fqn}`.**\n\n{detail}\n\n{remedy}\n\n"
        f"{_FALLBACK_HINT}"
    )
    st.stop()


def bigquery_frame(metric: str) -> tuple:
    try:
        table_fqn = index_table_fqn(metric)
    except BigQueryConfigError as error:
        _config_error(str(error))

    st.sidebar.caption(f"Data source: BigQuery `{table_fqn}`")

    try:
        segment_values = load_segments(table_fqn)
        period_values = load_two_hour_periods(table_fqn)
    except BigQueryCredentialsError as error:
        _credentials_error(error)
    except BigQueryConfigError as error:
        _config_error(str(error))
    except Exception as error:  # google.auth / api_core errors
        _access_error(table_fqn, error)

    if not segment_values:
        st.warning("The BigQuery table returned no segments.")
        st.stop()
    if not period_values:
        st.warning("The BigQuery table returned no two-hour periods.")
        st.stop()

    selected_segment = segment_radio(segment_values)
    selected_period = two_hour_slicer(period_values)

    try:
        raw = load_bigquery_cells(
            table_fqn, metric, selected_segment, selected_period
        )
    except BigQueryCredentialsError as error:
        _credentials_error(error)
    except Exception as error:
        _access_error(table_fqn, error)

    validation = validate_aggregated_cells(raw, metric)
    if validation.removed_rows:
        st.warning(
            f"Excluded {validation.removed_rows:,} row(s) with an invalid H3 "
            f"index or {metric} value returned by BigQuery."
        )
    return (
        validation.data,
        selected_segment,
        selected_period,
        f"BigQuery `{table_fqn}`",
    )


def local_frame(metric: str) -> tuple:
    uploaded = st.sidebar.file_uploader("Upload index CSV", type="csv")
    if uploaded is not None:
        contents, source_name = uploaded.getvalue(), uploaded.name
    elif os.path.exists(LOCAL_SAMPLE_FILE):
        with open(LOCAL_SAMPLE_FILE, "rb") as handle:
            contents = handle.read()
        source_name = f"{LOCAL_SAMPLE_FILE} (synthetic)"
    else:
        st.info(
            "Upload a CSV with `h3_id`, `segment`, `hour_bucket` and a metric "
            "column to use the local fallback."
        )
        st.stop()

    st.sidebar.caption(f"Data source: {source_name}")
    validation = load_local_index(contents, metric)
    if validation.removed_rows:
        st.warning(
            f"Excluded {validation.removed_rows:,} invalid row(s). "
            "Check H3 indexes, segments, two-hour periods, and metric values."
        )

    data = validation.data
    segment_values = sorted(data["segment"].unique().tolist())
    period_values = sorted({int(period) for period in data["hour_bucket"]})
    selected_segment = segment_radio(segment_values)
    selected_period = two_hour_slicer(period_values)
    aggregated = aggregate_two_hour_index_cells(
        data, [selected_segment], selected_period, metric
    )
    return aggregated, selected_segment, selected_period, source_name


st.title("Two-Hour Index Analysis")
st.caption(
    "Page 1 of 2. Index analysis by location, audience segment and two-hour "
    "period of the day."
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
        cells, selected_segment, selected_period, source_name = bigquery_frame(metric)
    else:
        cells, selected_segment, selected_period, source_name = local_frame(metric)
except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
    st.error(f"Unable to use this data source: {error}")
    st.stop()

map_style = st.radio("Basemap", BASEMAP_OPTIONS, horizontal=True, key="basemap")
style_url = basemap_style(map_style)
dark_basemap = is_dark_basemap(map_style)

st.subheader(
    f"{METRIC_LABELS[metric]} by H3 cell - {period_label(selected_period)}"
)

if cells.empty:
    st.info("No H3 cells match the selected segment and two-hour period.")
    render_h3_map(cells, "[255, 200, 100]", "", style_url)
    st.stop()

values = cells[metric]
cells = cells.assign(
    fill_color=colors_for(values, metric, dark_basemap),
    metric_label=[format_value(value, metric) for value in values],
)

summary_columns = st.columns(4)
summary_columns[0].metric("Visible H3 cells", f"{len(cells):,}")
summary_columns[1].metric("Segment", selected_segment)
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
    legend_html(metric, float(values.min()), float(values.max()), dark_basemap),
    unsafe_allow_html=True,
)
st.caption(
    f"Values are the {selected_segment} average for each H3 cell, for the "
    f"{period_label(selected_period)} period. Data source: {source_name}."
)

st.download_button(
    "Download results",
    data=cells[["h3_id", metric]].to_csv(index=False).encode("utf-8"),
    file_name=f"h3-{metric}-{int(selected_period):02d}00.csv",
    mime="text/csv",
)

with st.expander(f"{METRIC_LABELS[metric]} results"):
    st.dataframe(cells[["h3_id", metric]], width="stretch", hide_index=True)
