import unittest

import h3
import pandas as pd

from h3_analysis.bigquery_source import (
    BigQueryConfigError,
    BigQueryCredentialsError,
    REQUIRED_SERVICE_ACCOUNT_FIELDS,
    SERVICE_ACCOUNT_SECRET_KEY,
    billing_project,
    credentials_source,
    build_day_parts_query,
    build_day_section_index_query,
    build_index_query,
    build_segments_query,
    build_two_hour_periods_query,
    coerce_two_hour_period,
    day_section_table_fqn,
    index_table_fqn,
    service_account_info,
)
from h3_analysis.data import DataValidationError, validate_aggregated_cells


class TableFqnTests(unittest.TestCase):
    def test_per_metric_fqn_env_wins(self):
        env = {
            "BIGQUERY_OVERALL_INDEX_TABLE_FQN": "your-gcp-project.your_dataset.overall_index_table",
            "BIGQUERY_PROJECT_ID": "ignored",
        }
        self.assertEqual(
            index_table_fqn("overall_index", env),
            "your-gcp-project.your_dataset.overall_index_table",
        )

    def test_three_part_config_is_composed_per_metric(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_VOLUME_INDEX_TABLE": "volume_index_table",
            "BIGQUERY_EXCLUSIVITY_INDEX_TABLE": "exclusivity_index_table",
        }
        self.assertEqual(
            index_table_fqn("volume_index", env),
            "your-gcp-project.your_dataset.volume_index_table",
        )
        self.assertEqual(
            index_table_fqn("exclusivity_index", env),
            "your-gcp-project.your_dataset.exclusivity_index_table",
        )

    def test_missing_config_names_the_metric_specific_var(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
        }
        with self.assertRaisesRegex(
            BigQueryConfigError, "BIGQUERY_OVERALL_INDEX_TABLE"
        ):
            index_table_fqn("overall_index", env)

    def test_empty_env_raises(self):
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn("volume_index", {})

    def test_unknown_metric_rejected(self):
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn("made_up_index", {})

    def test_malformed_fqn_raises(self):
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn(
                "volume_index", {"BIGQUERY_VOLUME_INDEX_TABLE_FQN": "just.two"}
            )

    def test_injection_attempt_in_table_name_is_rejected(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_VOLUME_INDEX_TABLE": "t`; DROP TABLE x; --",
        }
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn("volume_index", env)


class QueryConstructionTests(unittest.TestCase):
    fqn = "your-gcp-project.your_dataset.volume_index_table"

    def test_index_query_is_parameterized_and_aggregates(self):
        sql, params = build_index_query(
            self.fqn, "volume_index", ["Families", "HNWI"], 14
        )
        self.assertIn("AVG(volume_index) AS volume_index", sql)
        self.assertIn("GROUP BY h3_id", sql)
        self.assertIn("@segments", sql)
        self.assertIn(f"`{self.fqn}`", sql)
        self.assertEqual(
            params,
            {"segments": ["Families", "HNWI"], "two_hour_period": 14},
        )

    def test_averages_in_two_steps_not_one(self):
        """Live tables repeat each (h3_id, segment) pair ~8x, unevenly.

        A single AVG grouped by h3_id would be a weighted average dominated by
        whichever pair carries more duplicate rows - that changed 48.6% of
        cells (up to 108% relative) against the real data.
        """
        sql, _ = build_index_query(self.fqn, "overall_index", ["Families"], 8)
        self.assertIn("GROUP BY h3_id, segment, hour_bucket", sql)
        self.assertIn("per_pair", sql)
        # The outer average must read the collapsed pairs, not the raw table.
        outer = sql.split("per_pair AS (")[1].split(")")[-1]
        self.assertIn("FROM per_pair", outer)
        self.assertEqual(sql.count(f"`{self.fqn}`"), 1)

    def test_every_metric_uses_the_two_step_shape(self):
        from h3_analysis.data import PAGE1_METRICS

        for metric in PAGE1_METRICS:
            with self.subTest(metric=metric):
                sql, params = build_index_query(
                    self.fqn, metric, ["Families"], 6
                )
                self.assertIn("GROUP BY h3_id, segment, hour_bucket", sql)
                self.assertIn(f"AVG({metric}) AS {metric}", sql)
                # The two-hour filter applies to every metric, not just one.
                self.assertIn("hour_bucket = @two_hour_period", sql)
                self.assertEqual(params["two_hour_period"], 6)

    def test_segment_values_never_appear_in_sql_text(self):
        sql, _ = build_index_query(
            self.fqn, "overall_index", ["Fam'ilies", "HN;WI"], 22
        )
        self.assertNotIn("Fam'ilies", sql)
        self.assertNotIn("HN;WI", sql)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            build_index_query(self.fqn, "drop_table", ["Families"], 0)

    def test_empty_segments_rejected(self):
        with self.assertRaises(ValueError):
            build_index_query(self.fqn, "volume_index", [], 0)

    def test_two_hour_period_is_a_parameter_never_sql_text(self):
        """A hostile period must reach BigQuery as a value or not at all."""
        with self.assertRaises(ValueError):
            build_index_query(
                self.fqn, "volume_index", ["Families"], "0 OR 1=1; --"
            )
        sql, params = build_index_query(self.fqn, "volume_index", ["Families"], 12)
        self.assertNotIn("12", sql)
        self.assertEqual(params["two_hour_period"], 12)

    def test_two_hour_period_accepts_integral_values_only(self):
        self.assertEqual(coerce_two_hour_period("8"), 8)
        self.assertEqual(coerce_two_hour_period(8.0), 8)
        for bad in ("morning", 3.5, None, True, ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    coerce_two_hour_period(bad)

    def test_two_hour_periods_query_lists_the_column(self):
        sql = build_two_hour_periods_query(self.fqn)
        self.assertIn("SELECT DISTINCT hour_bucket", sql)
        self.assertNotIn("segment", sql)
        # The 0/2/.../22 domain comes from the table, never from the code.
        self.assertNotIn("22", sql)

    def test_segments_query_selects_only_segment(self):
        sql = build_segments_query(self.fqn)
        self.assertIn("SELECT DISTINCT segment", sql)
        self.assertNotIn("hour_bucket", sql)
        self.assertNotIn("user_count", sql)


class BillingProjectTests(unittest.TestCase):
    """Jobs must be billed to the configured project.

    Falling through to the ambient ADC project produces a
    "does not have bigquery.jobs.create permission in project ..." error that
    names a project nobody configured, while the tables themselves are readable.
    """

    def test_defaults_to_the_data_project(self):
        self.assertEqual(
            billing_project({"BIGQUERY_PROJECT_ID": "your-gcp-project"}), "your-gcp-project"
        )

    def test_explicit_billing_project_wins(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_BILLING_PROJECT": "billing-project",
        }
        self.assertEqual(billing_project(env), "billing-project")

    def test_unset_returns_empty_so_adc_default_applies(self):
        self.assertEqual(billing_project({}), "")


class DayFqnTests(unittest.TestCase):
    """Page 2's day-section tables use the same two config forms as Page 1,
    with a ``_DAY_SECTIONS`` infix so they never collide with Page 1's vars."""

    def test_per_metric_fqn_env_wins(self):
        env = {
            "BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE_FQN": (
                "your-gcp-project.your_dataset.overall_index_day_sections_table"
            ),
            "BIGQUERY_PROJECT_ID": "ignored",
        }
        self.assertEqual(
            day_section_table_fqn("overall_index", env),
            "your-gcp-project.your_dataset.overall_index_day_sections_table",
        )

    def test_three_part_config_is_composed_per_metric(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE": (
                "volume_index_day_sections_table"
            ),
        }
        self.assertEqual(
            day_section_table_fqn("volume_index", env),
            "your-gcp-project.your_dataset.volume_index_day_sections_table",
        )

    def test_does_not_fall_back_to_page1_env_vars(self):
        """A Page 1 var alone must not silently resolve a Page 2 table."""
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_EXCLUSIVITY_INDEX_TABLE": "exclusivity_index_table",
        }
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("exclusivity_index", env)

    def test_missing_config_names_the_metric_specific_var(self):
        env = {"BIGQUERY_PROJECT_ID": "your-gcp-project", "BIGQUERY_DATASET": "your_dataset"}
        with self.assertRaisesRegex(
            BigQueryConfigError, "BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE"
        ):
            day_section_table_fqn("overall_index", env)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("made_up_index", {})


class DaySectionQueryConstructionTests(unittest.TestCase):
    fqn = "your-gcp-project.your_dataset.volume_index_day_sections_table"

    def test_query_is_single_step_not_two_step(self):
        """No per-pair duplication on the live day-section tables (verified:
        max repeat of (h3_id, segment, hour_bucket) is 1), so unlike Page 1
        this must NOT use a per-pair CTE."""
        sql, params = build_day_section_index_query(
            self.fqn, "volume_index", ["Families", "HNWI"], "Morning"
        )
        self.assertNotIn("per_pair", sql)
        self.assertIn("AVG(volume_index) AS volume_index", sql)
        self.assertIn("GROUP BY h3_id", sql)
        self.assertNotIn("GROUP BY h3_id, segment", sql)
        self.assertIn("@segments", sql)
        self.assertIn("@hour_bucket", sql)
        self.assertEqual(sql.count(f"`{self.fqn}`"), 1)
        self.assertEqual(
            params, {"segments": ["Families", "HNWI"], "hour_bucket": "Morning"}
        )

    def test_every_metric_uses_the_single_step_shape(self):
        from h3_analysis.data import PAGE2_METRICS

        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                sql, _ = build_day_section_index_query(
                    self.fqn, metric, ["Families"], "Noon"
                )
                self.assertIn(f"AVG({metric}) AS {metric}", sql)
                self.assertNotIn("per_pair", sql)

    def test_segment_and_day_part_values_never_appear_in_sql_text(self):
        sql, _ = build_day_section_index_query(
            self.fqn, "overall_index", ["Fam'ilies", "HN;WI"], "Morning'; --"
        )
        self.assertNotIn("Fam'ilies", sql)
        self.assertNotIn("HN;WI", sql)
        self.assertNotIn("Morning'; --", sql)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(self.fqn, "drop_table", ["Families"], "Noon")

    def test_empty_segments_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(self.fqn, "volume_index", [], "Noon")

    def test_empty_hour_bucket_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(self.fqn, "volume_index", ["Families"], "")

    def test_page2_has_no_two_hour_period_filter(self):
        """Page 2 slices by day-part only; the two-hour slicer is Page 1 only."""
        sql, params = build_day_section_index_query(
            self.fqn, "overall_index", ["Families"], "Morning"
        )
        self.assertNotIn("two_hour_period", sql)
        self.assertNotIn("two_hour_period", params)
        self.assertEqual(set(params), {"segments", "hour_bucket"})

    def test_day_parts_query_selects_only_hour_bucket(self):
        sql = build_day_parts_query(self.fqn)
        self.assertIn("SELECT DISTINCT hour_bucket", sql)
        self.assertNotIn("segment", sql)


class AggregatedValidationTests(unittest.TestCase):
    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)

    def test_valid_frame_passes_through(self):
        frame = pd.DataFrame({"h3_id": [self.cell], "overall_index": [12.5]})
        result = validate_aggregated_cells(frame, "overall_index")
        self.assertEqual(result.removed_rows, 0)
        self.assertEqual(len(result.data), 1)

    def test_drops_invalid_h3_and_negative_values(self):
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, "nope", self.cell],
                "volume_index": [0.1, 0.2, -5.0],
            }
        )
        result = validate_aggregated_cells(frame, "volume_index")
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.removed_rows, 2)

    def test_missing_column_reported(self):
        with self.assertRaisesRegex(DataValidationError, "exclusivity_index"):
            validate_aggregated_cells(
                pd.DataFrame({"h3_id": [self.cell]}), "exclusivity_index"
            )

    def test_empty_result_is_allowed(self):
        frame = pd.DataFrame({"h3_id": [], "overall_index": []})
        result = validate_aggregated_cells(frame, "overall_index")
        self.assertTrue(result.data.empty)


class ServiceAccountSecretTests(unittest.TestCase):
    """Streamlit Community Cloud runs outside Google Cloud, so Application
    Default Credentials fail there against an unreachable
    metadata.google.internal. A ``[gcp_service_account]`` secret is the
    supported path; absent one, ADC still applies locally and on Cloud Run."""

    def sample(self, **overrides):
        info = {
            "type": "service_account",
            "project_id": "your-gcp-project",
            "private_key_id": "0123456789abcdef",
            "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
            "client_email": "reader@your-gcp-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        info.update(overrides)
        return {SERVICE_ACCOUNT_SECRET_KEY: info}

    def test_no_secrets_means_application_default_credentials(self):
        self.assertIsNone(service_account_info({}))
        self.assertIsNone(service_account_info(None))
        self.assertIsNone(service_account_info({"other": "value"}))
        self.assertEqual(credentials_source({}), "Application Default Credentials")

    def test_complete_secret_is_returned(self):
        info = service_account_info(self.sample())
        self.assertEqual(
            info["client_email"],
            "reader@your-gcp-project.iam.gserviceaccount.com",
        )
        for field in REQUIRED_SERVICE_ACCOUNT_FIELDS:
            self.assertIn(field, info)
        self.assertIn(SERVICE_ACCOUNT_SECRET_KEY, credentials_source(self.sample()))

    def test_missing_fields_are_named(self):
        secrets = self.sample()
        del secrets[SERVICE_ACCOUNT_SECRET_KEY]["private_key"]
        secrets[SERVICE_ACCOUNT_SECRET_KEY]["client_email"] = "   "
        with self.assertRaises(BigQueryCredentialsError) as caught:
            service_account_info(secrets)
        message = str(caught.exception)
        self.assertIn("private_key", message)
        self.assertIn("client_email", message)

    def test_credentials_error_is_a_config_error(self):
        """The pages catch it separately, but any existing
        BigQueryConfigError handler must still see it."""
        self.assertTrue(
            issubclass(BigQueryCredentialsError, BigQueryConfigError)
        )

    def test_non_table_secret_rejected(self):
        with self.assertRaises(BigQueryCredentialsError):
            service_account_info({SERVICE_ACCOUNT_SECRET_KEY: "paste-the-json"})

    def test_escaped_newlines_in_the_private_key_are_restored(self):
        """A key pasted on one line carries literal backslash-n, which
        google-auth cannot parse."""
        one_line = (
            "-----BEGIN PRIVATE KEY-----\\nfake\\n-----END PRIVATE KEY-----\\n"
        )
        info = service_account_info(self.sample(private_key=one_line))
        self.assertNotIn("\\n", info["private_key"])
        self.assertEqual(info["private_key"].count("\n"), 3)

    def test_real_newlines_are_left_alone(self):
        info = service_account_info(self.sample())
        self.assertEqual(
            info["private_key"],
            "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        )


if __name__ == "__main__":
    unittest.main()
