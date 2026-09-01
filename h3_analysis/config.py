"""Local configuration loading.

Deployed environments (Cloud Run) inject configuration as real environment
variables, so nothing here runs in production. For local development this reads
an optional, Git-ignored ``.env`` file at the repository root so developers do
not have to re-export the BigQuery table names in every shell.

Values already present in the environment always win, which keeps
``H3_DATA_SOURCE=local python -m streamlit run app.py`` and CI overrides working.
Only non-sensitive identifiers belong in ``.env``; credentials come from the
platform - Application Default Credentials, or a ``[gcp_service_account]``
secret - never from a file in the repository.

Streamlit Community Cloud has no environment-variable UI: configuration is
pasted into the app's Secrets, and Streamlit promotes top-level secret values
to ``os.environ`` when the secrets are first read. That read is lazy, so
:func:`prime_streamlit_secrets` forces it before anything looks the table
names up.
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


def prime_streamlit_secrets() -> bool:
    """Load Streamlit's secrets early, returning whether any were found.

    Reading ``st.secrets`` is what promotes its top-level values to
    ``os.environ``; on Streamlit Community Cloud that is how
    ``BIGQUERY_PROJECT_ID`` and the table names arrive. Doing it here means the
    promotion has already happened by the time a page resolves a table.

    Outside Streamlit, or with no secrets configured, this is a no-op: the
    ``.env`` file and real environment variables are unaffected, and a
    ``[gcp_service_account]`` section is left for
    :func:`h3_analysis.bigquery_source.credentials_from_secrets` to read.
    """
    try:
        import streamlit as st

        secrets = st.secrets
        loader = getattr(secrets, "load_if_toml_exists", None)
        if callable(loader):
            return bool(loader())
        return bool(len(secrets))
    except Exception:
        # No streamlit, or no secrets file - both are normal locally.
        return False
