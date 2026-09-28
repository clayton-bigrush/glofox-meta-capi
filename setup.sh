#!/usr/bin/env bash
# Interactive one-time setup for the Glofox -> Meta CAPI sync on Google Cloud.
# Run this from the folder that holds this file:  bash setup.sh
# It asks for your project, region, branch IDs, and the three secret values,
# then deploys everything and schedules it every 6 hours. Safe to re-run.
set -euo pipefail

echo "=== Glofox -> Meta CAPI: Cloud Run setup ==="
read -rp "GCP Project ID: " PROJECT_ID
read -rp "Region [us-central1]: " REGION; REGION="${REGION:-us-central1}"
read -rp "Glofox branch IDs, comma-separated [679a61d7ae83a5511e06af02]: " BRANCH_IDS
BRANCH_IDS="${BRANCH_IDS:-679a61d7ae83a5511e06af02}"
JOB="glofox-meta-capi"

gcloud config set project "$PROJECT_ID"

echo "Enabling APIs (one-time, ~1 min)..."
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

echo "Paste your secret values (they will not display on screen):"
read -rsp "Glofox API Key: " GK; echo
read -rsp "Glofox API Token: " GT; echo
read -rsp "Meta CAPI Access Token: " MT; echo

put_secret () {  # $1 name  $2 value
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=-
  else
    printf '%s' "$2" | gcloud secrets create "$1" --data-file=-
  fi
}
put_secret glofox-api-key   "$GK"
put_secret glofox-api-token "$GT"
put_secret meta-capi-token  "$MT"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Granting the runtime service account access to the secrets..."
for S in glofox-api-key glofox-api-token meta-capi-token; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member "serviceAccount:${SA}" --role roles/secretmanager.secretAccessor >/dev/null
done

echo "Building and deploying the job (first build ~2-3 min)..."
gcloud run jobs deploy "$JOB" \
  --source . --region "$REGION" \
  --tasks 1 --max-retries 1 --task-timeout 900 --memory 512Mi \
  --set-env-vars "SEND=1,SEND_WINDOW_DAYS=7,META_DATASET_ID=1274450289387879,GLOFOX_BRANCH_IDS=${BRANCH_IDS}" \
  --set-secrets "GLOFOX_API_KEY=glofox-api-key:latest,GLOFOX_API_TOKEN=glofox-api-token:latest,META_ACCESS_TOKEN=meta-capi-token:latest"

echo "Allowing the scheduler to run the job..."
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member "serviceAccount:${SA}" --role roles/run.invoker >/dev/null

echo "Creating the every-6-hours schedule..."
if gcloud scheduler jobs describe "${JOB}-schedule" --location "$REGION" >/dev/null 2>&1; then
  echo "  (schedule already exists, leaving it as-is)"
else
  gcloud scheduler jobs create http "${JOB}-schedule" \
    --location "$REGION" --schedule "0 */6 * * *" \
    --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB}:run" \
    --http-method POST --oauth-service-account-email "$SA"
fi

cat <<EOF

=== Done. ===
Run it once now to confirm:
  gcloud run jobs execute $JOB --region $REGION --wait
See the result (look for "events_received"):
  gcloud logging read 'resource.type=cloud_run_job' --limit 30 --format='value(textPayload)' --freshness=10m
Add a franchisee later: append their branch ID and redeploy:
  gcloud run jobs update $JOB --region $REGION --update-env-vars GLOFOX_BRANCH_IDS=id1,id2,id3
EOF
