import unittest

import h3
import pandas as pd

from h3_analysis.data import (
    DataValidationError,
    aggregate_cells,
    format_hour_bucket,
    validate_data,
)


class DataValidationTests(unittest.TestCase):
    def setUp(self):
        self.cell = h3.latlng_to_cell(24.45, 54.38, 9)

    def valid_frame(self):
        return pd.DataFrame(
            {
                "h3_id": [self.cell, self.cell],
                "hour_bucket": [8, 8],
                "segment": ["Families", "HNWI"],
                "user_count": [2, 3],
            }
        )

    def test_validates_and_aggregates_duplicate_cells(self):
        result = validate_data(self.valid_frame())
        aggregate = aggregate_cells(result.data, ["Families", "HNWI"], 8)
        self.assertEqual(result.removed_rows, 0)
        self.assertEqual(aggregate.loc[0, "user_count"], 5)

    def test_reports_missing_columns(self):
        with self.assertRaisesRegex(DataValidationError, "segment, user_count"):
            validate_data(pd.DataFrame({"h3_id": [], "hour_bucket": []}))

    def test_removes_invalid_rows(self):
        frame = self.valid_frame()
        frame.loc[0, "h3_id"] = "not-an-h3-cell"
        frame.loc[1, "hour_bucket"] = 9
        with self.assertRaisesRegex(DataValidationError, "no valid data rows"):
            validate_data(frame)

    def test_zero_count_is_valid(self):
        frame = self.valid_frame().iloc[[0]].copy()
        frame["user_count"] = 0
        result = validate_data(frame)
        self.assertEqual(result.data.iloc[0]["user_count"], 0)

    def test_formats_midnight_rollover(self):
        self.assertEqual(format_hour_bucket(22), "22:00-00:00")


if __name__ == "__main__":
    unittest.main()

