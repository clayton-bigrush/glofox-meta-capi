# Glofox → Meta CAPI — FIT4MOM Membership Attribution

Sends a Meta Conversions API **StartTrial** event for each new PAID Glofox
membership, so Meta can attribute franchise ad spend to real paying members.
Counts real, first-time paid signups only — no renewals, no double-counting.

## Files
- `glofox_to_meta_capi.py` — the pipeline (Python standard library only, no deps)
- `Dockerfile` — container image for Cloud Run
- `setup.sh` — one-time interactive deploy to Google Cloud

## Where it runs
Deployed as a Cloud Run **Job** in the Google Cloud project `big-rush-platform`
(region `us-central1`, job `glofox-meta-capi`), triggered every 6 hours by Cloud
Scheduler. Credentials live in Secret Manager — never in this repo.

## Config (environment variables)
- `GLOFOX_API_KEY`, `GLOFOX_API_TOKEN` — Glofox credentials (Secret Manager)
- `GLOFOX_BRANCH_IDS` — comma-separated enrolled Glofox branch IDs
- `META_DATASET_ID` — FIT4MOM Meta dataset (default set in code)
- `META_ACCESS_TOKEN` — Meta CAPI token (Secret Manager)
- `SEND=1` to send; `SEND_WINDOW_DAYS=7`

## Add a franchisee (branch)
Append the branch ID and redeploy:

    gcloud run jobs update glofox-meta-capi --region us-central1 \
      --update-env-vars GLOFOX_BRANCH_IDS=id1,id2,id3

## Never commit
Glofox credentials CSV, the Meta token, or any `.env` — see `.gitignore`.
