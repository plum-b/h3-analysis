"""BigQuery data access for Page 1 and Page 2 (the index-analysis maps).

This module is deliberately free of Streamlit imports so the query-construction
logic can be unit tested without credentials or a running app. The Streamlit
pages cache the results of :func:`run_query`.

Page 1 shows one index metric at a time, each stored in its own BigQuery table
with the logical schema ``h3_id`` (STRING), ``segment`` (STRING) and one numeric
metric column named after the metric (``overall_index`` / ``volume_index`` /
``exclusivity_index``). There is no hour column. Each ``(h3_id, segment)`` pair
is **repeated** (~8x on the live tables, unevenly), so the metric is averaged in
two steps - see :func:`build_index_query`.

Page 2 shows the same three metrics from a parallel set of "*_day_sections"
tables that add an ``hour_bucket`` (day-part, e.g. Morning/Noon/After
noon/Night/Other) column. Verified against the live tables: each
``(h3_id, segment, hour_bucket)`` triple is NOT repeated (max repeat count is
1), so :func:`build_day_section_index_query` averages across segments in a
single step - do not add a Page-1-style per-pair CTE here, it would just
average groups that already have exactly one row.

Configuration comes entirely from the environment. Per metric, either:

* ``BIGQUERY_<METRIC>_TABLE_FQN`` — a full ``project.dataset.table``; or
* ``BIGQUERY_PROJECT_ID`` + ``BIGQUERY_DATASET`` + ``BIGQUERY_<METRIC>_TABLE``

(Page 2 uses the same two forms with a ``_DAY_SECTIONS`` infix, e.g.
``BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE``.) ``<METRIC>`` is
``OVERALL_INDEX``, ``VOLUME_INDEX`` or ``EXCLUSIVITY_INDEX``. Nothing about the
project, dataset, table, credentials or filter values is hard-coded. Segment
(and day-part) filters are always passed as query parameters.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional, Sequence

import pandas as pd

from h3_analysis.data import PAGE1_METRICS, PAGE2_METRICS

# BigQuery identifier rules we accept from configuration. Project ids allow
# lowercase letters, digits and hyphens; dataset and table names allow letters,
# digits and underscores. Anything else is rejected before it can reach a
# FROM clause.
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")

_ENV_PROJECT = "BIGQUERY_PROJECT_ID"
_ENV_DATASET = "BIGQUERY_DATASET"


def _metric_env(metric: str, infix: str = "") -> tuple[str, str]:
    """Return the (``*_TABLE_FQN``, ``*_TABLE``) env var names for a metric.

    ``infix`` distinguishes Page 2's day-section tables (``_DAY_SECTIONS``)
    from Page 1's plain index tables (``""``).
    """
    stem = f"BIGQUERY_{metric.upper()}{infix}"
    return f"{stem}_TABLE_FQN", f"{stem}_TABLE"


class BigQueryConfigError(RuntimeError):
    """Raised when BigQuery configuration is missing or malformed."""


def _clean(env: Mapping[str, str], key: str) -> str:
    return (env.get(key) or "").strip()


def _validate_parts(project: str, dataset: str, table: str) -> str:
    if not _PROJECT_RE.match(project):
        raise BigQueryConfigError(f"Invalid BigQuery project id: '{project}'.")
    for label, value in (("dataset", dataset), ("table", table)):
        if not _NAME_RE.match(value):
            raise BigQueryConfigError(f"Invalid BigQuery {label} name: '{value}'.")
    return f"{project}.{dataset}.{table}"


def _resolve_table_fqn(
    metric: str,
    allowed_metrics: Sequence[str],
    page_label: str,
    infix: str,
    env: Optional[Mapping[str, str]],
) -> str:
    """Shared FQN resolution for both Page 1 and Page 2 metric tables.

    Prefers ``BIGQUERY_<METRIC>[infix]_TABLE_FQN``; otherwise composes the name
    from ``BIGQUERY_PROJECT_ID`` + ``BIGQUERY_DATASET`` +
    ``BIGQUERY_<METRIC>[infix]_TABLE``. Raises :class:`BigQueryConfigError` with
    an actionable message.
    """
    if metric not in allowed_metrics:
        raise BigQueryConfigError(
            f"Unknown {page_label} metric '{metric}'. Expected one of: "
            + ", ".join(allowed_metrics)
        )

    env = os.environ if env is None else env
    fqn_key, table_key = _metric_env(metric, infix)

    fqn = _clean(env, fqn_key)
    if fqn:
        parts = fqn.split(".")
        if len(parts) != 3 or not all(parts):
            raise BigQueryConfigError(
                f"{fqn_key} must be 'project.dataset.table', got '{fqn}'."
            )
        return _validate_parts(*parts)

    project = _clean(env, _ENV_PROJECT)
    dataset = _clean(env, _ENV_DATASET)
    table = _clean(env, table_key)
    missing = [
        name
        for name, value in (
            (_ENV_PROJECT, project),
            (_ENV_DATASET, dataset),
            (table_key, table),
        )
        if not value
    ]
    if missing:
        raise BigQueryConfigError(
            f"Set {fqn_key}, or all of "
            + ", ".join((_ENV_PROJECT, _ENV_DATASET, table_key))
            + ". Missing: "
            + ", ".join(missing)
            + "."
        )
    return _validate_parts(project, dataset, table)


def index_table_fqn(
    metric: str, env: Optional[Mapping[str, str]] = None
) -> str:
    """Resolve the fully qualified table name for one Page 1 metric.

    Prefers ``BIGQUERY_<METRIC>_TABLE_FQN``; otherwise composes the name from
    ``BIGQUERY_PROJECT_ID`` + ``BIGQUERY_DATASET`` + ``BIGQUERY_<METRIC>_TABLE``.
    Raises :class:`BigQueryConfigError` with an actionable message.
    """
    return _resolve_table_fqn(metric, PAGE1_METRICS, "Page 1", "", env)


def day_section_table_fqn(
    metric: str, env: Optional[Mapping[str, str]] = None
) -> str:
    """Resolve the fully qualified table name for one Page 2 day-section metric.

    Prefers ``BIGQUERY_<METRIC>_DAY_SECTIONS_TABLE_FQN``; otherwise composes the
    name from ``BIGQUERY_PROJECT_ID`` + ``BIGQUERY_DATASET`` +
    ``BIGQUERY_<METRIC>_DAY_SECTIONS_TABLE``.
    """
    return _resolve_table_fqn(
        metric, PAGE2_METRICS, "Page 2", "_DAY_SECTIONS", env
    )


def _backtick(table_fqn: str) -> str:
    # table_fqn has already been validated against the identifier patterns, so
    # there is nothing to escape; the backticks just satisfy standard SQL.
    return "`" + table_fqn + "`"


def build_segments_query(table_fqn: str) -> str:
    """SQL returning the distinct segment values for the checkboxes."""
    return (
        "SELECT DISTINCT segment\n"
        f"FROM {_backtick(table_fqn)}\n"
        "WHERE segment IS NOT NULL\n"
        "ORDER BY segment"
    )


def build_index_query(
    table_fqn: str, metric: str, segments: Sequence[str]
) -> tuple[str, dict]:
    """Build the aggregated index query and its named parameters.

    The tables repeat each ``(h3_id, segment)`` pair (~8x, unevenly), so this
    averages in **two steps**: first within each cell/segment pair, then across
    the selected segments. A single ``AVG ... GROUP BY h3_id`` would instead be a
    weighted average that lets whichever pair happens to carry more duplicate
    rows dominate the cell - measured against the live tables that changed 48.6%
    of cells, by up to 108% relative. This mirrors
    :func:`h3_analysis.data.collapse_index_duplicates` followed by
    :func:`h3_analysis.data.aggregate_index_cells` on the CSV path.

    Aggregation happens in BigQuery; only ``h3_id`` and the averaged metric come
    back. Segment values travel as ``@segments`` (an array), never interpolated
    into the SQL text. The metric name is validated against
    :data:`PAGE1_METRICS` before it is used as a column identifier.
    """
    if metric not in PAGE1_METRICS:
        raise ValueError(
            f"Unknown Page 1 metric '{metric}'. Expected one of: "
            + ", ".join(PAGE1_METRICS)
        )
    if not segments:
        raise ValueError("At least one segment is required.")
    segments = [str(segment) for segment in segments]

    sql = (
        "WITH per_pair AS (\n"
        f"  SELECT h3_id, segment, AVG({metric}) AS {metric}\n"
        f"  FROM {_backtick(table_fqn)}\n"
        "  WHERE segment IN UNNEST(@segments)\n"
        f"    AND {metric} IS NOT NULL\n"
        "  GROUP BY h3_id, segment\n"
        ")\n"
        f"SELECT h3_id, AVG({metric}) AS {metric}\n"
        "FROM per_pair\n"
        "GROUP BY h3_id\n"
        "ORDER BY h3_id"
    )
    return sql, {"segments": segments}


def build_day_parts_query(table_fqn: str) -> str:
    """SQL returning the distinct ``hour_bucket`` (day-part) values.

    Mirrors :func:`build_segments_query`. Values are not hard-coded anywhere in
    this module - on the live tables they are Morning / Noon / After noon /
    Night / Other, but this queries whatever the table actually contains.
    """
    return (
        "SELECT DISTINCT hour_bucket\n"
        f"FROM {_backtick(table_fqn)}\n"
        "WHERE hour_bucket IS NOT NULL\n"
        "ORDER BY hour_bucket"
    )


def build_day_section_index_query(
    table_fqn: str, metric: str, segments: Sequence[str], hour_bucket: str
) -> tuple[str, dict]:
    """Build the aggregated Page 2 day-section query and its named parameters.

    Unlike Page 1's tables, each ``(h3_id, segment, hour_bucket)`` triple is
    NOT repeated on the live day-section tables (verified against BigQuery:
    max repeat count is 1), so this averages across the selected segments in a
    **single** step - there is no duplicate-pair CTE to collapse first. This
    mirrors :func:`h3_analysis.data.aggregate_day_section_cells` on the CSV
    path (:func:`h3_analysis.data.collapse_day_section_duplicates` is a no-op
    defensive step there for the same reason).

    Aggregation happens in BigQuery; only ``h3_id`` and the averaged metric
    come back. Segment values travel as ``@segments`` (an array) and the
    day-part as ``@hour_bucket`` (a scalar), never interpolated into the SQL
    text. The metric name is validated against :data:`PAGE2_METRICS` before it
    is used as a column identifier.
    """
    if metric not in PAGE2_METRICS:
        raise ValueError(
            f"Unknown Page 2 metric '{metric}'. Expected one of: "
            + ", ".join(PAGE2_METRICS)
        )
    if not segments:
        raise ValueError("At least one segment is required.")
    if not hour_bucket:
        raise ValueError("A day-part (hour_bucket) is required.")
    segments = [str(segment) for segment in segments]

    sql = (
        f"SELECT h3_id, AVG({metric}) AS {metric}\n"
        f"FROM {_backtick(table_fqn)}\n"
        "WHERE segment IN UNNEST(@segments)\n"
        "  AND hour_bucket = @hour_bucket\n"
        f"  AND {metric} IS NOT NULL\n"
        "GROUP BY h3_id\n"
        "ORDER BY h3_id"
    )
    return sql, {"segments": segments, "hour_bucket": str(hour_bucket)}


def _query_parameters(params: Mapping[str, object]):
    from google.cloud import bigquery

    job_params = []
    for name, value in params.items():
        if isinstance(value, (list, tuple)):
            job_params.append(
                bigquery.ArrayQueryParameter(name, "STRING", list(value))
            )
        elif isinstance(value, bool):
            job_params.append(bigquery.ScalarQueryParameter(name, "BOOL", value))
        elif isinstance(value, int):
            job_params.append(bigquery.ScalarQueryParameter(name, "INT64", value))
        elif isinstance(value, float):
            job_params.append(
                bigquery.ScalarQueryParameter(name, "FLOAT64", value)
            )
        else:
            job_params.append(
                bigquery.ScalarQueryParameter(name, "STRING", str(value))
            )
    return job_params


def get_client(project: Optional[str] = None):
    """Create a BigQuery client using Application Default Credentials."""
    from google.cloud import bigquery

    return bigquery.Client(project=project) if project else bigquery.Client()


def run_query(
    sql: str,
    params: Optional[Mapping[str, object]] = None,
    client=None,
) -> pd.DataFrame:
    """Execute a parameterized query and return a DataFrame."""
    from google.cloud import bigquery

    client = client or get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=_query_parameters(params or {})
    )
    return client.query(sql, job_config=job_config).result().to_dataframe()
