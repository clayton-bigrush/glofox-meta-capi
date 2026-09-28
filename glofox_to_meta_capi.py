#!/usr/bin/env python3
"""
Glofox -> Meta Conversions API sync (FIT4MOM membership attribution).
Cloud-ready: credentials and config come from environment variables (Secret Manager on
Cloud Run). Single standard event: StartTrial, for a PAID Glofox membership start, with value.

Env:
  GLOFOX_API_KEY, GLOFOX_API_TOKEN      Glofox credentials (Secret Manager)
  GLOFOX_BRANCH_IDS                     comma-separated enrolled branch IDs
  META_DATASET_ID                       default 1274450289387879
  META_ACCESS_TOKEN                     Meta CAPI token (Secret Manager)
  META_TEST_EVENT_CODE                  set ONLY for validation; unset in production
  SEND                                  "1" to send; anything else = dry run
  SEND_WINDOW_DAYS                      default 7 (Meta attribution window)
Local fallback: if GLOFOX_API_KEY/TOKEN are unset, reads CREDS_CSV.
Exits non-zero if a Meta batch is rejected, so the scheduler/logs surface failures.
"""
import csv, json, os, hashlib, datetime, urllib.request, urllib.error, time, sys

CREDS_CSV = os.environ.get("CREDS_CSV", "/mnt/user-data/uploads/glofox-test/credentials_20260908_184931.csv")
BASE = "https://gf-api.aws.glofox.com/prod/"
DEFAULT_BRANCH = "679a61d7ae83a5511e06af02"
BRANCH_IDS = [b.strip() for b in os.environ.get("GLOFOX_BRANCH_IDS", DEFAULT_BRANCH).split(",") if b.strip()]
SEND_WINDOW_DAYS = int(os.environ.get("SEND_WINDOW_DAYS", "7"))
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", SEND_WINDOW_DAYS))
CURRENCY = "USD"
ACTION_SOURCE = "system_generated"
EVENT_NAME = "StartTrial"
DRY_RUN = os.environ.get("SEND", "0") != "1"
META_API_VERSION = "v21.0"
META_DATASET_ID = os.environ.get("META_DATASET_ID", "1274450289387879")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_TEST_EVENT_CODE = os.environ.get("META_TEST_EVENT_CODE", "")

API_KEY = os.environ.get("GLOFOX_API_KEY", "").strip()
API_TOKEN = os.environ.get("GLOFOX_API_TOKEN", "").strip()
if not (API_KEY and API_TOKEN) and os.path.exists(CREDS_CSV):
    with open(CREDS_CSV) as f:
        row = next(csv.DictReader(f))
    API_KEY, API_TOKEN = row["Api Key"].strip(), row["Api Token"].strip()
if not (API_KEY and API_TOKEN):
    sys.exit("Missing Glofox credentials (GLOFOX_API_KEY / GLOFOX_API_TOKEN).")

def log(**kv):
    print(json.dumps({"ts": datetime.datetime.utcnow().isoformat() + "Z", **kv}), flush=True)

def gf(path, branch):
    h = {"x-api-key": API_KEY, "x-glofox-api-token": API_TOKEN,
         "x-glofox-branch-id": branch, "Accept": "application/json"}
    req = urllib.request.Request(BASE + path.lstrip("/"), headers=h, method="GET")
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5); continue
            return {"_error": e.code}
        except Exception as e:
            return {"_error": str(e)}
    return {"_error": "retries"}

def list_members(branch, limit=50, max_pages=200):
    out, page = [], 1
    while page <= max_pages:
        r = gf(f"2.0/members?page={page}&limit={limit}", branch)
        if "_error" in r: break
        out.extend(r.get("data", []))
        if not r.get("has_more"): break
        page += 1
    return out

sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
def h_email(e): return [sha(e.strip().lower())] if e and "@" in e else []
def h_phone(p):
    d = "".join(c for c in str(p or "") if c.isdigit())
    if len(d) == 10: d = "1" + d
    return [sha(d)] if len(d) >= 11 else []
def h_name(n): return [sha(n.strip().lower())] if n else []

def user_data(detail):
    addr = detail.get("address") or {}
    ud = {}
    if h_email(detail.get("email")): ud["em"] = h_email(detail.get("email"))
    if h_phone(detail.get("phone")): ud["ph"] = h_phone(detail.get("phone"))
    if h_name(detail.get("first_name")): ud["fn"] = h_name(detail.get("first_name"))
    if h_name(detail.get("last_name")):  ud["ln"] = h_name(detail.get("last_name"))
    if detail.get("_id"): ud["external_id"] = [detail["_id"]]
    zp = addr.get("postcode") or addr.get("postal_code")
    if zp: ud["zp"] = [sha(str(zp).strip().lower())]
    return ud

def classify(mm):
    mtype = mm.get("type")
    if mtype == "payg" or bool(mm.get("trial")):
        return None, None
    price = mm.get("plan_price")
    if isinstance(price, (int, float)) and price > 0 and mtype in ("time", "time_classes", "num_classes"):
        return EVENT_NAME, float(price)
    return None, None

def build_events(branch, now, cutoff):
    members = list_members(branch)
    events = []
    for u in members:
        sd = (u.get("membership") or {}).get("start_date")
        if not (isinstance(sd, int) and cutoff <= sd <= now):
            continue
        detail = gf(f"2.0/members/{u['_id']}", branch)
        if "_error" in detail:
            continue
        ud = user_data(detail)
        if not ud.get("em") and not ud.get("ph"):
            continue
        ev, val = classify(detail.get("membership") or {})
        if not ev:
            continue
        e = {"event_name": ev, "event_time": sd, "action_source": ACTION_SOURCE,
             "event_id": f"{ev}:{u['_id']}:{sd}", "user_data": ud}
        if val is not None:
            e["custom_data"] = {"value": val, "currency": CURRENCY}
        events.append(e)
    log(event="branch_done", branch=branch, members=len(members), built=len(events))
    return events

def send_to_meta(events):
    url = f"https://graph.facebook.com/{META_API_VERSION}/{META_DATASET_ID}/events?access_token={META_ACCESS_TOKEN}"
    payload = {"data": events}
    if META_TEST_EVENT_CODE:
        payload["test_event_code"] = META_TEST_EVENT_CODE
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def main():
    now = int(datetime.datetime.utcnow().timestamp())
    cutoff = now - WINDOW_DAYS * 86400
    all_events = []
    for b in BRANCH_IDS:
        all_events.extend(build_events(b, now, cutoff))
    total_value = sum(e.get("custom_data", {}).get("value", 0) for e in all_events)
    log(event="built", branches=len(BRANCH_IDS), events=len(all_events),
        value_total=round(total_value, 2), dry_run=DRY_RUN, window_days=WINDOW_DAYS,
        test_mode=bool(META_TEST_EVENT_CODE))

    if DRY_RUN:
        log(event="dry_run_complete", sent=0)
        return
    if not META_ACCESS_TOKEN:
        sys.exit("SEND=1 but META_ACCESS_TOKEN missing.")

    received, failed = 0, False
    for i in range(0, len(all_events), 1000):
        st, body = send_to_meta(all_events[i:i + 1000])
        ok = st == 200
        try:
            received += json.loads(body).get("events_received", 0)
        except Exception:
            pass
        log(event="meta_send", http=st, ok=ok, body=body[:400])
        if not ok:
            failed = True
    log(event="run_complete", events_received=received, batches_failed=failed)
    if failed:
        sys.exit("One or more Meta batches were rejected (see logs).")

if __name__ == "__main__":
    main()
