import os
import tempfile
import unittest
from pathlib import Path

from h3_analysis.bigquery_source import day_section_table_fqn, index_table_fqn
from h3_analysis.config import load_local_env


class LocalEnvLoadingTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def write(self, text: str) -> Path:
        path = Path(self.tmp.name) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_is_not_an_error(self):
        path = Path(self.tmp.name) / "absent.env"
        self.assertEqual(load_local_env(path), {})

    def test_loads_pairs_and_skips_comments_and_blanks(self):
        path = self.write(
            "# a comment\n\nBIGQUERY_PROJECT_ID=maddictdata\n"
            "BIGQUERY_DATASET = OOH_Analysis \nnot a pair\n"
        )
        os.environ.pop("BIGQUERY_PROJECT_ID", None)
        os.environ.pop("BIGQUERY_DATASET", None)
        applied = load_local_env(path)
        self.assertEqual(applied["BIGQUERY_PROJECT_ID"], "maddictdata")
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "OOH_Analysis")

    def test_strips_surrounding_quotes(self):
        path = self.write('BIGQUERY_DATASET="OOH_Analysis"\n')
        os.environ.pop("BIGQUERY_DATASET", None)
        load_local_env(path)
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "OOH_Analysis")

    def test_existing_environment_wins(self):
        path = self.write("BIGQUERY_DATASET=from_file\n")
        os.environ["BIGQUERY_DATASET"] = "from_shell"
        applied = load_local_env(path)
        self.assertNotIn("BIGQUERY_DATASET", applied)
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "from_shell")


class ShippedExampleConfigTests(unittest.TestCase):
    """The committed .env.example must resolve to the agreed tables."""

    EXPECTED = {
        "overall_index": "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered",
        "volume_index": "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered",
        "exclusivity_index": (
            "maddictdata.OOH_Analysis.h3_analysis_exclusivity_index_filtered"
        ),
    }

    EXPECTED_DAY_SECTIONS = {
        "overall_index": (
            "maddictdata.OOH_Analysis.h3_analysis_indexed_filtered_day_sections"
        ),
        "volume_index": (
            "maddictdata.OOH_Analysis.h3_analysis_volume_index_filtered_day_sections"
        ),
        "exclusivity_index": (
            "maddictdata.OOH_Analysis."
            "h3_analysis_exclusivity_index_filtered_day_sections"
        ),
    }

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.example = root / ".env.example"

    def test_example_exists(self):
        self.assertTrue(self.example.exists(), ".env.example must be committed")

    def _env_from_example(self) -> dict:
        env = {}
        for line in self.example.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("'\"")
        return env

    def test_example_resolves_every_metric_table(self):
        env = self._env_from_example()
        for metric, expected in self.EXPECTED.items():
            with self.subTest(metric=metric):
                self.assertEqual(index_table_fqn(metric, env), expected)

    def test_example_resolves_every_day_section_table(self):
        env = self._env_from_example()
        for metric, expected in self.EXPECTED_DAY_SECTIONS.items():
            with self.subTest(metric=metric):
                self.assertEqual(day_section_table_fqn(metric, env), expected)

    def test_example_carries_no_credentials(self):
        text = self.example.read_text(encoding="utf-8").lower()
        for forbidden in ("private_key", "client_secret", "password", "api_key"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
