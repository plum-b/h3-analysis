from __future__ import annotations

import io
import os

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

from h3_analysis.colors import (
    METRIC_HELP,
    METRIC_LABELS,
    colors_for,
    format_value,
    legend_html,
)
from h3_analysis.data import (
    ANALYSIS_METRICS,
    INDEX_METRICS,
    DataValidationError,
    ValidationResult,
    aggregate_cells,
    aggregate_index_cells,
    collapse_index_duplicates,
    format_hour_bucket,
    validate_data,
    validate_index_data,
)

st.set_page_config(page_title="H3 Analysis", page_icon="🗺️", layout="wide")

DATA_DIR = "data"
DEFAULT_FILE = f"{DATA_DIR}/every_2_hours.csv"
INDEX_FILES = {metric: f"{DATA_DIR}/{metric}.csv" for metric in INDEX_METRICS}
ANALYSIS_DATASETS = (
    "analysis_indexed_filtered",
    "analysis_indexed",
)
ANALYSIS_DATASET_LABELS = {
    "analysis_indexed_filtered": "Filtered",
    "analysis_indexed": "Full",
}
ANALYSIS_FILES = {
    name: f"{DATA_DIR}/{name}.csv" for name in ANALYSIS_DATASETS
}
ANALYSIS_METRIC = ANALYSIS_METRICS[0]
UAE_LAT, UAE_LON = 24.0, 54.0


@st.cache_data(show_spinner="Loading and validating H3 data...")
def load_local_data(path: str, modified_ns: int):
    """Load and validate a local CSV, refreshing only when the file changes."""
    del modified_ns
    return validate_data(pd.read_csv(path))


@st.cache_data(show_spinner="Loading and validating uploaded CSV...")
def load_uploaded_data(contents: bytes):
    return validate_data(pd.read_csv(io.BytesIO(contents)))


def load_source():
    uploaded = st.sidebar.file_uploader("Upload CSV", type="csv")
    if uploaded is not None:
        return load_uploaded_data(uploaded.getvalue()), uploaded.name
    if os.path.exists(DEFAULT_FILE):
        modified_ns = os.stat(DEFAULT_FILE).st_mtime_ns
        return load_local_data(DEFAULT_FILE, modified_ns), DEFAULT_FILE
    st.info("Upload a CSV containing H3 cells to get started.")
    st.stop()


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


def render_h3_map(frame: pd.DataFrame, fill_color, tooltip_text: str, style: str) -> None:
    """Render one H3 layer. Shared by all maps so they stay consistent."""
    layer = pdk.Layer(
        "H3HexagonLayer",
        frame,
        get_hexagon="h3_id",
        get_fill_color=fill_color,
        pickable=True,
        opacity=0.65,
        # Flat cells: colour alone carries the value, no extrusion.
        extruded=False,
        stroked=False,
    )
    latitude, longitude = map_center(frame)
    view_state = pdk.ViewState(
        latitude=latitude, longitude=longitude, zoom=7.5, pitch=0, bearing=0
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            map_style=style,
            tooltip={"text": tooltip_text},
        ),
        width="stretch",
    )


def map_center(frame: pd.DataFrame) -> tuple[float, float]:
    if frame.empty:
        return UAE_LAT, UAE_LON
    centers = frame["h3_id"].map(h3.cell_to_latlng)
    return (
        sum(center[0] for center in centers) / len(centers),
        sum(center[1] for center in centers) / len(centers),
    )


st.title("H3 Grid Analysis - UAE")
st.caption("Explore audience activity by location, segment, and two-hour period.")

try:
    validation, source_name = load_source()
except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
    st.error(f"Unable to use this CSV: {error}")
    st.stop()

data = validation.data
st.sidebar.caption(f"Data source: {source_name}")
if validation.removed_rows:
    st.warning(
        f"Excluded {validation.removed_rows:,} invalid row(s). "
        "Check H3 indexes, hour buckets, segments, and user counts."
    )

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

hour_values = sorted(data["hour_bucket"].unique().tolist())
selected_hour = st.select_slider(
    "Two-hour period",
    options=hour_values,
    value=hour_values[0],
    format_func=format_hour_bucket,
)

filtered = aggregate_cells(data, selected_segments, selected_hour)
period_label = format_hour_bucket(selected_hour)

total_users = filtered["user_count"].sum()
metric_columns = st.columns(4)
metric_columns[0].metric("Users", f"{total_users:,.0f}")
metric_columns[1].metric("Visible H3 cells", f"{len(filtered):,}")
metric_columns[2].metric("Segments", f"{len(selected_segments):,}")
metric_columns[3].metric("Period", period_label)

st.subheader(f"User count by H3 cell - {period_label}")

map_style = st.radio(
    "Basemap", ["Dark", "Street Map"], horizontal=True, key="basemap"
)
style_url = (
    pdk.map_styles.DARK if map_style == "Dark" else pdk.map_styles.CARTO_ROAD
)

if filtered.empty:
    st.info("No H3 cells match the current filters.")
else:
    max_value = max(float(filtered["user_count"].max()), 1.0)
    render_h3_map(
        filtered,
        f"[255, 255 * (1 - user_count / {max_value}), 100]",
        "H3: {h3_id}\nUsers: {user_count}",
        style_url,
    )
    st.caption("Color scale: yellow indicates lower counts; red indicates higher counts.")

download_data = filtered.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download filtered results",
    data=download_data,
    file_name=f"h3-results-{selected_hour:02d}.csv",
    mime="text/csv",
)

with st.expander("Filtered H3 results"):
    st.dataframe(filtered, width="stretch", hide_index=True)

st.divider()

st.header("Index analysis")
st.caption(
    "A separate dataset describing how each segment is distributed across the "
    "grid. These exports carry no hour column, so this map is not affected by "
    "the two-hour period above. It follows the segment and basemap choices."
)

index_metric = st.radio(
    "Index metric",
    INDEX_METRICS,
    format_func=lambda metric: METRIC_LABELS[metric],
    horizontal=True,
    key="index_metric",
)
st.caption(METRIC_HELP[index_metric])

index_path = INDEX_FILES[index_metric]

if not os.path.exists(index_path):
    st.info(f"Add `{index_path}` to enable this map.")
else:
    try:
        index_validation = load_index_data(
            index_path, index_metric, os.stat(index_path).st_mtime_ns
        )
    except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
        st.error(f"Unable to use {index_path}: {error}")
    else:
        if index_validation.removed_rows:
            st.warning(
                f"Excluded {index_validation.removed_rows:,} invalid row(s) from "
                f"{index_metric}."
            )

        index_cells = aggregate_index_cells(
            index_validation.data, selected_segments, index_metric
        )

        st.subheader(f"{METRIC_LABELS[index_metric]} by H3 cell")

        if index_cells.empty:
            st.info("No H3 cells match the selected segments.")
        else:
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
                "Values are averaged across the repeated rows for each cell and "
                "segment, then across the selected segments."
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

st.divider()

st.header("Overall analysis index")
st.caption(
    "A third map for the overall analysis index. Like the index map above, "
    "these exports have no hour column, so this view ignores the two-hour "
    "period slider and follows the segment and basemap choices."
)

analysis_dataset = st.radio(
    "Analysis dataset",
    ANALYSIS_DATASETS,
    format_func=lambda name: ANALYSIS_DATASET_LABELS[name],
    horizontal=True,
    key="analysis_dataset",
)
st.caption(
    "Filtered uses `analysis_indexed_filtered.csv`; Full uses "
    "`analysis_indexed.csv`. Both share the same overall index metric and "
    "aggregation rules."
)
st.caption(METRIC_HELP[ANALYSIS_METRIC])

analysis_path = ANALYSIS_FILES[analysis_dataset]

if not os.path.exists(analysis_path):
    st.info(f"Add `{analysis_path}` to enable this map.")
else:
    try:
        analysis_validation = load_index_data(
            analysis_path, ANALYSIS_METRIC, os.stat(analysis_path).st_mtime_ns
        )
    except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError) as error:
        st.error(f"Unable to use {analysis_path}: {error}")
    else:
        if analysis_validation.removed_rows:
            st.warning(
                f"Excluded {analysis_validation.removed_rows:,} invalid row(s) from "
                f"{ANALYSIS_DATASET_LABELS[analysis_dataset]}."
            )

        analysis_cells = aggregate_index_cells(
            analysis_validation.data, selected_segments, ANALYSIS_METRIC
        )

        st.subheader(
            f"{METRIC_LABELS[ANALYSIS_METRIC]} by H3 cell "
            f"({ANALYSIS_DATASET_LABELS[analysis_dataset]})"
        )

        if analysis_cells.empty:
            st.info("No H3 cells match the selected segments.")
        else:
            values = analysis_cells[ANALYSIS_METRIC]
            analysis_cells = analysis_cells.assign(
                fill_color=colors_for(values, ANALYSIS_METRIC, map_style == "Dark"),
                metric_label=[
                    format_value(value, ANALYSIS_METRIC) for value in values
                ],
            )

            summary_columns = st.columns(3)
            summary_columns[0].metric("Visible H3 cells", f"{len(analysis_cells):,}")
            summary_columns[1].metric(
                "Median", format_value(float(values.median()), ANALYSIS_METRIC)
            )
            summary_columns[2].metric(
                "Maximum", format_value(float(values.max()), ANALYSIS_METRIC)
            )

            render_h3_map(
                analysis_cells,
                "fill_color",
                (
                    f"H3: {{h3_id}}\n"
                    f"{METRIC_LABELS[ANALYSIS_METRIC]}: {{metric_label}}"
                ),
                style_url,
            )
            st.markdown(
                legend_html(
                    ANALYSIS_METRIC,
                    float(values.min()),
                    float(values.max()),
                    map_style == "Dark",
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                "Values are averaged across the repeated rows for each cell and "
                "segment, then across the selected segments. The hour slider "
                "does not apply to this map."
            )

            st.download_button(
                "Download analysis results",
                data=analysis_cells[["h3_id", ANALYSIS_METRIC]]
                .to_csv(index=False)
                .encode("utf-8"),
                file_name=f"h3-{analysis_dataset}.csv",
                mime="text/csv",
                key="download_analysis",
            )

            with st.expander(
                f"{METRIC_LABELS[ANALYSIS_METRIC]} "
                f"({ANALYSIS_DATASET_LABELS[analysis_dataset]}) results"
            ):
                st.dataframe(
                    analysis_cells[["h3_id", ANALYSIS_METRIC]],
                    width="stretch",
                    hide_index=True,
                )
