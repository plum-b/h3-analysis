import unittest

import h3
import numpy as np
import pandas as pd

from h3_analysis.colors import (
    METRIC_RAMPS,
    colors_for,
    format_value,
    legend_html,
    normalize,
    ramp_for,
)
from h3_analysis.data import (
    INDEX_METRICS,
    PAGE2_METRICS,
    DataValidationError,
    aggregate_day_section_cells,
    aggregate_index_cells,
    collapse_day_section_duplicates,
    collapse_index_duplicates,
    validate_day_section_data,
    validate_index_data,
)


class IndexValidationTests(unittest.TestCase):
    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)
        self.other = h3.latlng_to_cell(25.20, 55.27, 9)

    def frame(self, metric, values=None, segments=None):
        values = [0.5, 0.25] if values is None else values
        segments = ["Families", "Families"] if segments is None else segments
        return pd.DataFrame(
            {
                "h3_id": [self.cell] * len(values),
                metric: values,
                "segment": segments,
            }
        )

    def test_both_metrics_validate(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                result = validate_index_data(self.frame(metric), metric)
                self.assertEqual(result.removed_rows, 0)
                self.assertEqual(len(result.data), 2)
                self.assertIn(metric, result.data.columns)

    def test_reports_missing_metric_column(self):
        frame = self.frame("exclusivity_index")
        with self.assertRaisesRegex(DataValidationError, "volume_index"):
            validate_index_data(frame, "volume_index")

    def test_reports_missing_base_columns(self):
        frame = pd.DataFrame({"exclusivity_index": [0.5]})
        with self.assertRaisesRegex(DataValidationError, "h3_id, segment"):
            validate_index_data(frame, "exclusivity_index")

    def test_rejects_unknown_metric(self):
        with self.assertRaisesRegex(DataValidationError, "Unknown metric"):
            validate_index_data(self.frame("exclusivity_index"), "made_up_index")

    def test_accepts_file_carrying_both_metrics(self):
        frame = self.frame("exclusivity_index")
        frame["volume_index"] = [0.001, 0.002]
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                result = validate_index_data(frame, metric)
                self.assertEqual(list(result.data.columns), ["h3_id", "segment", metric])

    def test_removes_invalid_values(self):
        metric = "exclusivity_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, "not-a-cell", self.cell, self.cell, self.cell],
                metric: [0.5, 0.5, -1.0, np.nan, np.inf],
                "segment": ["Families"] * 5,
            }
        )
        result = validate_index_data(frame, metric)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.removed_rows, 4)

    def test_rejects_blank_segment(self):
        metric = "exclusivity_index"
        frame = self.frame(metric, values=[0.5], segments=[" "])
        with self.assertRaisesRegex(DataValidationError, "no valid"):
            validate_index_data(frame, metric)

    def test_errors_when_every_row_invalid(self):
        metric = "volume_index"
        frame = self.frame(metric, values=[-1.0, np.nan])
        with self.assertRaisesRegex(DataValidationError, "no valid 'volume_index' rows"):
            validate_index_data(frame, metric)


class IndexAggregationTests(unittest.TestCase):
    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)
        self.other = h3.latlng_to_cell(25.20, 55.27, 9)

    def test_collapse_averages_repeated_rows(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                frame = pd.DataFrame(
                    {
                        "h3_id": [self.cell] * 3,
                        "segment": ["Families"] * 3,
                        metric: [0.2, 0.4, 0.6],
                    }
                )
                collapsed = collapse_index_duplicates(frame, metric)
                self.assertEqual(len(collapsed), 1)
                self.assertAlmostEqual(collapsed.loc[0, metric], 0.4)

    def test_collapse_keeps_segments_separate(self):
        metric = "exclusivity_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, self.cell],
                "segment": ["Families", "HNWI"],
                metric: [0.2, 0.8],
            }
        )
        self.assertEqual(len(collapse_index_duplicates(frame, metric)), 2)

    def test_aggregate_averages_across_selected_segments(self):
        metric = "volume_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, self.cell, self.other],
                "segment": ["Families", "HNWI", "Families"],
                metric: [0.2, 0.6, 0.1],
            }
        )
        result = aggregate_index_cells(frame, ["Families", "HNWI"], metric)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(
            result.loc[result["h3_id"] == self.cell, metric].iloc[0], 0.4
        )

    def test_aggregate_respects_segment_selection(self):
        metric = "exclusivity_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, self.other],
                "segment": ["Families", "HNWI"],
                metric: [0.2, 0.9],
            }
        )
        result = aggregate_index_cells(frame, ["Families"], metric)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result.loc[0, metric], 0.2)

    def test_aggregate_returns_empty_for_unmatched_segment(self):
        metric = "volume_index"
        frame = pd.DataFrame(
            {"h3_id": [self.cell], "segment": ["Families"], metric: [0.5]}
        )
        result = aggregate_index_cells(frame, ["Nobody"], metric)
        self.assertTrue(result.empty)


class DaySectionValidationTests(unittest.TestCase):
    """Page 2's day-section schema: h3_id/segment/hour_bucket/<metric>."""

    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)
        self.other = h3.latlng_to_cell(25.20, 55.27, 9)

    def frame(self, metric, values=None, segments=None, buckets=None):
        values = [0.5, 0.25] if values is None else values
        segments = ["Families", "Families"] if segments is None else segments
        buckets = ["Morning", "Noon"] if buckets is None else buckets
        return pd.DataFrame(
            {
                "h3_id": [self.cell] * len(values),
                metric: values,
                "segment": segments,
                "hour_bucket": buckets,
            }
        )

    def test_every_page2_metric_validates(self):
        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                result = validate_day_section_data(self.frame(metric), metric)
                self.assertEqual(result.removed_rows, 0)
                self.assertEqual(len(result.data), 2)
                self.assertIn("hour_bucket", result.data.columns)

    def test_reports_missing_hour_bucket_column(self):
        frame = self.frame("overall_index").drop(columns=["hour_bucket"])
        with self.assertRaisesRegex(DataValidationError, "hour_bucket"):
            validate_day_section_data(frame, "overall_index")

    def test_rejects_blank_hour_bucket(self):
        frame = self.frame(
            "volume_index", values=[0.5], segments=["Families"], buckets=[" "]
        )
        with self.assertRaisesRegex(DataValidationError, "no valid"):
            validate_day_section_data(frame, "volume_index")

    def test_removes_invalid_h3_and_negative_values(self):
        metric = "exclusivity_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, "not-a-cell", self.cell],
                metric: [0.5, 0.5, -1.0],
                "segment": ["Families"] * 3,
                "hour_bucket": ["Morning"] * 3,
            }
        )
        result = validate_day_section_data(frame, metric)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.removed_rows, 2)

    def test_rejects_unknown_metric(self):
        with self.assertRaisesRegex(DataValidationError, "Unknown metric"):
            validate_day_section_data(self.frame("overall_index"), "made_up_index")


class DaySectionAggregationTests(unittest.TestCase):
    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)
        self.other = h3.latlng_to_cell(25.20, 55.27, 9)

    def test_collapse_averages_repeated_triples(self):
        metric = "overall_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell] * 3,
                "segment": ["Families"] * 3,
                "hour_bucket": ["Morning"] * 3,
                metric: [0.2, 0.4, 0.6],
            }
        )
        collapsed = collapse_day_section_duplicates(frame, metric)
        self.assertEqual(len(collapsed), 1)
        self.assertAlmostEqual(collapsed.loc[0, metric], 0.4)

    def test_collapse_keeps_day_parts_separate(self):
        """Same cell and segment, different hour_bucket, must not merge -
        this is what distinguishes the day-section schema from Page 1's."""
        metric = "overall_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, self.cell],
                "segment": ["Families", "Families"],
                "hour_bucket": ["Morning", "Night"],
                metric: [0.2, 0.8],
            }
        )
        self.assertEqual(len(collapse_day_section_duplicates(frame, metric)), 2)

    def test_aggregate_filters_by_day_part_and_averages_segments(self):
        metric = "volume_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell, self.cell, self.cell, self.other],
                "segment": ["Families", "HNWI", "Families", "Families"],
                "hour_bucket": ["Morning", "Morning", "Night", "Morning"],
                metric: [0.2, 0.6, 999.0, 0.1],
            }
        )
        result = aggregate_day_section_cells(
            frame, ["Families", "HNWI"], "Morning", metric
        )
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(
            result.loc[result["h3_id"] == self.cell, metric].iloc[0], 0.4
        )

    def test_aggregate_returns_empty_for_unmatched_day_part(self):
        metric = "exclusivity_index"
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell],
                "segment": ["Families"],
                "hour_bucket": ["Morning"],
                metric: [0.5],
            }
        )
        result = aggregate_day_section_cells(frame, ["Families"], "Night", metric)
        self.assertTrue(result.empty)


class ColorScaleTests(unittest.TestCase):
    def test_each_metric_has_its_own_single_hue_ramp(self):
        ramps = {metric: METRIC_RAMPS[metric] for metric in INDEX_METRICS}
        self.assertEqual(len(set(ramps.values())), len(INDEX_METRICS))

    def test_dark_basemap_reverses_the_ramp(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                light = ramp_for(metric, dark_basemap=False)
                dark = ramp_for(metric, dark_basemap=True)
                self.assertEqual(light, tuple(reversed(dark)))

    def test_normalize_spans_the_full_range(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                values = pd.Series(np.linspace(0.001, 1.0, 100))
                position = normalize(values, metric)
                self.assertAlmostEqual(position.min(), 0.0)
                self.assertAlmostEqual(position.max(), 1.0)
                self.assertTrue(position.between(0.0, 1.0).all())

    def test_log_scale_separates_skewed_volume_values(self):
        # Values spanning four orders of magnitude collapse under a linear scale.
        values = pd.Series([1e-7, 1e-6, 1e-5, 1e-4, 1e-3])
        spread = normalize(values, "volume_index")
        self.assertGreater(spread.iloc[2], 0.3)
        self.assertLess(spread.iloc[2], 0.7)

    def test_constant_values_land_mid_ramp(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                position = normalize(pd.Series([0.5, 0.5, 0.5]), metric)
                self.assertTrue((position == 0.5).all())

    def test_empty_input_produces_no_colors(self):
        for metric in INDEX_METRICS:
            with self.subTest(metric=metric):
                empty = pd.Series([], dtype=float)
                self.assertEqual(normalize(empty, metric).tolist(), [])
                self.assertEqual(colors_for(empty, metric, False), [])

    def test_all_null_values_stay_mid_ramp(self):
        position = normalize(pd.Series([np.nan, np.nan]), "exclusivity_index")
        self.assertTrue((position == 0.5).all())

    def test_colors_are_rgb_triples_for_both_metrics(self):
        values = pd.Series([0.1, 0.5, 0.9])
        for metric in INDEX_METRICS:
            for dark in (True, False):
                with self.subTest(metric=metric, dark=dark):
                    colors = colors_for(values, metric, dark)
                    self.assertEqual(len(colors), 3)
                    for color in colors:
                        self.assertEqual(len(color), 3)
                        self.assertTrue(all(0 <= channel <= 255 for channel in color))

    def test_colors_track_magnitude(self):
        values = pd.Series(np.linspace(0.01, 1.0, 20))
        low, high = colors_for(values, "exclusivity_index", dark_basemap=False)[:: 19]
        # On a light ramp the largest value must be the darker of the two.
        self.assertLess(sum(high), sum(low))

    def test_format_value_matches_the_scale(self):
        self.assertEqual(format_value(0.5, "exclusivity_index"), "0.500")
        self.assertIn("e-", format_value(0.0000074, "volume_index"))
        self.assertEqual(format_value(np.nan, "volume_index"), "n/a")

    def test_legend_reports_both_ends(self):
        html = legend_html("exclusivity_index", 0.1, 0.9, dark_basemap=False)
        self.assertIn("0.100", html)
        self.assertIn("0.900", html)
        self.assertIn("linear-gradient", html)


class ThirdMapRemovalTests(unittest.TestCase):
    """The former CSV 'Overall analysis index' (map_3) map is gone.

    ``overall_index`` still exists, but only as a Page 1 BigQuery metric with
    its own table - never the old ``data/map_3`` CSV / radio-dataset map.
    """

    def test_old_analysis_constants_are_gone(self):
        from h3_analysis import data

        self.assertFalse(hasattr(data, "ANALYSIS_METRICS"))
        self.assertFalse(hasattr(data, "INDEX_LIKE_METRICS"))
        self.assertFalse(hasattr(data, "REQUIRED_COLUMNS"))
        self.assertFalse(hasattr(data, "validate_data"))

    def test_overall_index_is_a_page1_bigquery_metric(self):
        from h3_analysis import colors
        from h3_analysis.data import PAGE1_METRICS

        self.assertIn("overall_index", PAGE1_METRICS)
        self.assertNotIn("overall_index", INDEX_METRICS)  # not a Page 2 CSV metric
        self.assertEqual(colors.METRIC_SCALES["overall_index"], "linear")

    def test_no_map_3_or_hourly_data_dir(self):
        import os

        root = os.path.dirname(os.path.dirname(__file__))
        self.assertFalse(os.path.exists(os.path.join(root, "data", "map_3")))
        self.assertFalse(os.path.exists(os.path.join(root, "data", "map_1")))
        self.assertFalse(
            os.path.exists(os.path.join(root, "pages", "3_Overall_Analysis.py"))
        )


if __name__ == "__main__":
    unittest.main()
