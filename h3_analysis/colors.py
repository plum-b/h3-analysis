"""Sequential color ramps and scaling for the index-like maps.

Each metric gets its own single-hue ramp (light -> dark) because the metrics
measure different things and must never be read against a shared scale.
The ramps follow the project's sequential-color rule: one hue per magnitude
encoding, stepped monotonically in lightness.

``exclusivity_index`` and ``overall_index`` use a linear scale. ``volume_index``
spans roughly four orders of magnitude, so a linear scale would collapse almost
every cell onto the lightest step; it uses a log scale instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Blue ramp, 100 -> 700 (documented sequential hue).
BLUE_RAMP = (
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
)

# Orange ramp, derived at the same lightness profile as BLUE_RAMP so the two
# sequential contexts read as equals. Single hue (~40.7 deg), monotone lightness.
ORANGE_RAMP = (
    "#fbd7ca", "#f5c4b2", "#f2b098", "#eb9c7f", "#e68764", "#df7249", "#d95923",
    "#c94908", "#b44005", "#9f3600", "#8a2e00", "#752600", "#621e00",
)

# Teal-green ramp for the overall index; same lightness profile as the others
# so the three metrics read as equal peers.
TEAL_GREEN_RAMP = (
    "#c5ebe3", "#addfd5", "#94d3c6", "#7cc7b8", "#63bbaa", "#4bae9b", "#32a28d",
    "#2a917e", "#257f6f", "#1f6d60", "#1a5c50", "#144b41", "#0f3a32",
)

METRIC_RAMPS = {
    "overall_index": TEAL_GREEN_RAMP,
    "exclusivity_index": BLUE_RAMP,
    "volume_index": ORANGE_RAMP,
}

METRIC_SCALES = {
    "overall_index": "linear",
    "exclusivity_index": "linear",
    "volume_index": "log",
}

METRIC_LABELS = {
    "overall_index": "Overall index",
    "exclusivity_index": "Exclusivity index",
    "volume_index": "Volume index",
}

METRIC_HELP = {
    "overall_index": (
        "A combined overall score for each cell and segment. Higher means a "
        "stronger overall signal for that audience in the cell."
    ),
    "exclusivity_index": (
        "How concentrated a segment is in a cell relative to its overall "
        "presence. Higher means the cell is more distinctive for that segment."
    ),
    "volume_index": (
        "The share of a segment's total activity that falls in a cell. Higher "
        "means more of the segment's volume is here."
    ),
}

# Percentile clip keeps a handful of extreme cells from flattening the ramp.
LOW_PERCENTILE = 2.0
HIGH_PERCENTILE = 98.0


def hex_to_rgb(value: str) -> list[int]:
    value = value.lstrip("#")
    return [int(value[index:index + 2], 16) for index in (0, 2, 4)]


def ramp_for(metric: str, dark_basemap: bool) -> tuple[str, ...]:
    """Return the ramp oriented so high values stay visible on the basemap.

    On a dark basemap the ramp is reversed: the darkest steps would otherwise
    sink into the background, so high magnitude takes the light end instead.
    """
    ramp = METRIC_RAMPS[metric]
    return tuple(reversed(ramp)) if dark_basemap else ramp


def normalize(values: pd.Series, metric: str) -> pd.Series:
    """Scale a metric onto 0..1 using the scale that suits its distribution."""
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    if numeric.empty:
        return pd.Series([], dtype=float, index=numeric.index)

    scaled = numeric
    if METRIC_SCALES[metric] == "log":
        # Values are strictly positive in practice; guard anyway.
        scaled = np.log10(numeric.where(numeric > 0))

    finite = scaled.replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return pd.Series(0.5, index=numeric.index, dtype=float)

    low = float(np.percentile(finite, LOW_PERCENTILE))
    high = float(np.percentile(finite, HIGH_PERCENTILE))
    if high <= low:
        # A constant slice has no spread to show; place it mid-ramp.
        return pd.Series(0.5, index=numeric.index, dtype=float)

    position = (scaled - low) / (high - low)
    return position.clip(0.0, 1.0).fillna(0.0)


def colors_for(values: pd.Series, metric: str, dark_basemap: bool) -> list[list[int]]:
    """Map metric values onto RGB triples by interpolating the metric's ramp."""
    ramp = [hex_to_rgb(step) for step in ramp_for(metric, dark_basemap)]
    position = normalize(values, metric)
    last = len(ramp) - 1

    colors = []
    for fraction in position:
        point = float(fraction) * last
        lower = int(np.floor(point))
        upper = min(lower + 1, last)
        weight = point - lower
        colors.append(
            [
                int(round(ramp[lower][channel] * (1 - weight) + ramp[upper][channel] * weight))
                for channel in range(3)
            ]
        )
    return colors


def format_value(value: float, metric: str) -> str:
    """Format a metric for tooltips and legends at a readable precision."""
    if pd.isna(value):
        return "n/a"
    if METRIC_SCALES[metric] == "log":
        return f"{value:.2e}"
    return f"{value:.3f}"


def legend_html(metric: str, low: float, high: float, dark_basemap: bool) -> str:
    """Build a horizontal gradient legend matching the rendered ramp."""
    ramp = ramp_for(metric, dark_basemap)
    gradient = ", ".join(ramp)
    return f"""
<div style="margin:0.25rem 0 0.75rem 0;">
  <div style="font-size:0.8rem;opacity:0.75;margin-bottom:0.3rem;">
    {METRIC_LABELS[metric]} ({METRIC_SCALES[metric]} scale)
  </div>
  <div style="height:12px;border-radius:6px;background:linear-gradient(to right, {gradient});
              border:1px solid rgba(255,255,255,0.15);"></div>
  <div style="display:flex;justify-content:space-between;font-size:0.75rem;
              opacity:0.75;margin-top:0.25rem;font-variant-numeric:tabular-nums;">
    <span>{format_value(low, metric)}</span>
    <span>{format_value(high, metric)}</span>
  </div>
</div>
""".strip()
