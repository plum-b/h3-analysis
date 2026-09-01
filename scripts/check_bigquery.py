"""Verify BigQuery configuration, permissions, and the Page 1/2 table schemas.

Run this before launching the app or deploying:

    python3 scripts/check_bigquery.py

It resolves each metric's table from configuration, confirms the expected
columns exist, and runs the real segment (+ day-part, for Page 2) and
aggregation queries against a single segment so the cost stays negligible.
Nothing is written; no credential is read from the repository - authentication
is a ``[gcp_service_account]`` secret when one is configured (the Streamlit
Cloud path, readable here from a Git-ignored ``.streamlit/secrets.toml``),
otherwise Application Default Credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from h3_analysis.bigquery_source import (  # noqa: E402
    BigQueryConfigError,
    BigQueryCredentialsError,
    billing_project,
    credentials_source,
    build_day_parts_query,
    build_day_section_index_query,
    build_index_query,
    build_segments_query,
    build_two_hour_periods_query,
    day_section_table_fqn,
    get_client,
    index_table_fqn,
    run_query,
)
from h3_analysis.config import load_local_env  # noqa: E402
from h3_analysis.data import PAGE1_METRICS, PAGE2_METRICS  # noqa: E402


def check_page1_metric(client, metric: str) -> bool:
    print(f"\n=== Page 1: {metric} ===")
    try:
        table_fqn = index_table_fqn(metric)
    except BigQueryConfigError as error:
        print(f"FAIL  {error}")
        return False
    print(f"table   {table_fqn}")

    try:
        table = client.get_table(table_fqn)
    except Exception as error:
        print(f"FAIL  Cannot read table metadata: {error}")
        return False

    columns = {field.name: field.field_type for field in table.schema}
    print(f"rows    {table.num_rows:,}")
    required = ("h3_id", "segment", "hour_bucket", metric)
    missing = [name for name in required if name not in columns]
    if missing:
        print(f"FAIL  Missing expected column(s): {', '.join(missing)}")
        print(f"      Table columns: {', '.join(sorted(columns))}")
        return False
    print("schema  " + ", ".join(f"{name} {columns[name]}" for name in required))

    try:
        segments = run_query(build_segments_query(table_fqn), client=client)
        values = sorted(segments["segment"].dropna().astype(str))
        print(f"segments {len(values)}: {', '.join(values)}")
        if not values:
            print("FAIL  No segments returned.")
            return False

        periods = run_query(build_two_hour_periods_query(table_fqn), client=client)
        period_values = sorted(
            {int(period) for period in periods["hour_bucket"].dropna()}
        )
        print(
            f"periods {len(period_values)}: "
            + ", ".join(str(period) for period in period_values)
        )
        if not period_values:
            print("FAIL  No two-hour periods returned.")
            return False

        sql, params = build_index_query(
            table_fqn, metric, values[:1], period_values[0]
        )
        frame = run_query(sql, params, client=client)
        print(
            f"query   OK - {len(frame):,} cells for segment '{values[0]}', "
            f"two-hour period {period_values[0]}"
        )
        if not frame.empty:
            column = frame[metric]
            print(f"        {metric} min={column.min():.6g} max={column.max():.6g}")
    except Exception as error:
        print(f"FAIL  Query failed: {error}")
        print(
            f"      Needs roles/bigquery.jobUser on {client.project} "
            "(the billing project) and roles/bigquery.dataViewer on the "
            "dataset."
        )
        return False
    return True


def check_page2_metric(client, metric: str) -> bool:
    print(f"\n=== Page 2 (day-part): {metric} ===")
    try:
        table_fqn = day_section_table_fqn(metric)
    except BigQueryConfigError as error:
        print(f"FAIL  {error}")
        return False
    print(f"table   {table_fqn}")

    try:
        table = client.get_table(table_fqn)
    except Exception as error:
        print(f"FAIL  Cannot read table metadata: {error}")
        return False

    columns = {field.name: field.field_type for field in table.schema}
    print(f"rows    {table.num_rows:,}")
    required = ("h3_id", "segment", "hour_bucket", metric)
    missing = [name for name in required if name not in columns]
    if missing:
        print(f"FAIL  Missing expected column(s): {', '.join(missing)}")
        print(f"      Table columns: {', '.join(sorted(columns))}")
        return False
    print("schema  " + ", ".join(f"{name} {columns[name]}" for name in required))

    try:
        segments = run_query(build_segments_query(table_fqn), client=client)
        segment_values = sorted(segments["segment"].dropna().astype(str))
        print(f"segments {len(segment_values)}: {', '.join(segment_values)}")

        day_parts = run_query(build_day_parts_query(table_fqn), client=client)
        day_part_values = sorted(day_parts["hour_bucket"].dropna().astype(str))
        print(f"day-parts {len(day_part_values)}: {', '.join(day_part_values)}")

        if not segment_values or not day_part_values:
            print("FAIL  No segments or day-parts returned.")
            return False

        sql, params = build_day_section_index_query(
            table_fqn, metric, segment_values[:1], day_part_values[0]
        )
        frame = run_query(sql, params, client=client)
        print(
            f"query   OK - {len(frame):,} cells for segment "
            f"'{segment_values[0]}', day-part '{day_part_values[0]}'"
        )
        if not frame.empty:
            column = frame[metric]
            print(f"        {metric} min={column.min():.6g} max={column.max():.6g}")
    except Exception as error:
        print(f"FAIL  Query failed: {error}")
        print(
            f"      Needs roles/bigquery.jobUser on {client.project} "
            "(the billing project) and roles/bigquery.dataViewer on the "
            "dataset."
        )
        return False
    return True


def main() -> int:
    load_local_env()

    print(f"credentials      {credentials_source()}")
    try:
        client = get_client()
    except BigQueryCredentialsError as error:
        print(f"FAIL  {error}")
        return 1
    except Exception as error:
        print(f"FAIL  Could not create a BigQuery client: {error}")
        print("      Run: gcloud auth application-default login")
        return 1

    configured = billing_project()
    print(f"billing project  {client.project}")
    if not configured:
        print(
            "WARN  Neither BIGQUERY_BILLING_PROJECT nor BIGQUERY_PROJECT_ID is "
            "set, so jobs are billed to the Application Default Credentials "
            "project above. That is a common cause of a bigquery.jobs.create "
            "permission error naming a project you never configured."
        )

    failures = 0
    for metric in PAGE1_METRICS:
        if not check_page1_metric(client, metric):
            failures += 1
    for metric in PAGE2_METRICS:
        if not check_page2_metric(client, metric):
            failures += 1

    print("\n" + ("All checks passed." if not failures else f"{failures} check(s) failed."))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
