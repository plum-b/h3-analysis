"""Shared PyDeck rendering helpers used by both map pages.

Keeping the H3 layer, view centering, and basemap choices in one place means the
user-count page and the index page stay visually consistent.
"""

from __future__ import annotations

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

# Fallback center used only when no cells are on screen.
UAE_LAT, UAE_LON = 24.0, 54.0

BASEMAP_OPTIONS = ("Dark", "Street Map")


def basemap_style(choice: str) -> str:
    """Translate a basemap label into a deck.gl style URL."""
    return pdk.map_styles.DARK if choice == "Dark" else pdk.map_styles.CARTO_ROAD


def segment_checkboxes(segment_values: list[str]) -> list[str]:
    """Render the shared sidebar segment checkboxes; stop if none are selected.

    Shared by both pages so "select all" and per-segment behavior stay
    identical between them.
    """
    st.sidebar.subheader("Segments")
    select_all = st.sidebar.checkbox("Select all", value=True)
    selected = [
        segment
        for segment in segment_values
        if st.sidebar.checkbox(
            segment, value=select_all, key=f"segment_{select_all}_{segment}"
        )
    ]
    if not selected:
        st.warning("Select at least one segment to display the map.")
        st.stop()
    return selected


def map_center(frame: pd.DataFrame) -> tuple[float, float]:
    """Center the view on the displayed cells, falling back to the UAE."""
    if frame.empty:
        return UAE_LAT, UAE_LON
    centers = frame["h3_id"].map(h3.cell_to_latlng)
    return (
        sum(center[0] for center in centers) / len(centers),
        sum(center[1] for center in centers) / len(centers),
    )


def render_h3_map(
    frame: pd.DataFrame, fill_color, tooltip_text: str, style: str
) -> None:
    """Render one H3 layer. Shared by both pages so they stay consistent."""
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
