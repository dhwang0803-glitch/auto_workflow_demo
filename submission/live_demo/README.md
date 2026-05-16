# Live demo — local replay

This bundle lets a judge run the full three-track demo against a local
stack in ~5 minutes. The demo video (`../media/demo_30s.mp4`) was
recorded with these same scripts.

## What you get

| File | Purpose |
|---|---|
| `seed_demo_data.py` | Truncates and reseeds Postgres with `alice@demo.local` + `bob@demo.local` + one workspace skill |
| `run_demo_scenarios.py` | Drives the three live scenarios end-to-end via HTTP (no UI required) |
| `RECORD_DEMO.md` | Instructions for re-recording the 30-second mp4 with Playwright |
| `compose_demo_video.ps1` | ffmpeg compositor invoked by the recorder |

## Prereqs

- Postgres on `localhost:5435` (or your own — set `DATABASE_URL`)
- API_Server running on `localhost:8000`
- AI_Agent reachable (live Modal endpoint, local llama.cpp, or stub)

The full stack-up instructions are in the repo root `README.md`.

## Steps

```powershell
# 1. seed
$env:PYTHONUTF8 = "1"
python seed_demo_data.py
#   prints alice / bob passwords

# 2. run all three tracks
python run_demo_scenarios.py --all
#   Track A: marketplace adoption (alice extracts → bob adopts)
#   Track B: personalization (alice edits → next draft reflects edit)
#   Track C: share (bob promotes alice's personal skill to workspace)

# 3. (optional) re-record video — see RECORD_DEMO.md
```

Each scenario emits an NDJSON trace; `run_demo_scenarios.py --help`
shows per-track flags.

## What you should see

- Track A — alice's `POST /v1/skills/extract` returns a SkillCard, then
  bob can `GET /api/v1/skills` and the new workspace skill is in the list.
- Track B — alice saves an edited workflow, the next
  `POST /v1/personalization/extract_from_diff` produces a candidate
  marked `pending_review`, and a fresh `POST /v1/compose` retrieves it
  alongside workspace skills.
- Track C — `POST /v1/personalization/{id}/share` flips the candidate's
  `scope` from `user` to `workspace`. From bob's session, the same row
  now appears in `/api/v1/skills` (team marketplace).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `login alice@example.com failed: 401` | re-run `seed_demo_data.py` |
| `ECONNREFUSED localhost:8000` | start API_Server (`uvicorn app.main:app --port 8000`) |
| `502` from `/v1/compose` | Modal cold start — first call may take 30-90 s, retry |
| `cp949` decode error | set `$env:PYTHONUTF8 = "1"` before any python invocation |
