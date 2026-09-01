"""BigQuery data access for Page 1 and Page 2 (the index-analysis maps).

This module is deliberately free of Streamlit imports so the query-construction
logic can be unit tested without credentials or a running app. The Streamlit
pages cache the results of :func:`run_query`.

Page 1 shows one index metric at a time, each stored in its own BigQuery table
with the logical schema ``h3_id`` (STRING), ``segment`` (STRING) and one numeric
metric column named after the metric (``overall_index`` / ``volume_index`` /
``exclusivity_index``), plus an ``hour_bucket`` (INT64) column holding the
**two-hour period** of the day: 0, 2, 4 ... 22 on the live tables. Each
``(h3_id, segment, hour_bucket)`` triple appears exactly once (verified against
BigQuery), which is what makes a ``(h3_id, segment)`` pair look "repeated"
~8.4x - the twelve two-hour periods are the repetition. Page 1 filters to
exactly one two-hour period and averages in two steps - see
:func:`build_index_query`.

Both pages' tables name this column ``hour_bucket``, but the domains differ:
Page 1 stores INT64 two-hour periods, Page 2 STRING day-part labels. The two
are never mixed.

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

Credentials come from the platform, never from this repository - a
``[gcp_service_account]`` secret when one is configured (the Streamlit
Community Cloud path), otherwise Application Default Credentials. See
:data:`CREDENTIALS_HELP` and :func:`get_client`.
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
# Project the query JOB is billed to. Defaults to the data project; set this
# only when jobs must be billed to a different project from the tables.
_ENV_BILLING_PROJECT = "BIGQUERY_BILLING_PROJECT"

# Both pages' tables call the time column ``hour_bucket``. On Page 1 it holds
# INT64 two-hour periods (0, 2, ... 22); on Page 2, STRING day-part labels.
TIME_COLUMN = "hour_bucket"


def _metric_env(metric: str, infix: str = "") -> tuple[str, str]:
    """Return the (``*_TABLE_FQN``, ``*_TABLE``) env var names for a metric.

    ``infix`` distinguishes Page 2's day-section tables (``_DAY_SECTIONS``)
    from Page 1's plain index tables (``""``).
    """
    stem = f"BIGQUERY_{metric.upper()}{infix}"
    return f"{stem}_TABLE_FQN", f"{stem}_TABLE"


class BigQueryConfigError(RuntimeError):
    """Raised when BigQuery configuration is missing or malformed."""


class BigQueryCredentialsError(BigQueryConfigError):
    """Raised when no usable Google Cloud credentials are available.

    A subclass of :class:`BigQueryConfigError` so existing handlers keep
    working; the pages catch it first to show credential-specific guidance.
    """


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


def coerce_two_hour_period(value) -> int:
    """Return ``value`` as the INT64 two-hour period, or raise ``ValueError``.

    The selector's value reaches this as a Python ``int``, a NumPy integer from
    a BigQuery result, or a digit string from a widget. Anything else - a float
    with a fraction, ``None``, free text - is rejected here rather than being
    passed to BigQuery, so a bad value can never reach the query at all.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid two-hour period: {value!r}.")
    try:
        period = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid two-hour period: {value!r}.") from None
    if period != float(value):
        raise ValueError(f"Invalid two-hour period: {value!r}.")
    return period


def build_index_query(
    table_fqn: str,
    metric: str,
    segments: Sequence[str],
    two_hour_period,
) -> tuple[str, dict]:
    """Build the aggregated index query and its named parameters.

    Page 1 always shows exactly one two-hour period, so the query filters
    ``hour_bucket = @two_hour_period`` before aggregating, then averages in
    **two steps**: first within each ``(h3_id, segment, hour_bucket)`` group,
    then across the selected segments. Keeping the per-group step means the
    outer average weights every selected segment equally however many rows it
    contributes - collapsing it into a single ``AVG ... GROUP BY h3_id`` would
    be a row-weighted average instead. This mirrors
    :func:`h3_analysis.data.collapse_index_duplicates` followed by
    :func:`h3_analysis.data.aggregate_index_cells` on the CSV path.

    Aggregation happens in BigQuery; only ``h3_id`` and the averaged metric come
    back. Segment values travel as ``@segments`` (an array) and the period as
    ``@two_hour_period`` (an INT64 scalar), never interpolated into the SQL
    text. The metric name is validated against :data:`PAGE1_METRICS` before it
    is used as a column identifier.
    """
    if metric not in PAGE1_METRICS:
        raise ValueError(
            f"Unknown Page 1 metric '{metric}'. Expected one of: "
            + ", ".join(PAGE1_METRICS)
        )
    if not segments:
        raise ValueError("At least one segment is required.")
    period = coerce_two_hour_period(two_hour_period)
    segments = [str(segment) for segment in segments]

    sql = (
        "WITH per_pair AS (\n"
        f"  SELECT h3_id, segment, {TIME_COLUMN}, AVG({metric}) AS {metric}\n"
        f"  FROM {_backtick(table_fqn)}\n"
        "  WHERE segment IN UNNEST(@segments)\n"
        f"    AND {TIME_COLUMN} = @two_hour_period\n"
        f"    AND {metric} IS NOT NULL\n"
        f"  GROUP BY h3_id, segment, {TIME_COLUMN}\n"
        ")\n"
        f"SELECT h3_id, AVG({metric}) AS {metric}\n"
        "FROM per_pair\n"
        "GROUP BY h3_id\n"
        "ORDER BY h3_id"
    )
    return sql, {"segments": segments, "two_hour_period": period}

def _distinct_time_values_query(table_fqn: str) -> str:
    """SQL listing the distinct ``hour_bucket`` values a table actually holds.

    Shared by both pages' time selectors. Neither the two-hour periods nor the
    day-part labels are hard-coded anywhere in this module - whatever the table
    contains is what the selector offers.
    """
    return (
        f"SELECT DISTINCT {TIME_COLUMN}\n"
        f"FROM {_backtick(table_fqn)}\n"
        f"WHERE {TIME_COLUMN} IS NOT NULL\n"
        f"ORDER BY {TIME_COLUMN}"
    )


def build_two_hour_periods_query(table_fqn: str) -> str:
    """SQL returning Page 1's distinct two-hour periods (INT64: 0, 2, ... 22)."""
    return _distinct_time_values_query(table_fqn)


def build_day_parts_query(table_fqn: str) -> str:
    """SQL returning Page 2's distinct ``hour_bucket`` day-part labels.

    Mirrors :func:`build_segments_query`. On the live tables the values are
    Morning / Noon / After noon / Night / Other.
    """
    return _distinct_time_values_query(table_fqn)


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


# --- Credentials -----------------------------------------------------------
# Two supported paths, tried in this order:
#
# 1. A service account supplied by the platform's secrets store, under a
#    ``[gcp_service_account]`` table. Streamlit Community Cloud needs this: it
#    runs outside Google Cloud, so there is no metadata server and Application
#    Default Credentials fail trying to reach ``metadata.google.internal``.
# 2. Application Default Credentials - ``gcloud auth application-default
#    login`` locally, or the attached runtime service account on Cloud Run.
#
# No key material is ever read from the repository. The secret is supplied by
# Streamlit Cloud's Secrets UI, or, for local work, by the Git-ignored
# ``.streamlit/secrets.toml``.

SERVICE_ACCOUNT_SECRET_KEY = "gcp_service_account"

# ``from_service_account_info`` itself only requires ``client_email`` and
# ``token_uri``; the rest are checked here so a truncated paste is reported as
# configuration rather than as a signing failure deep inside google-auth.
REQUIRED_SERVICE_ACCOUNT_FIELDS = (
    "type",
    "project_id",
    "private_key_id",
    "private_key",
    "client_email",
    "token_uri",
)

_CREDENTIAL_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

CREDENTIALS_HELP = (
    "No usable Google Cloud credentials were found.\n\n"
    "On Streamlit Community Cloud there is no metadata server, so Application "
    "Default Credentials cannot work. Add a service account to the app's "
    "Secrets (Manage app > Settings > Secrets) as a "
    f"`[{SERVICE_ACCOUNT_SECRET_KEY}]` table holding the fields of the "
    "service account's JSON key: "
    + ", ".join(f"`{field}`" for field in REQUIRED_SERVICE_ACCOUNT_FIELDS)
    + ". The same table can live in the Git-ignored "
    "`.streamlit/secrets.toml` for local runs.\n\n"
    "Running locally or on Cloud Run instead? Use Application Default "
    "Credentials: `gcloud auth application-default login`, or the attached "
    "runtime service account."
)

# Substrings that mark "there are no credentials here" rather than a genuine
# BigQuery/IAM failure. The first is what Streamlit Community Cloud reports.
_MISSING_CREDENTIAL_MARKERS = (
    "metadata.google.internal",
    "compute engine metadata",
    "could not automatically determine credentials",
    "default credentials",
)

# Sentinel meaning "look the secrets up yourself". ``None`` means "there are no
# secrets", which is a normal state and not an error.
_DISCOVER = object()


def _streamlit_secrets():
    """Return Streamlit's secrets mapping, or ``None`` when there is none.

    Streamlit is imported lazily so this module stays importable - and unit
    testable - without it. A missing secrets file is not an error: it is the
    normal local-development case, where Application Default Credentials apply.
    """
    try:
        import streamlit as st
    except Exception:  # streamlit is not installed
        return None
    try:
        secrets = st.secrets
        if SERVICE_ACCOUNT_SECRET_KEY not in secrets:
            return None
    except Exception:  # no secrets file at all
        return None
    return secrets


def service_account_info(secrets=_DISCOVER) -> Optional[dict]:
    """Return the validated ``[gcp_service_account]`` mapping, or ``None``.

    ``None`` means no service account is configured, so the caller falls back
    to Application Default Credentials. A section that is present but
    incomplete raises :class:`BigQueryCredentialsError` naming the missing
    keys - silently falling back would only resurface as the confusing
    ``metadata.google.internal`` error.
    """
    if secrets is _DISCOVER:
        secrets = _streamlit_secrets()
    if not secrets:
        return None

    try:
        raw = secrets[SERVICE_ACCOUNT_SECRET_KEY]
    except (KeyError, TypeError):
        return None
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise BigQueryCredentialsError(
            f"The [{SERVICE_ACCOUNT_SECRET_KEY}] secret must be a TOML table "
            "holding the fields of the service account's JSON key."
        )

    info = {str(key): value for key, value in raw.items()}
    missing = [
        field
        for field in REQUIRED_SERVICE_ACCOUNT_FIELDS
        if not str(info.get(field) or "").strip()
    ]
    if missing:
        raise BigQueryCredentialsError(
            f"The [{SERVICE_ACCOUNT_SECRET_KEY}] secret is missing: "
            + ", ".join(missing)
            + ". Copy every field from the service account's JSON key."
        )

    # A private key pasted on one line keeps its "\n" as two characters;
    # google-auth needs real newlines. A TOML triple-quoted value already has
    # them, so only the escaped form is converted.
    private_key = info["private_key"]
    if isinstance(private_key, str):
        info["private_key"] = private_key.replace("\\n", "\n")
    return info


def credentials_from_secrets(secrets=_DISCOVER):
    """Build service-account credentials from secrets, or ``None`` if unset."""
    info = service_account_info(secrets)
    if info is None:
        return None

    from google.oauth2 import service_account

    try:
        return service_account.Credentials.from_service_account_info(
            info, scopes=list(_CREDENTIAL_SCOPES)
        )
    except Exception as error:
        raise BigQueryCredentialsError(
            f"The [{SERVICE_ACCOUNT_SECRET_KEY}] secret is not a usable "
            f"service-account key: {error}"
        ) from error


def credentials_source(secrets=_DISCOVER) -> str:
    """Describe which credential path applies, for logs and the check script."""
    if service_account_info(secrets) is None:
        return "Application Default Credentials"
    return f"service account from secrets [{SERVICE_ACCOUNT_SECRET_KEY}]"


def _is_missing_credentials(error: BaseException) -> bool:
    """True when ``error`` means "no credentials", not "access denied"."""
    try:
        from google.auth import exceptions as auth_exceptions
    except Exception:
        auth_exceptions = None
    if auth_exceptions is not None and isinstance(
        error,
        (auth_exceptions.DefaultCredentialsError, auth_exceptions.TransportError),
    ):
        return True
    message = str(error).lower()
    return any(marker in message for marker in _MISSING_CREDENTIAL_MARKERS)


def _raise_for_missing_credentials(error: BaseException) -> None:
    """Re-raise ``error`` as a :class:`BigQueryCredentialsError` if it is one."""
    if isinstance(error, BigQueryCredentialsError):
        raise error
    if _is_missing_credentials(error):
        raise BigQueryCredentialsError(f"{error}\n\n{CREDENTIALS_HELP}") from error


def billing_project(env: Optional[Mapping[str, str]] = None) -> str:
    """Project that query jobs are billed to, from configuration.

    Prefers ``BIGQUERY_BILLING_PROJECT``, else the data project
    ``BIGQUERY_PROJECT_ID``. Returns ``""`` when neither is set, which leaves
    the client on the Application Default Credentials' own project.

    Without this, ``bigquery.Client()`` bills jobs to whatever project the local
    ``gcloud`` config points at, which fails with "does not have
    bigquery.jobs.create permission in project ..." naming a project nobody
    configured - even though the tables themselves are perfectly readable.
    """
    env = os.environ if env is None else env
    return _clean(env, _ENV_BILLING_PROJECT) or _clean(env, _ENV_PROJECT)


def get_client(project: Optional[str] = None, credentials=None):
    """Create a BigQuery client from secrets or Application Default Credentials.

    A ``[gcp_service_account]`` secret wins when one is configured - that is
    the Streamlit Community Cloud path, where ADC cannot work. Otherwise the
    client falls back to Application Default Credentials, leaving local
    development and Cloud Run exactly as they were.

    ``project`` is the billing project for the jobs this client runs; it
    defaults to the configured one (see :func:`billing_project`), then to the
    service account's own project.
    """
    from google.cloud import bigquery

    if credentials is None:
        credentials = credentials_from_secrets()
    project = (
        project
        or billing_project()
        or (getattr(credentials, "project_id", "") if credentials else "")
    )
    try:
        if credentials is not None:
            return bigquery.Client(project=project or None, credentials=credentials)
        return bigquery.Client(project=project) if project else bigquery.Client()
    except Exception as error:
        _raise_for_missing_credentials(error)
        raise


def run_query(
    sql: str,
    params: Optional[Mapping[str, object]] = None,
    client=None,
) -> pd.DataFrame:
    """Execute a parameterized query and return a DataFrame."""
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=_query_parameters(params or {})
    )
    try:
        client = client or get_client()
        return client.query(sql, job_config=job_config).result().to_dataframe()
    except Exception as error:
        # Credentials can also fail when the token is first used rather than
        # when the client is built - on Streamlit Cloud that surfaces here, as
        # an unreachable metadata.google.internal.
        _raise_for_missing_credentials(error)
        raise
