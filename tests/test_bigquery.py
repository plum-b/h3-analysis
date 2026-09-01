import unittest

import h3
import pandas as pd

from h3_analysis.bigquery_source import (
    BigQueryConfigError,
    billing_project,
    build_day_parts_query,
    build_day_section_index_query,
    build_index_query,
    build_segments_query,
    build_two_hour_periods_query,
    coerce_two_hour_period,
    day_section_table_fqn,
    index_table_fqn,
)
from h3_analysis.data import DataValidationError, validate_aggregated_cells


class TableFqnTests(unittest.TestCase):
    def test_per_metric_fqn_env_wins(self):
        env = {
            "BIGQUERY_OVERALL_INDEX_TABLE_FQN": "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered",
            "BIGQUERY_PROJECT_ID": "ignored",
        }
        self.assertEqual(
            index_table_fqn("overall_index", env),
            "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered",
        )

    def test_three_part_config_is_composed_per_metric(self):
        env = {
            "BIGQUERY_PROJECT_ID": "maddictdata",
            "BIGQUERY_DATASET": "OOH_Analysis",
            "BIGQUERY_VOLUME_INDEX_TABLE": "h3_analysis_volume_index_filtered",
            "BIGQUERY_EXCLUSIVITY_INDEX_TABLE": "h3_analysis_exclusivity_index_filtered",
        }
        self.assertEqual(
            index_table_fqn("volume_index", env),
            "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered",
        )
        self.assertEqual(
            index_table_fqn("exclusivity_index", env),
            "maddictdata.OOH_Analysis.h3_analysis_exclusivity_index_filtered",
        )

    def test_missing_config_names_the_metric_specific_var(self):
        env = {
            "BIGQUERY_PROJECT_ID": "maddictdata",
            "BIGQUERY_DATASET": "OOH_Analysis",
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
            "BIGQUERY_PROJECT_ID": "maddictdata",
            "BIGQUERY_DATASET": "OOH_Analysis",
            "BIGQUERY_VOLUME_INDEX_TABLE": "t`; DROP TABLE x; --",
        }
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn("volume_index", env)


class QueryConstructionTests(unittest.TestCase):
    fqn = "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered"

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
            billing_project({"BIGQUERY_PROJECT_ID": "maddictdata"}), "maddictdata"
        )

    def test_explicit_billing_project_wins(self):
        env = {
            "BIGQUERY_PROJECT_ID": "maddictdata",
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
                "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered_day_sections"
            ),
            "BIGQUERY_PROJECT_ID": "ignored",
        }
        self.assertEqual(
            day_section_table_fqn("overall_index", env),
            "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered_day_sections",
        )

    def test_three_part_config_is_composed_per_metric(self):
        env = {
            "BIGQUERY_PROJECT_ID": "maddictdata",
            "BIGQUERY_DATASET": "OOH_Analysis",
            "BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE": (
                "h3_analysis_volume_index_filtered_day_sections"
            ),
        }
        self.assertEqual(
            day_section_table_fqn("volume_index", env),
            "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered_day_sections",
        )

    def test_does_not_fall_back_to_page1_env_vars(self):
        """A Page 1 var alone must not silently resolve a Page 2 table."""
        env = {
            "BIGQUERY_PROJECT_ID": "maddictdata",
            "BIGQUERY_DATASET": "OOH_Analysis",
            "BIGQUERY_EXCLUSIVITY_INDEX_TABLE": "h3_analysis_exclusivity_index_filtered",
        }
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("exclusivity_index", env)

    def test_missing_config_names_the_metric_specific_var(self):
        env = {"BIGQUERY_PROJECT_ID": "maddictdata", "BIGQUERY_DATASET": "OOH_Analysis"}
        with self.assertRaisesRegex(
            BigQueryConfigError, "BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE"
        ):
            day_section_table_fqn("overall_index", env)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("made_up_index", {})


class DaySectionQueryConstructionTests(unittest.TestCase):
    fqn = "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered_day_sections"

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


if __name__ == "__main__":
    unittest.main()
