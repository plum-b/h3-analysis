# H3 Analysis

A Streamlit application for exploring audience **index** metrics on an H3 grid,
focused on the UAE. The app has **exactly two map pages**, each on its own
Streamlit page because they use different data sources and analysis logic:

| Page | File | Data source | Filter | Metric |
| --- | --- | --- | --- | --- |
| 1. Index analysis | `app.py` | **BigQuery** (3 tables) — local CSV = dev fallback | audience segment | `overall_index` / `volume_index` / `exclusivity_index` (switchable) |
| 2. Index analysis (day-part) | `pages/2_Index_Analysis.py` | local CSV (`data/map_2/*.csv` or `data/sample_index.csv`) | audience segment | `exclusivity_index` / `volume_index` |

Only one map is shown per page. Each page averages the selected metric across the
chosen segments per `h3_id` (index values are **averaged, never summed**).

## Current phase

- **Page 1 is implemented on BigQuery.** One table per metric, all with the
  logical schema `h3_id` STRING, `segment` STRING, `<metric>` FLOAT, **no time
  column**. Each `(h3_id, segment)` pair is repeated (~8x, unevenly), so values
  are averaged within each pair before being averaged across segments.
  Production never reads a production CSV.
- **Page 2 still reads local CSVs** and is a placeholder for the day-part
  (morning / noon / evening / …) BigQuery version.

## Future plan

1. Migrate Page 2 to its own three BigQuery tables that add a day-part
   dimension, giving Page 2 a time-of-day filter.
2. Add further BigQuery tables/pages only when their approved schema and purpose
   are provided.

The old CSV-only "Overall analysis index" (`data/map_3`) map and the hourly
`user_count` map have been removed. `overall_index` now lives only as a Page 1
BigQuery metric.

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
`data/sample_index.csv` (which carries all three metric columns).

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

It resolves each metric's table, confirms `h3_id` / `segment` / `<metric>`
exist, lists the segments, and runs the real aggregation query for one segment.
Then start the app:

```bash
python3 -m streamlit run app.py
```

Each table must expose `h3_id` (STRING), `segment` (STRING) and one numeric
column named exactly after its metric (`overall_index` in
`h3_analysis_indexed_filtered`, `volume_index`, `exclusivity_index`). Segment
filters are always sent as an array query parameter; no user value is
interpolated into SQL, and only the validated table FQN reaches the `FROM`
clause.

## Configuration (environment variables)

Current production values (also in `.env.example`):

| Variable | Purpose | Value |
| --- | --- | --- |
| `BIGQUERY_PROJECT_ID` | GCP project holding the tables | `maddictdata` |
| `BIGQUERY_DATASET` | dataset holding the three tables | `OOH_Analysis` |
| `BIGQUERY_OVERALL_INDEX_TABLE` | table for `overall_index` | `h3_analysis_indexed_filtered` |
| `BIGQUERY_VOLUME_INDEX_TABLE` | table for `volume_index` | `h3_analysis_volume_index_filtered` |
| `BIGQUERY_EXCLUSIVITY_INDEX_TABLE` | table for `exclusivity_index` | `h3_analysis_exclusivity_index_filtered` |
| `BIGQUERY_<METRIC>_TABLE_FQN` | optional per-metric override, full `project.dataset.table` | — |
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
PROJECT_ID=maddictdata
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
  "${PROJECT_ID}:OOH_Analysis"
```

If the tables live in another project, also grant `roles/bigquery.jobUser`
there (or run jobs in the data project).

### 4. GitHub Actions Workload Identity Federation

```bash
PROJECT_ID=maddictdata
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
`BIGQUERY_VOLUME_INDEX_TABLE`, `BIGQUERY_EXCLUSIVITY_INDEX_TABLE`.

### 5. Cloud Run deployment

Push to `main` (or run the workflow manually). The workflow builds the image,
pushes it to Artifact Registry, and deploys with `--no-allow-unauthenticated`
and internal ingress. Manual equivalent:

```bash
gcloud run deploy h3-analysis \
  --source . --region your-region \
  --service-account h3-analysis-run@maddictdata.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --ingress internal-and-cloud-load-balancing \
  --set-env-vars ^@@^BIGQUERY_PROJECT_ID=maddictdata@@BIGQUERY_DATASET=OOH_Analysis@@BIGQUERY_OVERALL_INDEX_TABLE=h3_analysis_indexed_filtered@@BIGQUERY_VOLUME_INDEX_TABLE=h3_analysis_volume_index_filtered@@BIGQUERY_EXCLUSIVITY_INDEX_TABLE=h3_analysis_exclusivity_index_filtered
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
| `BIGQUERY_PROJECT_ID` | e.g. `maddictdata` |
| `BIGQUERY_DATASET` | e.g. `OOH_Analysis` |
| `BIGQUERY_OVERALL_INDEX_TABLE` | e.g. `h3_analysis_indexed_filtered` |
| `BIGQUERY_VOLUME_INDEX_TABLE` | e.g. `h3_analysis_volume_index_filtered` |
| `BIGQUERY_EXCLUSIVITY_INDEX_TABLE` | e.g. `h3_analysis_exclusivity_index_filtered` |
| `PORT` | set automatically by Cloud Run |

No credentials are set as env vars; the runtime service account provides them.

## Do not commit

Service-account JSON keys, `.streamlit/secrets.toml`, production data exports,
map-provider tokens, or any credential.
