"""Verify BigQuery configuration, permissions, and the Page 1 table schemas.

Run this before launching the app or deploying:

    python3 scripts/check_bigquery.py

It resolves each metric's table from configuration, confirms the expected
columns exist, and runs the real segment + aggregation queries against a single
segment so the cost stays negligible. Nothing is written; no credential is read
from the repository - authentication is Application Default Credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from h3_analysis.bigquery_source import (  # noqa: E402
    BigQueryConfigError,
    build_index_query,
    build_segments_query,
    get_client,
    index_table_fqn,
    run_query,
)
from h3_analysis.config import load_local_env  # noqa: E402
from h3_analysis.data import PAGE1_METRICS  # noqa: E402


def main() -> int:
    load_local_env()

    try:
        client = get_client()
    except Exception as error:
        print(f"FAIL  Could not create a BigQuery client: {error}")
        print("      Run: gcloud auth application-default login")
        return 1

    failures = 0
    for metric in PAGE1_METRICS:
        print(f"\n=== {metric} ===")
        try:
            table_fqn = index_table_fqn(metric)
        except BigQueryConfigError as error:
            print(f"FAIL  {error}")
            failures += 1
            continue
        print(f"table   {table_fqn}")

        try:
            table = client.get_table(table_fqn)
        except Exception as error:
            print(f"FAIL  Cannot read table metadata: {error}")
            failures += 1
            continue

        columns = {field.name: field.field_type for field in table.schema}
        print(f"rows    {table.num_rows:,}")
        missing = [name for name in ("h3_id", "segment", metric) if name not in columns]
        if missing:
            print(f"FAIL  Missing expected column(s): {', '.join(missing)}")
            print(f"      Table columns: {', '.join(sorted(columns))}")
            failures += 1
            continue
        print(
            "schema  "
            + ", ".join(f"{name} {columns[name]}" for name in ("h3_id", "segment", metric))
        )

        try:
            segments = run_query(build_segments_query(table_fqn), client=client)
            values = sorted(segments["segment"].dropna().astype(str))
            print(f"segments {len(values)}: {', '.join(values)}")
            if not values:
                print("FAIL  No segments returned.")
                failures += 1
                continue

            sql, params = build_index_query(table_fqn, metric, values[:1])
            frame = run_query(sql, params, client=client)
            print(f"query   OK - {len(frame):,} cells for segment '{values[0]}'")
            if not frame.empty:
                column = frame[metric]
                print(
                    f"        {metric} min={column.min():.6g} "
                    f"max={column.max():.6g}"
                )
        except Exception as error:
            print(f"FAIL  Query failed: {error}")
            print(
                "      Needs roles/bigquery.jobUser on the project and "
                "roles/bigquery.dataViewer on the dataset."
            )
            failures += 1

    print("\n" + ("All checks passed." if not failures else f"{failures} check(s) failed."))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
