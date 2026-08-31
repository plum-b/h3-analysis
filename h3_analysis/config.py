"""Local configuration loading.

Deployed environments (Cloud Run) inject configuration as real environment
variables, so nothing here runs in production. For local development this reads
an optional, Git-ignored ``.env`` file at the repository root so developers do
not have to re-export the BigQuery table names in every shell.

Values already present in the environment always win, which keeps
``H3_DATA_SOURCE=local python -m streamlit run app.py`` and CI overrides working.
Only non-sensitive identifiers belong in ``.env``; credentials come from
Application Default Credentials, never from a file in the repository.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_local_env(path: Path | None = None) -> dict[str, str]:
    """Populate ``os.environ`` from a ``KEY=value`` file, returning what was set.

    Missing files are not an error - the file is a developer convenience. Blank
    lines and ``#`` comments are skipped, surrounding quotes are stripped, and
    existing environment variables are never overwritten.
    """
    path = ENV_FILE if path is None else path
    applied: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, OSError):
        return applied

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
