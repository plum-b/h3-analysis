# H3 Analysis

A Streamlit application for exploring audience **index** metrics on an H3 grid,
focused on the UAE. The app has **exactly two map pages**, each on its own
Streamlit page because they use different data sources and analysis logic:

| Page | File | Data source | Filter | Metric |
| --- | --- | --- | --- | --- |
| 1. Two-Hour Index Analysis | `pages/1_Two-Hour_Index_Analysis.py` | **BigQuery** (3 tables) — local CSV = dev fallback | audience segment + one two-hour period | `overall_index` / `volume_index` / `exclusivity_index` (switchable) |
| 2. Day-Part Index Analysis | `pages/2_Day-Part_Index_Analysis.py` | **BigQuery** (3 `*_day_sections` tables) — local CSV = dev fallback | audience segment **+ day-part** | `overall_index` / `volume_index` / `exclusivity_index` (switchable) |

Only one map is shown per page. Each page averages the selected metric across the
chosen segments per `h3_id` (index values are **averaged, never summed**).

## Current phase

Both pages are implemented on BigQuery. One table per metric, per page.

- **Page 1** — schema `h3_id` STRING, `segment` STRING, `<metric>` FLOAT,
  `hour_bucket` **INT64** — the two-hour period of the day (0, 2, 4 … 22). Each
  `(h3_id, segment, hour_bucket)` triple appears **exactly once**; the ~8.4 rows
  per `(h3_id, segment)` pair are simply the twelve periods. The page slices to
  one period, then averages within each `(h3_id, segment, hour_bucket)` group
  *before* averaging across segments (a two-step aggregation), so a segment
  contributing more rows cannot dominate a cell.
- **Page 2** — schema `h3_id` STRING, `segment` STRING, `<metric>` FLOAT,
  `hour_bucket` STRING (the day-part). Each `(h3_id, segment, hour_bucket)`
  triple appears **exactly once**, so after filtering to one day-part the metric
  is averaged across segments in a **single** step.

Production never reads a production CSV on either page.

### Page 2 schema (verified against the live tables)

| Column | Type | Meaning |
| --- | --- | --- |
| `h3_id` | STRING | Valid H3 cell index, **resolution 9** |
| `segment` | STRING | `Families`, `HNWI`, `Potential Car Buyers` |
| `<metric>` | FLOAT ≥ 0 | `overall_index` / `volume_index` / `exclusivity_index` |
| `hour_bucket` | STRING | `Morning`, `Noon`, `After noon`, `Night`, `Other` |

The column is called `hour_bucket` but holds **day-part labels, not hours**.
Each table has ~601k rows over ~60.2k distinct cells (3 segments × 5
day-parts), with no NULL or negative metric values, all inside the UAE.

### Fixed: Page 2 showed only a few cells, some in the ocean

Page 2 was never actually connected to its data. It read
`data/map_2/exclusivity_index.csv` / `volume_index.csv`, but that directory is
empty and `.gitignore`d, so it silently fell back to `data/sample_index.csv` — a
**synthetic 45-cell, resolution-8** file. Hence "only a few cells" (45 vs
~60.2k), and "cells in the ocean" (a resolution-8 hexagon covers ~7x the area of
a resolution-9 one, so coastal cells visibly overhang the shoreline). The H3
IDs, centroid handling, and shared map rendering were all correct, and there is
no join anywhere in this path. Page 2 now reads its own three `*_day_sections`
BigQuery tables, gains a day-part filter, and its local fallback
(`data/sample_index_day_sections.csv`) is resolution 9 with the real schema.

## Future plan

Add further BigQuery tables/pages only when their approved schema and purpose
are provided.

The old CSV-only "Overall analysis index" (`data/map_3`) map and the hourly
`user_count` map have been removed. `overall_index` now lives only as a
BigQuery metric (on both pages, from different tables).

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate            # PowerShell: .venv\Scripts\Activate.ps1
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

### Running Page 1 without BigQuery

Set the sidebar **Data source** to `Local CSV (development)`, or start with:

```bash
H3_DATA_SOURCE=local python3 -m streamlit run app.py
```

The local fallback uses an uploaded CSV, otherwise the committed synthetic
`data/sample_index_two_hours.csv` (all three metric columns plus the twelve
two-hour `hour_bucket` values). The same sidebar switch and
`H3_DATA_SOURCE=local` also apply to Page 2, whose fallback is
`data/sample_index_day_sections.csv` (all three metrics plus its day-part
`hour_bucket`).

### Running Page 1 against BigQuery locally

Copy the committed template once — it already names the production tables — then
authenticate:

```bash
cp .env.example .env
gcloud auth application-default login
```

`.env` is Git-ignored and holds **identifiers only**; credentials always come
from Application Default Credentials. Real environment variables override it, so
CI and Cloud Run are unaffected.

Verify configuration, permissions, and table schemas before launching the UI:

```bash
python3 scripts/check_bigquery.py
```

It checks **both pages**: it resolves each metric's table, confirms the expected
columns exist (`h3_id` / `segment` / `<metric>` / `hour_bucket`), prints the
billing project, lists the segments plus each page's time values (Page 1 two-hour
periods, Page 2 day-parts), and runs the real aggregation query for one segment.
Then start the app:

```bash
python3 -m streamlit run app.py
```

Each Page 1 table must expose `h3_id` (STRING), `segment` (STRING), one numeric
column named exactly after its metric, and `hour_bucket` (INT64, the two-hour
period); each Page 2 `*_day_sections` table must expose the same columns with
`hour_bucket` as STRING day-part labels. Segment filters are always sent as an
array query parameter, and the time filter as a scalar parameter
(`@two_hour_period` on Page 1, `@hour_bucket` on Page 2); no user value is
interpolated into SQL, and only the validated table FQN reaches the `FROM`
clause.

Query **jobs** are billed to `BIGQUERY_BILLING_PROJECT`, or `BIGQUERY_PROJECT_ID`
when that is unset. Without this the client falls back to whatever project the
local `gcloud` config points at, which fails with a `bigquery.jobs.create`
permission error naming a project that appears nowhere in this repository even
though the tables are readable.

## Basemaps

The basemap radio offers **Streets + terrain** (the default), **Street Map** and
**Dark**. The detailed style is OpenFreeMap Liberty: building footprints, 28 road
classes, POI and place labels, and a Natural Earth shaded-relief source, so
streets and buildings read through the translucent H3 layer as you zoom in. It is
token-free.

Set `H3_BASEMAP_STYLE_URL` to any MapLibre style.json URL to use a different
provider — that variable is also where a provider key belongs, never in source.
Anything malformed or unrecognised falls back to CARTO Voyager, so a basemap can
never be the reason the map fails to draw.

## Configuration (environment variables)

Current production values (also in `.env.example`):

| Variable | Purpose | Value |
| --- | --- | --- |
| `BIGQUERY_PROJECT_ID` | GCP project holding the tables | `your-gcp-project` |
| `BIGQUERY_DATASET` | dataset holding all six tables | `your_dataset` |
| `BIGQUERY_BILLING_PROJECT` | Project the query **jobs** are billed to (defaults to `BIGQUERY_PROJECT_ID`) | `your-gcp-project` |
| `H3_BASEMAP_STYLE_URL` | MapLibre style.json URL for the detailed basemap (optional) | OpenFreeMap Liberty |
| `BIGQUERY_OVERALL_INDEX_TABLE` | Page 1 table for `overall_index` | `overall_index_table` |
| `BIGQUERY_VOLUME_INDEX_TABLE` | Page 1 table for `volume_index` | `volume_index_table` |
| `BIGQUERY_EXCLUSIVITY_INDEX_TABLE` | Page 1 table for `exclusivity_index` | `exclusivity_index_table` |
| `BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE` | Page 2 table for `overall_index` | `overall_index_day_sections_table` |
| `BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE` | Page 2 table for `volume_index` | `volume_index_day_sections_table` |
| `BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE` | Page 2 table for `exclusivity_index` | `exclusivity_index_day_sections_table` |
| `BIGQUERY_<METRIC>[_DAY_SECTIONS]_TABLE_FQN` | optional per-metric override, full `project.dataset.table` | — |
| `H3_DATA_SOURCE=local` | default the sidebar to the local CSV fallback | — |
| `PORT` | Cloud Run injects this; the app binds it | — |

Resolution order: real environment variables, then the Git-ignored `.env`. The
table names live in configuration only — never in source code.

Credentials come from **Application Default Credentials** — never a JSON key in
the repo or image. Local dev uses `gcloud auth application-default login`;
Cloud Run uses its attached runtime service account.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Deployment guide (Cloud Run + Workload Identity Federation)

No service-account key is ever downloaded or committed. GitHub authenticates to
Google Cloud with Workload Identity Federation
(`.github/workflows/deploy-cloud-run.yml`).

### 1. Required Google Cloud APIs

```bash
gcloud services enable \
  run.googleapis.com \
  bigquery.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com
```

### 2. Cloud Run runtime service account

```bash
gcloud iam service-accounts create h3-analysis-run \
  --display-name="H3 Analysis Cloud Run runtime"
```

Cloud Run runs the container as this account; the app's ADC resolves to it.

### 3. Minimum BigQuery read permissions for the Page 1 tables

Grant the runtime service account the least privilege that still lets it run a
query and read the three tables:

```bash
PROJECT_ID=your-gcp-project
RUN_SA=h3-analysis-run@${PROJECT_ID}.iam.gserviceaccount.com

# Run query jobs in the project.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUN_SA}" \
  --role="roles/bigquery.jobUser"

# Read data - scope to the dataset (covers all three tables), or repeat
# `bq add-iam-policy-binding` per table for tighter scope.
bq add-iam-policy-binding \
  --member="serviceAccount:${RUN_SA}" \
  --role="roles/bigquery.dataViewer" \
  "${PROJECT_ID}:your_dataset"
```

If the tables live in another project, also grant `roles/bigquery.jobUser`
there (or run jobs in the data project).

### 4. GitHub Actions Workload Identity Federation

```bash
PROJECT_ID=your-gcp-project
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
DEPLOY_SA=deployer@${PROJECT_ID}.iam.gserviceaccount.com
REPO=your-org/your-repo

gcloud iam service-accounts create deployer --display-name="GitHub deployer"

gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Let the GitHub repo impersonate the deploy service account.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}"

# Deploy permissions for the deployer SA.
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" --role="$ROLE"
done
```

Create an Artifact Registry repo once:

```bash
gcloud artifacts repositories create containers \
  --repository-format=docker --location=your-region
```

Then set these GitHub **Variables** (identifiers, not secrets):
`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`
(`projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github`),
`GCP_DEPLOY_SERVICE_ACCOUNT`, `CLOUD_RUN_SERVICE`,
`CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`, `ARTIFACT_REGISTRY_REPO`,
`BIGQUERY_PROJECT_ID`, `BIGQUERY_DATASET`, `BIGQUERY_OVERALL_INDEX_TABLE`,
`BIGQUERY_VOLUME_INDEX_TABLE`, `BIGQUERY_EXCLUSIVITY_INDEX_TABLE`,
`BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE`,
`BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE`,
`BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE`.

### 5. Cloud Run deployment

Push to `main` (or run the workflow manually). The workflow builds the image,
pushes it to Artifact Registry, and deploys with `--no-allow-unauthenticated`
and internal ingress. Manual equivalent:

```bash
gcloud run deploy h3-analysis \
  --source . --region your-region \
  --service-account h3-analysis-run@your-gcp-project.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --ingress internal-and-cloud-load-balancing \
  --set-env-vars ^@@^BIGQUERY_PROJECT_ID=your-gcp-project@@BIGQUERY_DATASET=your_dataset@@BIGQUERY_OVERALL_INDEX_TABLE=overall_index_table@@BIGQUERY_VOLUME_INDEX_TABLE=volume_index_table@@BIGQUERY_EXCLUSIVITY_INDEX_TABLE=exclusivity_index_table@@BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE=overall_index_day_sections_table@@BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE=volume_index_day_sections_table@@BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE=exclusivity_index_day_sections_table
```

### 6. Internal-only access (Cloud Run auth + IAP)

- Deploy with `--no-allow-unauthenticated` (the workflow does this).
- Put the service behind an external HTTPS load balancer with a serverless NEG
  and enable **Identity-Aware Proxy** on the backend service.
- Grant `roles/iap.httpsResourceAccessor` only to your internal group:

  ```bash
  gcloud iap web add-iam-policy-binding \
    --resource-type=backend-services --service=h3-analysis-backend \
    --member="group:staff@your-domain.com" \
    --role="roles/iap.httpsResourceAccessor"
  ```

- Keep ingress `internal-and-cloud-load-balancing` so the run URL is not
  publicly reachable.

### 7. Required environment variables (deployed)

| Variable | Value |
| --- | --- |
| `BIGQUERY_PROJECT_ID` | e.g. `your-gcp-project` |
| `BIGQUERY_DATASET` | e.g. `your_dataset` |
| `BIGQUERY_OVERALL_INDEX_TABLE` | e.g. `overall_index_table` |
| `BIGQUERY_VOLUME_INDEX_TABLE` | e.g. `volume_index_table` |
| `BIGQUERY_EXCLUSIVITY_INDEX_TABLE` | e.g. `exclusivity_index_table` |
| `BIGQUERY_OVERALL_INDEX_DAY_SECTIONS_TABLE` | e.g. `overall_index_day_sections_table` |
| `BIGQUERY_VOLUME_INDEX_DAY_SECTIONS_TABLE` | e.g. `volume_index_day_sections_table` |
| `BIGQUERY_EXCLUSIVITY_INDEX_DAY_SECTIONS_TABLE` | e.g. `exclusivity_index_day_sections_table` |
| `PORT` | set automatically by Cloud Run |

No credentials are set as env vars; the runtime service account provides them.

## Do not commit

Service-account JSON keys, `.streamlit/secrets.toml`, production data exports,
map-provider tokens, or any credential.
