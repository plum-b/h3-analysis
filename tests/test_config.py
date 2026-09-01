import os
import tempfile
import unittest
from pathlib import Path

from h3_analysis.bigquery_source import day_section_table_fqn, index_table_fqn
from h3_analysis.config import load_local_env
from h3_analysis.data import PAGE1_METRICS, PAGE2_METRICS


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
            "# a comment\n\nBIGQUERY_PROJECT_ID=your-gcp-project\n"
            "BIGQUERY_DATASET = your_dataset \nnot a pair\n"
        )
        os.environ.pop("BIGQUERY_PROJECT_ID", None)
        os.environ.pop("BIGQUERY_DATASET", None)
        applied = load_local_env(path)
        self.assertEqual(applied["BIGQUERY_PROJECT_ID"], "your-gcp-project")
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "your_dataset")

    def test_strips_surrounding_quotes(self):
        path = self.write('BIGQUERY_DATASET="your_dataset"\n')
        os.environ.pop("BIGQUERY_DATASET", None)
        load_local_env(path)
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "your_dataset")

    def test_existing_environment_wins(self):
        path = self.write("BIGQUERY_DATASET=from_file\n")
        os.environ["BIGQUERY_DATASET"] = "from_shell"
        applied = load_local_env(path)
        self.assertNotIn("BIGQUERY_DATASET", applied)
        self.assertEqual(os.environ["BIGQUERY_DATASET"], "from_shell")


class ShippedExampleConfigTests(unittest.TestCase):
    """The committed .env.example must resolve a table for every metric.

    The real project/dataset/table names are not committed - the repository is
    public - so these assert the template is *complete and well formed* rather
    than pinning particular identifiers. Hard-coding the expected names here
    would just reintroduce them one file over.
    """

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
        for metric in PAGE1_METRICS:
            with self.subTest(metric=metric):
                fqn = index_table_fqn(metric, env)
                self.assertEqual(len(fqn.split(".")), 3, fqn)
                self.assertTrue(all(fqn.split(".")))

    def test_example_resolves_every_day_section_table(self):
        env = self._env_from_example()
        for metric in PAGE2_METRICS:
            with self.subTest(metric=metric):
                fqn = day_section_table_fqn(metric, env)
                self.assertEqual(len(fqn.split(".")), 3, fqn)
                self.assertTrue(all(fqn.split(".")))

    def test_example_gives_each_metric_and_page_its_own_table(self):
        """Six distinct tables - a copy-paste slip would collide them."""
        env = self._env_from_example()
        fqns = [index_table_fqn(m, env) for m in PAGE1_METRICS]
        fqns += [day_section_table_fqn(m, env) for m in PAGE2_METRICS]
        self.assertEqual(len(set(fqns)), 6)

    def test_example_carries_no_real_identifiers(self):
        """The public template must stay placeholders, not the live names."""
        text = self.example.read_text(encoding="utf-8")
        env = self._env_from_example()
        for key in ("BIGQUERY_PROJECT_ID", "BIGQUERY_DATASET"):
            with self.subTest(key=key):
                self.assertIn("your", env[key].lower(), key)
        self.assertIn("PLACEHOLDER", text.upper())

    def test_example_carries_no_credentials(self):
        text = self.example.read_text(encoding="utf-8").lower()
        for forbidden in ("private_key", "client_secret", "password", "api_key"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
