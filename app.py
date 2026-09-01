"""Entry point - registers the two map pages and their navigation labels.

Streamlit labels the entry script in the sidebar after its *filename*, so while
Page 1 lived in ``app.py`` the navigation read "app" no matter what
``st.set_page_config(page_title=...)`` said. ``st.navigation`` is the supported
way to set those labels, so ``app.py`` stays the entry point (``streamlit run
app.py``, the Dockerfile and the launch config are unchanged) and each page's
body lives in its own script under ``pages/``.

Because ``st.navigation`` is used, Streamlit does not auto-discover ``pages/``;
this list is the single place where the two pages and their labels are declared.
``st.set_page_config`` must be called here, in the entry script, and nowhere
else - the per-page browser title comes from each ``st.Page`` title.

The app has exactly two map pages, each on its own page because they read
different tables and aggregate differently:

* **Two-Hour Index Analysis** - the ``*_filtered`` tables, sliced to one INT64
  two-hour period (0, 2, ... 22).
* **Day-Part Index Analysis** - the ``*_day_sections`` tables, filtered to one
  STRING day-part label (Morning / Noon / After noon / Night / Other).
"""

from __future__ import annotations

import streamlit as st

from h3_analysis.config import load_local_env

st.set_page_config(page_title="H3 Grid Analysis - UAE", page_icon="🗺️", layout="wide")

# Local-development convenience; deployed environments inject the real vars.
load_local_env()

PAGES = [
    st.Page(
        "pages/1_Two-Hour_Index_Analysis.py",
        title="Two-Hour Index Analysis",
        icon="🗺️",
        default=True,
    ),
    st.Page(
        "pages/2_Day-Part_Index_Analysis.py",
        title="Day-Part Index Analysis",
        icon="🧭",
    ),
]

st.navigation(PAGES).run()
