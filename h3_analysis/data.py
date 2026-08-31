from __future__ import annotations

from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd

# Page 1 (BigQuery) shows one of these index metrics, each from its own table.
# Schema per table: h3_id, segment, <the metric column>. No hour column.
PAGE1_METRICS = ("overall_index", "volume_index", "exclusivity_index")

# Page 2 (BigQuery, day-part) shows the same three metrics, each from its own
# "*_day_sections" table with the added ``hour_bucket`` (day-part) column.
# Confirmed against the live tables: h3_id/<metric>/segment/hour_bucket, all
# resolution-9 H3 cells, no NULL or negative metric values, and - unlike
# Page 1 - each (h3_id, segment, hour_bucket) triple is NOT repeated (verified
# max repeat count = 1 on all three live tables), so no per-pair averaging
# step is needed before averaging across segments.
PAGE2_METRICS = PAGE1_METRICS

# Legacy Page 2 local-CSV index exports (superseded by the day-section
# BigQuery tables above). Kept only because the generic h3_id/segment/<metric>
# validation and aggregation helpers below are still useful and tested; Page 2
# itself no longer reads this two-metric, no-hour-column shape.
INDEX_METRICS = ("exclusivity_index", "volume_index")
INDEX_BASE_COLUMNS = ("h3_id", "segment")

# Page 2's day-section schema: h3_id, segment, hour_bucket, <the metric column>.
DAY_SECTION_BASE_COLUMNS = ("h3_id", "segment", "hour_bucket")

# Every metric that shares the h3_id/segment/<metric> index schema.
ALL_INDEX_METRICS = tuple(dict.fromkeys(PAGE1_METRICS + INDEX_METRICS))


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


def validate_aggregated_cells(
    frame: pd.DataFrame, value_column: str
) -> ValidationResult:
    """Validate an already-aggregated ``h3_id``/value frame.

    Page 1 aggregates in BigQuery, so the app only needs to confirm the two
    expected columns are present, coerce the value to a non-negative number, and
    drop rows whose ``h3_id`` is not a real H3 index before mapping them.
    """
    required = ("h3_id", value_column)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError("Missing required columns: " + ", ".join(missing))

    data = frame.loc[:, list(required)].copy()
    data["h3_id"] = data["h3_id"].astype("string").str.strip()
    data[value_column] = pd.to_numeric(data[value_column], errors="coerce")

    valid_rows = (
        _valid_h3_mask(data["h3_id"])
        & np.isfinite(data[value_column])
        & data[value_column].ge(0)
    )
    removed_rows = int((~valid_rows).sum())
    data = data.loc[valid_rows].copy()
    return ValidationResult(data=data, removed_rows=removed_rows)


def validate_index_data(frame: pd.DataFrame, metric: str) -> ValidationResult:
    """Validate an index export for one metric.

    The index files hold ``h3_id``, ``segment`` and a single index column. A
    file that carries several index columns is also accepted, so a future export
    combining them needs no code change.
    """
    if metric not in ALL_INDEX_METRICS:
        raise DataValidationError(
            f"Unknown metric '{metric}'. Expected one of: "
            + ", ".join(ALL_INDEX_METRICS)
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


def validate_day_section_data(frame: pd.DataFrame, metric: str) -> ValidationResult:
    """Validate a Page 2 day-section export for one metric.

    Same shape as :func:`validate_index_data` plus a required ``hour_bucket``
    (day-part) column - e.g. ``Morning`` / ``Noon`` / ``After noon`` / ``Night``
    / ``Other`` on the live tables, though the value set is not hard-coded here.
    """
    if metric not in PAGE2_METRICS:
        raise DataValidationError(
            f"Unknown metric '{metric}'. Expected one of: "
            + ", ".join(PAGE2_METRICS)
        )

    required = (*DAY_SECTION_BASE_COLUMNS, metric)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError("Missing required columns: " + ", ".join(missing))

    data = frame.loc[:, list(required)].copy()
    data["h3_id"] = data["h3_id"].astype("string").str.strip()
    data["segment"] = data["segment"].astype("string").str.strip()
    data["hour_bucket"] = data["hour_bucket"].astype("string").str.strip()
    data[metric] = pd.to_numeric(data[metric], errors="coerce")

    valid_rows = (
        _valid_h3_mask(data["h3_id"])
        & data["segment"].notna()
        & data["segment"].ne("")
        & data["hour_bucket"].notna()
        & data["hour_bucket"].ne("")
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


def collapse_day_section_duplicates(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Average any repeated rows for the same cell, segment and day-part.

    The live day-section tables carry no duplicates for a given
    ``(h3_id, segment, hour_bucket)`` triple (verified against BigQuery: max
    repeat count is 1), unlike Page 1's tables. This still guards a local CSV
    that happens to repeat a row - averaging one row is a no-op.
    """
    return (
        frame.groupby(list(DAY_SECTION_BASE_COLUMNS), as_index=False, sort=True)[metric]
        .mean()
    )


def aggregate_day_section_cells(
    frame: pd.DataFrame,
    selected_segments: list[str],
    selected_hour_bucket: str,
    metric: str,
) -> pd.DataFrame:
    """Filter to one day-part, then average the metric across selected segments."""
    selected = frame[
        frame["segment"].isin(selected_segments)
        & frame["hour_bucket"].eq(selected_hour_bucket)
    ]
    return selected.groupby("h3_id", as_index=False, sort=True)[metric].mean()

