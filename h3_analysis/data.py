from __future__ import annotations

from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("h3_id", "hour_bucket", "segment", "user_count")
VALID_HOUR_BUCKETS = tuple(range(0, 24, 2))

# The index exports carry no hour column, so they are analysed on their own map.
INDEX_METRICS = ("exclusivity_index", "volume_index")
# Overall/analysis index uses the same schema and aggregation as the index maps.
ANALYSIS_METRICS = ("overall_index",)
INDEX_LIKE_METRICS = INDEX_METRICS + ANALYSIS_METRICS
INDEX_BASE_COLUMNS = ("h3_id", "segment")


class DataValidationError(ValueError):
    """Raised when an input file does not satisfy the application contract."""


@dataclass(frozen=True)
class ValidationResult:
    data: pd.DataFrame
    removed_rows: int


def _valid_h3_mask(values: pd.Series) -> pd.Series:
    """Flag valid H3 indexes, checking each distinct cell only once.

    The index exports repeat a small set of cells across millions of rows, so
    validating per unique value instead of per row keeps loading interactive.
    """
    lookup = {
        cell: bool(cell) and h3.is_valid_cell(cell)
        for cell in values.dropna().unique()
    }
    return values.map(lookup).fillna(False).astype(bool)


def validate_data(frame: pd.DataFrame) -> ValidationResult:
    """Validate and normalize an uploaded H3 data frame."""
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise DataValidationError("Missing required columns: " + ", ".join(missing))

    data = frame.loc[:, REQUIRED_COLUMNS].copy()
    data["h3_id"] = data["h3_id"].astype("string").str.strip()
    data["segment"] = data["segment"].astype("string").str.strip()
    data["hour_bucket"] = pd.to_numeric(data["hour_bucket"], errors="coerce")
    data["user_count"] = pd.to_numeric(data["user_count"], errors="coerce")

    valid_h3 = _valid_h3_mask(data["h3_id"])
    valid_rows = (
        valid_h3
        & data["hour_bucket"].isin(VALID_HOUR_BUCKETS)
        & data["segment"].notna()
        & data["segment"].ne("")
        & data["user_count"].notna()
        & data["user_count"].ge(0)
    )

    removed_rows = int((~valid_rows).sum())
    data = data.loc[valid_rows].copy()
    if data.empty:
        raise DataValidationError("The CSV contains no valid data rows.")

    data["hour_bucket"] = data["hour_bucket"].astype(int)
    return ValidationResult(data=data, removed_rows=removed_rows)


def aggregate_cells(
    frame: pd.DataFrame, selected_segments: list[str], selected_hour: int
) -> pd.DataFrame:
    """Filter the selected slice and sum counts for duplicate H3 cells."""
    return (
        frame[
            frame["segment"].isin(selected_segments)
            & frame["hour_bucket"].eq(selected_hour)
        ]
        .groupby("h3_id", as_index=False, sort=True)["user_count"]
        .sum()
    )


def validate_index_data(frame: pd.DataFrame, metric: str) -> ValidationResult:
    """Validate an index export for one metric.

    The index files hold ``h3_id``, ``segment`` and a single index column. A
    file that carries several index columns is also accepted, so a future export
    combining them needs no code change.
    """
    if metric not in INDEX_LIKE_METRICS:
        raise DataValidationError(
            f"Unknown metric '{metric}'. Expected one of: "
            + ", ".join(INDEX_LIKE_METRICS)
        )

    required = (*INDEX_BASE_COLUMNS, metric)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError("Missing required columns: " + ", ".join(missing))

    data = frame.loc[:, list(required)].copy()
    data["h3_id"] = data["h3_id"].astype("string").str.strip()
    data["segment"] = data["segment"].astype("string").str.strip()
    data[metric] = pd.to_numeric(data[metric], errors="coerce")

    valid_rows = (
        _valid_h3_mask(data["h3_id"])
        & data["segment"].notna()
        & data["segment"].ne("")
        & np.isfinite(data[metric])
        & data[metric].ge(0)
    )

    removed_rows = int((~valid_rows).sum())
    data = data.loc[valid_rows].copy()
    if data.empty:
        raise DataValidationError(
            f"The CSV contains no valid '{metric}' rows."
        )

    return ValidationResult(data=data, removed_rows=removed_rows)


def collapse_index_duplicates(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Average the repeated rows recorded for each cell and segment.

    The export dropped the hour column, so a cell/segment pair can appear up to
    twelve times with no way to tell the buckets apart. Averaging keeps the
    values inside their original range, which summing would not.
    """
    return (
        frame.groupby(list(INDEX_BASE_COLUMNS), as_index=False, sort=True)[metric]
        .mean()
    )


def aggregate_index_cells(
    frame: pd.DataFrame, selected_segments: list[str], metric: str
) -> pd.DataFrame:
    """Average the metric across the selected segments for each H3 cell."""
    selected = frame[frame["segment"].isin(selected_segments)]
    return selected.groupby("h3_id", as_index=False, sort=True)[metric].mean()


def format_hour_bucket(hour: int) -> str:
    """Return a human-readable label for a two-hour bucket."""
    return f"{hour:02d}:00-{(hour + 2) % 24:02d}:00"

