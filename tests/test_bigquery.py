import unittest

import h3
import pandas as pd

from h3_analysis.bigquery_source import (
    BigQueryConfigError,
    BigQueryCredentialsError,
    REQUIRED_SERVICE_ACCOUNT_FIELDS,
    SERVICE_ACCOUNT_SECRET_KEY,
    WEEK_PART_COLUMN,
    billing_project,
    credentials_source,
    build_day_parts_query,
    build_day_section_index_query,
    build_index_query,
    build_segments_query,
    build_two_hour_periods_query,
    build_week_parts_query,
    coerce_two_hour_period,
    day_section_table_fqn,
    index_table_fqn,
    service_account_info,
)
from h3_analysis.data import (
    PAGE1_METRICS,
    PAGE2_METRICS,
    DataValidationError,
    validate_aggregated_cells,
)


def _page_source(relative_path: str) -> str:
    """Read a repository file for the "is it really gone" source checks."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / relative_path).read_text(encoding="utf-8")


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
            "BIGQUERY_OVERALL_INDEX_TABLE": "overall_index_table",
        }
        self.assertEqual(
            index_table_fqn("volume_index", env),
            "your-gcp-project.your_dataset.volume_index_table",
        )
        self.assertEqual(
            index_table_fqn("overall_index", env),
            "your-gcp-project.your_dataset.overall_index_table",
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
            "BIGQUERY_VOLUME_INDEX_TABLE": "volume_index_table",
        }
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("volume_index", env)

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
        """No per-row duplication on the live day-section tables (verified:
        max repeat of (h3_id, segment, hour_bucket, Week_part) is 1), so unlike
        Page 1 this must NOT use a per-pair CTE."""
        sql, params = build_day_section_index_query(
            self.fqn, "volume_index", ["Families", "HNWI"], "Morning", "Weekday"
        )
        self.assertNotIn("per_pair", sql)
        self.assertIn("AVG(volume_index) AS volume_index", sql)
        self.assertIn("GROUP BY h3_id", sql)
        self.assertNotIn("GROUP BY h3_id, segment", sql)
        self.assertIn("@segments", sql)
        self.assertIn("@hour_bucket", sql)
        self.assertEqual(sql.count(f"`{self.fqn}`"), 1)
        self.assertEqual(
            params,
            {
                "segments": ["Families", "HNWI"],
                "hour_bucket": "Morning",
                "week_part": "Weekday",
            },
        )

    def test_week_part_is_a_bound_parameter_not_sql_text(self):
        """The Weekday/Weekend radio value reaches BigQuery as @week_part."""
        sql, params = build_day_section_index_query(
            self.fqn, "overall_index", ["Families"], "Morning", "Weekend"
        )
        self.assertIn(f"AND {WEEK_PART_COLUMN} = @week_part", sql)
        self.assertNotIn("Weekend", sql)
        self.assertEqual(params["week_part"], "Weekend")

    def test_day_part_and_week_part_filter_together(self):
        """Both filters must survive in the same WHERE clause - dropping
        either would average the other dimension's slices back together."""
        sql, params = build_day_section_index_query(
            self.fqn, "overall_index", ["HNWI"], "Night", "Weekend"
        )
        where = sql.split("WHERE", 1)[1]
        self.assertIn("segment IN UNNEST(@segments)", where)
        self.assertIn("hour_bucket = @hour_bucket", where)
        self.assertIn(f"{WEEK_PART_COLUMN} = @week_part", where)
        self.assertEqual(
            params,
            {
                "segments": ["HNWI"],
                "hour_bucket": "Night",
                "week_part": "Weekend",
            },
        )

    def test_week_part_applies_to_every_remaining_metric(self):
        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                sql, params = build_day_section_index_query(
                    self.fqn, metric, ["Families"], "Noon", "Weekday"
                )
                self.assertIn(f"AVG({metric}) AS {metric}", sql)
                self.assertIn(f"AND {WEEK_PART_COLUMN} = @week_part", sql)
                self.assertEqual(params["week_part"], "Weekday")

    def test_every_metric_uses_the_single_step_shape(self):
        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                sql, _ = build_day_section_index_query(
                    self.fqn, metric, ["Families"], "Noon", "Weekday"
                )
                self.assertIn(f"AVG({metric}) AS {metric}", sql)
                self.assertNotIn("per_pair", sql)

    def test_one_segment_is_still_bound_as_an_array_parameter(self):
        """The sidebar now selects a single segment; it must still travel as
        the @segments array rather than being spliced into the SQL."""
        sql, params = build_day_section_index_query(
            self.fqn, "volume_index", ["Potential Car Buyers"], "Noon", "Weekday"
        )
        self.assertIn("segment IN UNNEST(@segments)", sql)
        self.assertNotIn("Potential Car Buyers", sql)
        self.assertEqual(params["segments"], ["Potential Car Buyers"])

    def test_segment_and_day_part_values_never_appear_in_sql_text(self):
        sql, _ = build_day_section_index_query(
            self.fqn,
            "overall_index",
            ["Fam'ilies", "HN;WI"],
            "Morning'; --",
            "Weekend'; DROP TABLE",
        )
        self.assertNotIn("Fam'ilies", sql)
        self.assertNotIn("HN;WI", sql)
        self.assertNotIn("Morning'; --", sql)
        self.assertNotIn("DROP TABLE", sql)

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(
                self.fqn, "drop_table", ["Families"], "Noon", "Weekday"
            )

    def test_empty_segments_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(
                self.fqn, "volume_index", [], "Noon", "Weekday"
            )

    def test_empty_hour_bucket_rejected(self):
        with self.assertRaises(ValueError):
            build_day_section_index_query(
                self.fqn, "volume_index", ["Families"], "", "Weekday"
            )

    def test_empty_week_part_rejected(self):
        """Without a week-part the query would blend Weekday and Weekend."""
        with self.assertRaises(ValueError):
            build_day_section_index_query(
                self.fqn, "volume_index", ["Families"], "Noon", ""
            )

    def test_page2_has_no_two_hour_period_filter(self):
        """Page 2 slices by day-part and week-part; the two-hour slicer is
        Page 1 only."""
        sql, params = build_day_section_index_query(
            self.fqn, "overall_index", ["Families"], "Morning", "Weekday"
        )
        self.assertNotIn("two_hour_period", sql)
        self.assertNotIn("two_hour_period", params)
        self.assertEqual(set(params), {"segments", "hour_bucket", "week_part"})

    def test_day_parts_query_selects_only_hour_bucket(self):
        sql = build_day_parts_query(self.fqn)
        self.assertIn("SELECT DISTINCT hour_bucket", sql)
        self.assertNotIn("segment", sql)

    def test_week_parts_query_reads_the_domain_from_the_table(self):
        """Weekday/Weekend is never hard-coded; the selector offers whatever
        the table holds."""
        sql = build_week_parts_query(self.fqn)
        self.assertIn(f"SELECT DISTINCT {WEEK_PART_COLUMN}", sql)
        self.assertIn(f"`{self.fqn}`", sql)
        self.assertNotIn("Weekday", sql)
        self.assertNotIn("Weekend", sql)


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


class Page1HasNoWeekPartTests(unittest.TestCase):
    """Week_part exists only on the day-section tables, so neither Page 1's
    query nor its page may grow a Weekday/Weekend filter."""

    fqn = "your-gcp-project.your_dataset.overall_index_table"

    def test_two_hour_query_has_no_week_part_filter(self):
        sql, params = build_index_query(self.fqn, "overall_index", ["Families"], 8)
        self.assertNotIn(WEEK_PART_COLUMN, sql)
        self.assertNotIn("week_part", params)

    def test_page1_source_has_no_week_part_selector(self):
        source = _page_source("pages/1_Two-Hour_Index_Analysis.py")
        self.assertNotIn("build_week_parts_query", source)
        self.assertNotIn("week_part_radio", source)
        self.assertNotIn('"Week part"', source)


class SingleSegmentSelectorTests(unittest.TestCase):
    """The sidebar selects exactly one segment, with radio buttons and no
    "Select all"."""

    def test_selector_is_a_radio_returning_one_segment(self):
        from unittest.mock import MagicMock, patch

        from h3_analysis import mapping

        with patch.object(mapping, "st") as fake:
            fake.sidebar = MagicMock()
            fake.sidebar.radio.return_value = "HNWI"
            selected = mapping.segment_radio(["Families", "HNWI"])

        self.assertEqual(selected, "HNWI")
        self.assertEqual(fake.sidebar.radio.call_count, 1)
        self.assertEqual(
            fake.sidebar.radio.call_args.args[1], ["Families", "HNWI"]
        )
        fake.sidebar.checkbox.assert_not_called()

    def test_no_checkbox_selector_survives(self):
        from h3_analysis import mapping

        self.assertFalse(hasattr(mapping, "segment_checkboxes"))

    def test_both_pages_use_the_shared_radio_and_offer_no_select_all(self):
        for page in (
            "pages/1_Two-Hour_Index_Analysis.py",
            "pages/2_Day-Part_Index_Analysis.py",
        ):
            with self.subTest(page=page):
                source = _page_source(page)
                self.assertIn("segment_radio(", source)
                self.assertNotIn("segment_checkboxes", source)
                self.assertNotIn("Select all", source)


class ExclusivityRemovalTests(unittest.TestCase):
    """The exclusivity index is gone from the application: no selector, no
    label, no legend, no table, no query."""

    def test_not_a_metric_on_either_page(self):
        self.assertNotIn("exclusivity_index", PAGE1_METRICS)
        self.assertNotIn("exclusivity_index", PAGE2_METRICS)

    def test_has_no_label_help_ramp_or_scale(self):
        from h3_analysis import colors

        for name in ("METRIC_LABELS", "METRIC_HELP", "METRIC_RAMPS", "METRIC_SCALES"):
            with self.subTest(registry=name):
                self.assertNotIn("exclusivity_index", getattr(colors, name))

    def test_no_table_resolves_even_with_the_old_env_vars_set(self):
        """A leftover BIGQUERY_EXCLUSIVITY_* variable must not resurrect it."""
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_EXCLUSIVITY_INDEX_TABLE": "exclusivity_index_table",
            "BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE": (
                "exclusivity_index_day_sections_table"
            ),
        }
        with self.assertRaises(BigQueryConfigError):
            index_table_fqn("exclusivity_index", env)
        with self.assertRaises(BigQueryConfigError):
            day_section_table_fqn("exclusivity_index", env)

    def test_no_query_can_be_built_for_it(self):
        table = "your-gcp-project.your_dataset.t"
        with self.assertRaises(ValueError):
            build_index_query(table, "exclusivity_index", ["Families"], 8)
        with self.assertRaises(ValueError):
            build_day_section_index_query(
                table, "exclusivity_index", ["Families"], "Noon", "Weekday"
            )

    def test_absent_from_the_app(self):
        """The metric is gone. The *label* "Exclusivity index" is a separate
        matter - it is the display name of ``overall_index`` (see
        MetricLabelTests), which is why this looks for the metric key."""
        for name in (
            "app.py",
            "pages/1_Two-Hour_Index_Analysis.py",
            "pages/2_Day-Part_Index_Analysis.py",
            "h3_analysis/colors.py",
            "h3_analysis/mapping.py",
            "data/sample_index_day_sections.csv",
        ):
            with self.subTest(file=name):
                self.assertNotIn("exclusivity_index", _page_source(name))

    def test_no_exclusivity_table_is_configured_anywhere(self):
        """Neither the committed template nor the deploy workflow may still
        set an exclusivity table variable."""
        for name in (".env.example", ".github/workflows/deploy-cloud-run.yml"):
            with self.subTest(file=name):
                text = _page_source(name).upper()
                self.assertNotIn("BIGQUERY_EXCLUSIVITY", text)


class MetricLabelTests(unittest.TestCase):
    """Display names are decoupled from the metric keys: ``overall_index`` is
    shown as "Exclusivity index", while its table, column and cache keys keep
    the overall_index name."""

    def test_overall_index_is_labelled_exclusivity_index(self):
        from h3_analysis import colors

        self.assertEqual(colors.METRIC_LABELS["overall_index"], "Exclusivity index")
        self.assertEqual(colors.METRIC_LABELS["volume_index"], "Volume index")

    def test_the_rename_did_not_touch_the_metric_key_or_its_table(self):
        env = {
            "BIGQUERY_PROJECT_ID": "your-gcp-project",
            "BIGQUERY_DATASET": "your_dataset",
            "BIGQUERY_OVERALL_INDEX_TABLE": "overall_index_table",
            "BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE": (
                "overall_index_day_sections_table"
            ),
        }
        self.assertIn("overall_index", PAGE1_METRICS)
        self.assertEqual(
            index_table_fqn("overall_index", env),
            "your-gcp-project.your_dataset.overall_index_table",
        )
        self.assertEqual(
            day_section_table_fqn("overall_index", env),
            "your-gcp-project.your_dataset.overall_index_day_sections_table",
        )
        sql, _ = build_index_query(
            "your-gcp-project.your_dataset.overall_index_table",
            "overall_index",
            ["Families"],
            8,
        )
        self.assertIn("AVG(overall_index) AS overall_index", sql)
        self.assertNotIn("Exclusivity", sql)

    def test_every_label_reaches_the_legend(self):
        from h3_analysis.colors import METRIC_LABELS, legend_html

        for metric in PAGE1_METRICS:
            with self.subTest(metric=metric):
                self.assertIn(
                    METRIC_LABELS[metric],
                    legend_html(metric, 0.1, 0.9, dark_basemap=False),
                )


class DaySectionSampleFileTests(unittest.TestCase):
    """The committed Page 2 fallback must satisfy the contract it feeds."""

    def test_sample_carries_both_week_parts_for_every_day_part(self):
        frame = pd.read_csv("data/sample_index_day_sections.csv")
        self.assertEqual(sorted(frame[WEEK_PART_COLUMN].unique()), ["Weekday", "Weekend"])
        self.assertEqual(
            sorted(frame["hour_bucket"].unique()),
            ["After noon", "Morning", "Night", "Noon", "Other"],
        )
        counts = frame.groupby(["hour_bucket", WEEK_PART_COLUMN]).size().unstack()
        self.assertTrue((counts["Weekday"] == counts["Weekend"]).all())

    def test_sample_validates_for_every_remaining_metric(self):
        from h3_analysis.data import validate_day_section_data

        frame = pd.read_csv("data/sample_index_day_sections.csv")
        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                result = validate_day_section_data(frame, metric)
                self.assertEqual(result.removed_rows, 0)
        resolutions = {h3.get_resolution(cell) for cell in frame["h3_id"].unique()}
        self.assertEqual(resolutions, {9})
        self.assertEqual(
            frame.groupby(
                ["h3_id", "segment", "hour_bucket", WEEK_PART_COLUMN]
            ).size().max(),
            1,
        )


if __name__ == "__main__":
    unittest.main()
