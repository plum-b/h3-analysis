import unittest

import h3
import numpy as np
import pandas as pd

from h3_analysis.colors import (
    METRIC_LABELS,
    METRIC_RAMPS,
    METRIC_SCALES,
    ORANGE_RAMP,
    colors_for,
    format_value,
    hex_to_rgb,
    legend_html,
    normalize,
    ramp_for,
)
from h3_analysis.data import (
    ALL_INDEX_METRICS,
    INDEX_METRICS,
    PAGE2_METRICS,
    DataValidationError,
    aggregate_day_section_cells,
    aggregate_index_cells,
    aggregate_two_hour_index_cells,
    collapse_day_section_duplicates,
    collapse_index_duplicates,
    collapse_two_hour_duplicates,
    validate_day_section_data,
    validate_index_data,
    validate_two_hour_index_data,
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
    def test_every_metric_shares_the_orange_ramp(self):
        """One hue for all three metrics, on both pages.

        Orange reads best against the light detailed basemap, so hue is held
        constant and carries no meaning; scale and legend keep the metrics
        distinguishable.
        """
        for metric in ALL_INDEX_METRICS:
            with self.subTest(metric=metric):
                self.assertIs(METRIC_RAMPS[metric], ORANGE_RAMP)
        self.assertEqual(
            len({METRIC_RAMPS[metric] for metric in ALL_INDEX_METRICS}), 1
        )

    def test_every_ramp_step_is_actually_orange(self):
        """Guards against a stray blue/teal step being reintroduced."""
        for step in ORANGE_RAMP:
            with self.subTest(step=step):
                red, green, blue = hex_to_rgb(step)
                self.assertGreater(red, green)
                self.assertGreaterEqual(green, blue)

    def test_ramp_keeps_contrast_against_a_light_basemap(self):
        """The dark end must stay dark enough to read over pale map tiles."""
        light_end = sum(hex_to_rgb(ORANGE_RAMP[0]))
        dark_end = sum(hex_to_rgb(ORANGE_RAMP[-1]))
        self.assertLess(dark_end, light_end / 2)

    def test_ramp_lightness_is_monotonic(self):
        totals = [sum(hex_to_rgb(step)) for step in ORANGE_RAMP]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_shared_hue_does_not_merge_the_metric_scales(self):
        """A shared ramp must not imply a shared range or scale."""
        self.assertEqual(METRIC_SCALES["overall_index"], "linear")
        self.assertEqual(METRIC_SCALES["exclusivity_index"], "linear")
        self.assertEqual(METRIC_SCALES["volume_index"], "log")

        values = pd.Series([1e-4, 1e-3, 1e-2, 1e-1, 1.0])
        linear = colors_for(values, "overall_index", dark_basemap=False)
        same_scale = colors_for(values, "exclusivity_index", dark_basemap=False)
        logarithmic = colors_for(values, "volume_index", dark_basemap=False)
        # Same ramp and same scale -> identical colors.
        self.assertEqual(linear, same_scale)
        # Same ramp, different scale -> the value-to-color mapping still differs.
        self.assertNotEqual(linear, logarithmic)

    def test_dark_basemap_reverses_the_ramp(self):
        for metric in ALL_INDEX_METRICS:
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

    def test_legend_names_the_metric_and_its_scale_for_every_metric(self):
        """With one shared hue, the legend is what tells the metrics apart."""
        for metric in ALL_INDEX_METRICS:
            with self.subTest(metric=metric):
                html = legend_html(metric, 0.1, 0.9, dark_basemap=False)
                self.assertIn(METRIC_LABELS[metric], html)
                self.assertIn(f"({METRIC_SCALES[metric]} scale)", html)
                self.assertIn(ORANGE_RAMP[0], html)
                self.assertIn(ORANGE_RAMP[-1], html)


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

    def test_no_map_3_or_hourly_page_or_committed_export(self):
        """The removed maps are gone from the project.

        The check is that nothing is committed and no page reads them - not
        that the directories are absent from disk. A developer may keep the old
        exports locally; .gitignore is what keeps them out of the repository.
        """
        import os
        import subprocess

        root = os.path.dirname(os.path.dirname(__file__))
        self.assertFalse(
            os.path.exists(os.path.join(root, "pages", "3_Overall_Analysis.py"))
        )

        tracked = subprocess.run(
            ["git", "ls-files", "data/map_1", "data/map_3"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(tracked.stdout.strip(), "", "map_1/map_3 must stay untracked")

        for directory in ("data/map_1", "data/map_3"):
            for name in ("*.csv",):
                ignored = subprocess.run(
                    ["git", "check-ignore", os.path.join(directory, name)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    ignored.returncode,
                    0,
                    f"{directory}/{name} must be gitignored",
                )


class TwoHourIndexTests(unittest.TestCase):
    """Page 1 local-CSV path: validate, collapse and aggregate by two-hour period."""

    def setUp(self):
        self.cell_a = h3.latlng_to_cell(24.45, 54.38, 9)
        self.cell_b = h3.latlng_to_cell(25.20, 55.27, 9)
        self.frame = pd.DataFrame(
            {
                "h3_id": [self.cell_a] * 4 + [self.cell_b] * 2,
                "segment": [
                    "Families",
                    "Families",
                    "HNWI",
                    "HNWI",
                    "Families",
                    "HNWI",
                ],
                "hour_bucket": [8, 10, 8, 10, 8, 8],
                "overall_index": [10.0, 50.0, 20.0, 60.0, 5.0, 15.0],
            }
        )

    def test_requires_the_hour_bucket_column(self):
        with self.assertRaisesRegex(DataValidationError, "hour_bucket"):
            validate_two_hour_index_data(
                self.frame.drop(columns=["hour_bucket"]), "overall_index"
            )

    def test_drops_rows_with_a_missing_or_fractional_period(self):
        frame = self.frame.copy()
        frame.loc[0, "hour_bucket"] = None
        frame.loc[1, "hour_bucket"] = 9.5
        result = validate_two_hour_index_data(frame, "overall_index")
        self.assertEqual(result.removed_rows, 2)
        self.assertEqual(len(result.data), 4)

    def test_period_survives_validation_as_an_integer(self):
        result = validate_two_hour_index_data(self.frame, "overall_index")
        self.assertEqual(result.removed_rows, 0)
        self.assertEqual(
            sorted(result.data["hour_bucket"].unique().tolist()), [8, 10]
        )
        self.assertTrue(
            all(isinstance(value, (int, np.integer))
                for value in result.data["hour_bucket"])
        )

    def test_filters_to_one_period_then_averages_across_segments(self):
        collapsed = collapse_two_hour_duplicates(self.frame, "overall_index")
        result = aggregate_two_hour_index_cells(
            collapsed, ["Families", "HNWI"], 8, "overall_index"
        )
        values = dict(zip(result["h3_id"], result["overall_index"]))
        # Cell A at 08:00 is mean(10, 20) - the 10:00 rows must not leak in.
        self.assertAlmostEqual(values[self.cell_a], 15.0)
        self.assertAlmostEqual(values[self.cell_b], 10.0)

    def test_a_different_period_gives_different_values(self):
        collapsed = collapse_two_hour_duplicates(self.frame, "overall_index")
        result = aggregate_two_hour_index_cells(
            collapsed, ["Families", "HNWI"], 10, "overall_index"
        )
        values = dict(zip(result["h3_id"], result["overall_index"]))
        self.assertAlmostEqual(values[self.cell_a], 55.0)
        # Cell B has no 10:00 row at all, so it drops off the map.
        self.assertNotIn(self.cell_b, values)

    def test_each_segment_weighs_the_same_regardless_of_row_count(self):
        """The per-group step is what stops a row-heavy segment dominating."""
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell_a] * 4,
                "segment": ["Families", "Families", "Families", "HNWI"],
                "hour_bucket": [8, 8, 8, 8],
                "overall_index": [10.0, 10.0, 10.0, 50.0],
            }
        )
        collapsed = collapse_two_hour_duplicates(frame, "overall_index")
        result = aggregate_two_hour_index_cells(
            collapsed, ["Families", "HNWI"], 8, "overall_index"
        )
        # mean(10, 50) = 30, not the row-weighted mean(10,10,10,50) = 20.
        self.assertAlmostEqual(result["overall_index"].iloc[0], 30.0)

    def test_collapse_averages_repeated_triples_never_sums(self):
        frame = pd.DataFrame(
            {
                "h3_id": [self.cell_a, self.cell_a],
                "segment": ["Families", "Families"],
                "hour_bucket": [8, 8],
                "volume_index": [0.2, 0.4],
            }
        )
        collapsed = collapse_two_hour_duplicates(frame, "volume_index")
        self.assertEqual(len(collapsed), 1)
        self.assertAlmostEqual(collapsed["volume_index"].iloc[0], 0.3)


class TwoHourSampleFileTests(unittest.TestCase):
    """The committed Page 1 fallback must satisfy the contract it feeds."""

    def test_sample_covers_twelve_periods_of_valid_cells(self):
        frame = pd.read_csv("data/sample_index_two_hours.csv")
        self.assertEqual(
            sorted(frame["hour_bucket"].unique().tolist()),
            [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22],
        )
        for metric in ("overall_index", "volume_index", "exclusivity_index"):
            with self.subTest(metric=metric):
                result = validate_two_hour_index_data(frame, metric)
                self.assertEqual(result.removed_rows, 0)
        resolutions = {h3.get_resolution(cell) for cell in frame["h3_id"].unique()}
        self.assertEqual(resolutions, {9})
        self.assertEqual(
            frame.groupby(["h3_id", "segment", "hour_bucket"]).size().max(), 1
        )


class MapHeightTests(unittest.TestCase):
    """Both pages render through the one shared helper, so height is set once."""

    def test_map_height_is_at_least_double_the_streamlit_default(self):
        from h3_analysis.mapping import MAP_HEIGHT, STREAMLIT_DEFAULT_MAP_HEIGHT

        self.assertGreaterEqual(MAP_HEIGHT, STREAMLIT_DEFAULT_MAP_HEIGHT * 2)

    def test_renderer_sets_the_height_and_leaves_width_stretching(self):
        from unittest.mock import patch

        from h3_analysis import mapping

        frame = pd.DataFrame({"h3_id": [], "value": []})
        with patch.object(mapping.st, "pydeck_chart") as chart:
            mapping.render_h3_map(frame, "[0, 0, 0]", "", "dark")

        self.assertEqual(chart.call_count, 1)
        kwargs = chart.call_args.kwargs
        self.assertEqual(kwargs["height"], mapping.MAP_HEIGHT)
        # Width must keep filling the column - only the height changed.
        self.assertEqual(kwargs["width"], "stretch")

    def test_both_pages_render_through_the_shared_helper(self):
        """A per-page copy of the renderer would silently skip the new height."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for page in (
            "pages/1_Two-Hour_Index_Analysis.py",
            "pages/2_Day-Part_Index_Analysis.py",
        ):
            with self.subTest(page=page):
                source = (root / page).read_text(encoding="utf-8")
                self.assertIn("render_h3_map", source)
                self.assertNotIn("st.pydeck_chart", source)


if __name__ == "__main__":
    unittest.main()
