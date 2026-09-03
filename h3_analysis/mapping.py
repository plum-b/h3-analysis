"""Shared PyDeck rendering helpers used by both map pages.

Keeping the H3 layer, view centering, and basemap choices in one place means the
two index pages stay visually consistent.

Basemaps
--------
The default basemap is OpenFreeMap Liberty, a detailed OpenMapTiles style:
111 layers including building footprints, 28 road classes, POI and place
labels, and a Natural Earth shaded-relief source that gives real terrain shading
at low zoom. Streets and buildings therefore read through the translucent H3
layer as you zoom in, and the relief is genuine imagery rather than a road map
relabelled as terrain.

It is token-free, so nothing here needs a credential. ``H3_BASEMAP_STYLE_URL``
overrides it with any MapLibre style.json URL - that is where a deployment
points at its own provider (MapTiler Outdoor, a self-hosted style, anything with
its own key). Keys belong in that environment variable or in
``.streamlit/secrets.toml``, never in this file.

Anything malformed, unavailable, or unrecognised falls back to the hosted CARTO
Voyager style, which is always reachable: a basemap must never be the reason the
map fails to draw. Note Streamlit renders deck.gl from JSON and requires
``mapStyle`` to be a style *URL* - an inline style object raises
"e.mapStyle?.indexOf is not a function" in the browser - so every value here is
a string.
"""

from __future__ import annotations

import os

import h3
import pandas as pd
import pydeck as pdk
import streamlit as st

# Fallback center used only when no cells are on screen.
UAE_LAT, UAE_LON = 24.0, 54.0

# Streamlit renders a deck.gl chart 500px tall by default, which leaves the UAE
# grid cramped and forces constant panning. Twice that shows a whole emirate at
# a readable zoom. Height only - the chart still stretches to the column width,
# so the surrounding layout and column proportions are unchanged.
STREAMLIT_DEFAULT_MAP_HEIGHT = 500
MAP_HEIGHT = STREAMLIT_DEFAULT_MAP_HEIGHT * 2

BASEMAP_DETAILED = "Streets + terrain"
BASEMAP_ROAD = "Street Map"
BASEMAP_DARK = "Dark"

# Ordered most- to least-detailed; the first entry is what a page shows first.
BASEMAP_OPTIONS = (BASEMAP_DETAILED, BASEMAP_ROAD, BASEMAP_DARK)

ENV_DETAILED_STYLE = "H3_BASEMAP_STYLE_URL"

# Token-free default: streets, buildings, labels and shaded relief.
DEFAULT_DETAILED_STYLE = "https://tiles.openfreemap.org/styles/liberty"

# Always-reachable fallback for an unknown label or a malformed override.
FALLBACK_STYLE = pdk.map_styles.CARTO_ROAD


def _valid_style_url(url: str) -> bool:
    """Accept only an https style URL - no credentials smuggled in as a path."""
    return url.strip().startswith("https://")


def detailed_style_url() -> str:
    """The detailed basemap style URL, honouring the environment override."""
    override = (os.environ.get(ENV_DETAILED_STYLE) or "").strip()
    if override and _valid_style_url(override):
        return override
    return DEFAULT_DETAILED_STYLE


def basemap_style(choice: str) -> str:
    """Translate a basemap label into a deck.gl style URL."""
    if choice == BASEMAP_DETAILED:
        return detailed_style_url()
    if choice == BASEMAP_DARK:
        return pdk.map_styles.DARK
    if choice == BASEMAP_ROAD:
        return pdk.map_styles.CARTO_ROAD
    return FALLBACK_STYLE


def is_dark_basemap(choice: str) -> bool:
    """Whether a basemap needs the dark-background color ramp."""
    return choice == BASEMAP_DARK


def segment_radio(segment_values: list) -> str:
    """Render the shared sidebar segment selector and return the one segment.

    Exactly one segment is shown at a time: radio buttons make that visible in
    the control itself, so the map always answers "where is *this* audience"
    rather than showing an average over a set the reader has to reconstruct
    from checkboxes. There is deliberately no "select all".

    Shared by both pages so the selector behaves identically on each.
    """
    st.sidebar.subheader("Segment")
    if not segment_values:
        st.warning("No segments are available to display.")
        st.stop()
    return st.sidebar.radio(
        "Audience segment",
        segment_values,
        index=0,
        key="segment",
        help="The map shows one audience segment at a time.",
    )


def map_center(frame: pd.DataFrame) -> tuple:
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
        # Resolution-9 cells tile a city solidly, so the layer has to stay
        # quite translucent for the streets and buildings underneath to read.
        opacity=0.45,
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
        height=MAP_HEIGHT,
    )
