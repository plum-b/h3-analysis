import os

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="H3 Analysis", layout="wide")
st.title("H3 Grid Analysis — UAE")

DATA_DIR = "data"
DEFAULT_FILE = f"{DATA_DIR}/every_2_hours.csv"

uploaded = st.sidebar.file_uploader("Upload CSV", type="csv")

if uploaded is not None:
    df = pd.read_csv(uploaded)
elif os.path.exists(DEFAULT_FILE):
    df = pd.read_csv(DEFAULT_FILE)
    st.sidebar.caption(f"Using {DEFAULT_FILE}")
else:
    st.info("Upload a CSV to get started.")
    st.stop()

columns = df.columns.tolist()


def pick(col_names, keywords, fallback):
    for c in col_names:
        if any(k in c.lower() for k in keywords):
            return c
    return fallback if fallback in col_names else col_names[0]


h3_col = pick(columns, ["h3", "hex"], "h3_id")
time_col = pick(columns, ["hour", "time", "bucket"], "hour_bucket")
value_col = pick(columns, ["count", "value", "metric"], "user_count")
segment_col = pick(columns, ["segment", "audience", "cohort"], "segment")

st.sidebar.subheader("Segments")
segment_values = sorted(df[segment_col].dropna().unique().tolist())
selected_segments = [
    seg for seg in segment_values
    if st.sidebar.checkbox(seg, value=True, key=f"seg_{seg}")
]

if not selected_segments:
    st.warning("Select at least one segment.")
    st.stop()

df = df[df[segment_col].isin(selected_segments)]

hour_values = sorted(df[time_col].dropna().unique().tolist())
selected_hour = st.select_slider("Hour bucket", options=hour_values, value=hour_values[0])

filtered = (
    df[df[time_col] == selected_hour]
    .groupby(h3_col, as_index=False)[value_col]
    .sum()
)

st.subheader(f"{value_col} by H3 cell — hour {selected_hour}:00")

max_val = filtered[value_col].max() if len(filtered) else 1

layer = pdk.Layer(
    "H3HexagonLayer",
    filtered,
    get_hexagon=h3_col,
    get_fill_color=f"[255, 255 * (1 - {value_col} / {max_val}), 100]",
    get_elevation=value_col,
    elevation_scale=2,
    extruded=False,
    pickable=True,
    opacity=0.6,
)

# UAE center — fallback if a filtered slice happens to be empty.
UAE_LAT, UAE_LON = 24.0, 54.0

centers = filtered[h3_col].dropna().apply(h3.cell_to_latlng)
if len(centers):
    lat = sum(c[0] for c in centers) / len(centers)
    lon = sum(c[1] for c in centers) / len(centers)
else:
    lat, lon = UAE_LAT, UAE_LON

view_state = pdk.ViewState(latitude=lat, longitude=lon, zoom=7.5)

st.pydeck_chart(pdk.Deck(
    layers=[layer],
    initial_view_state=view_state,
    tooltip={"text": f"{{{h3_col}}}\n{value_col}: {{{value_col}}}"},
))

with st.expander("Raw data"):
    st.dataframe(filtered)
