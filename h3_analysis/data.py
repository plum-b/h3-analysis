from __future__ import annotations

from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd

# Page 1 (BigQuery) shows one of these index metrics, each from its own table.
# Schema per table: h3_id, segment, <the metric column>, hour_bucket - where
# hour_bucket is an INT64 *two-hour period* of the day (0, 2, ... 22 on the
# live tables). Verified against BigQuery: each (h3_id, segment, hour_bucket)
# triple appears exactly once, so the ~8.4 rows per (h3_id, segment) pair are
# the twelve two-hour periods, not true duplicates.
#
# The exclusivity index was removed from the application: it is offered by no
# selector, its tables are not configured, and nothing queries them.
PAGE1_METRICS = ("overall_index", "volume_index")

# Page 2 (BigQuery, day-part) shows the same two metrics, each from its own
# "*_day_sections" table with the added ``hour_bucket`` (day-part) and
# ``Week_part`` (Weekday / Weekend) columns. Confirmed against the live tables:
# h3_id/<metric>/segment/hour_bucket/Week_part, all resolution-9 H3 cells, no
# NULL or negative metric values, and - unlike Page 1 - each
# (h3_id, segment, hour_bucket, Week_part) row is NOT repeated (verified max
# repeat count = 1 on both live tables), so no per-pair averaging step is
# needed before averaging across segments.
PAGE2_METRICS = PAGE1_METRICS

# Legacy Page 2 local-CSV index exports (superseded by the day-section
# BigQuery tables above). Kept only because the generic h3_id/segment/<metric>
# validation and aggregation helpers below are still useful and tested; Page 2
# itself no longer reads this no-hour-column shape. Exclusivity was the other
# metric in that export and is gone with the rest of it.
INDEX_METRICS = ("volume_index",)
INDEX_BASE_COLUMNS = ("h3_id", "segment")

# Page 2's day-section schema: h3_id, segment, hour_bucket, Week_part and the
# metric column. ``Week_part`` splits every day-part into Weekday and Weekend,
# so it is part of the key, not an attribute of it.
WEEK_PART_COLUMN = "Week_part"
DAY_SECTION_BASE_COLUMNS = ("h3_id", "segment", "hour_bucket", WEEK_PART_COLUMN)

# Page 1's schema: the same three keys, but hour_bucket is the INT64 two-hour
# period rather than Page 2's day-part label.
TWO_HOUR_BASE_COLUMNS = ("h3_id", "segment", "hour_bucket")

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
    / ``Other`` on the live tables - and a required ``Week_part`` column
    (``Weekday`` / ``Weekend``). Neither value set is hard-coded here; the
    selectors offer whatever the data holds.
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
    data[WEEK_PART_COLUMN] = data[WEEK_PART_COLUMN].astype("string").str.strip()
    data[metric] = pd.to_numeric(data[metric], errors="coerce")

    valid_rows = (
        _valid_h3_mask(data["h3_id"])
        & data["segment"].notna()
        & data["segment"].ne("")
        & data["hour_bucket"].notna()
        & data["hour_bucket"].ne("")
        & data[WEEK_PART_COLUMN].notna()
        & data[WEEK_PART_COLUMN].ne("")
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
    """Average repeated rows for the same cell, segment, day-part and week-part.

    The live day-section tables carry no duplicates for a given
    ``(h3_id, segment, hour_bucket, Week_part)`` row (verified against
    BigQuery: max repeat count is 1), unlike Page 1's tables. This still guards
    a local CSV that happens to repeat a row - averaging one row is a no-op.
    """
    return (
        frame.groupby(list(DAY_SECTION_BASE_COLUMNS), as_index=False, sort=True)[metric]
        .mean()
    )


def aggregate_day_section_cells(
    frame: pd.DataFrame,
    selected_segments: list[str],
    selected_hour_bucket: str,
    selected_week_part: str,
    metric: str,
) -> pd.DataFrame:
    """Filter to one day-part *and* week-part, then average across segments.

    Mirrors :func:`h3_analysis.bigquery_source.build_day_section_index_query`:
    both filters apply together, so Weekday Morning and Weekend Morning are
    different views of the map rather than one blended average.
    """
    selected = frame[
        frame["segment"].isin(selected_segments)
        & frame["hour_bucket"].eq(selected_hour_bucket)
        & frame[WEEK_PART_COLUMN].eq(selected_week_part)
    ]
    return selected.groupby("h3_id", as_index=False, sort=True)[metric].mean()



def validate_two_hour_index_data(
    frame: pd.DataFrame, metric: str
) -> ValidationResult:
    """Validate a Page 1 export for one metric, including its two-hour period.

    Same shape as :func:`validate_index_data` plus a required, integral
    ``hour_bucket`` column - the two-hour period of the day. Rows whose period
    is missing or fractional are dropped rather than silently coerced, because a
    half-period would land in no selectable bucket.
    """
    if metric not in PAGE1_METRICS:
        raise DataValidationError(
            f"Unknown metric '{metric}'. Expected one of: "
            + ", ".join(PAGE1_METRICS)
        )

    required = (*TWO_HOUR_BASE_COLUMNS, metric)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError("Missing required columns: " + ", ".join(missing))

    data = frame.loc[:, list(required)].copy()
    data["h3_id"] = data["h3_id"].astype("string").str.strip()
    data["segment"] = data["segment"].astype("string").str.strip()
    periods = pd.to_numeric(data["hour_bucket"], errors="coerce")
    data[metric] = pd.to_numeric(data[metric], errors="coerce")

    valid_rows = (
        _valid_h3_mask(data["h3_id"])
        & data["segment"].notna()
        & data["segment"].ne("")
        & np.isfinite(periods)
        & periods.eq(periods.round())
        & np.isfinite(data[metric])
        & data[metric].ge(0)
    )

    removed_rows = int((~valid_rows).sum())
    data = data.loc[valid_rows].copy()
    if data.empty:
        raise DataValidationError(
            f"The CSV contains no valid '{metric}' rows."
        )
    data["hour_bucket"] = periods.loc[valid_rows].astype(int)

    return ValidationResult(data=data, removed_rows=removed_rows)


def collapse_two_hour_duplicates(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Average any repeated rows for the same cell, segment and two-hour period.

    The live tables hold exactly one row per (h3_id, segment, hour_bucket)
    triple, so this is normally a no-op; it guards a local CSV that repeats a
    row. Averaging - never summing - keeps a normalized index in range.
    """
    return (
        frame.groupby(list(TWO_HOUR_BASE_COLUMNS), as_index=False, sort=True)[metric]
        .mean()
    )


def aggregate_two_hour_index_cells(
    frame: pd.DataFrame,
    selected_segments: list,
    selected_period: int,
    metric: str,
) -> pd.DataFrame:
    """Filter to one two-hour period, then average across selected segments.

    Mirrors :func:`h3_analysis.bigquery_source.build_index_query`: the frame
    reaching here has already been collapsed per
    (h3_id, segment, hour_bucket), so this is the second averaging step and it
    weights each selected segment equally.
    """
    selected = frame[
        frame["segment"].isin(selected_segments)
        & frame["hour_bucket"].eq(selected_period)
    ]
    return selected.groupby("h3_id", as_index=False, sort=True)[metric].mean()
