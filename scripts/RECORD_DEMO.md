# 30-second demo video — automated recording

Drives the storyboard end-to-end in Playwright, captures two browser
contexts (alice + bob) as webm, then composites them into the final
1080p30 mp4 with `ffmpeg`. Output: `tmp/demo/final.mp4`.

## Prereqs

| | |
|---|---|
| Postgres up | `docker compose up -d postgres` (or your local) |
| Seed fixtures | `python -c "..."` snippet in `scripts/seed_demo_data.py` header |
| API_Server | `uvicorn app.main:app --port 8000` (from `API_Server/`) |
| Frontend dev | `pnpm dev` (from `Frontend/`) |
| Modal warm-up | one `POST /v1/health` or dummy `/v1/complete` so the first take isn't a 30-50s cold start |
| `ffmpeg` on PATH | for the compositor step |

The Frontend may be built with any `NEXT_PUBLIC_DEV_TOKEN`; the recorder
overrides the `Authorization` header per browser context with fresh
JWTs minted via `/api/v1/auth/login`, so the same dev server handles
both users in parallel.

## Run

```powershell
# 1. record (two Chromium windows pop up — don't touch them)
cd Frontend
pnpm run record:demo

# 2. composite into final mp4
cd ..
pwsh scripts/compose_demo_video.ps1
```

Outputs land in `tmp/demo/`:
- `raw/alice.webm` + `raw/bob.webm` — per-context raw recordings
- `markers.json` — scene/wait spans (ms relative to each context's recording start)
- `segments/seg_NN_*.mp4` — per-scene re-encodes
- `final.mp4` — the deliverable

## What gets cut

Scene markers stay; `kind=wait` markers (LLM round-trips, fixture
loads) are dropped. The compositor stitches scenes in storyboard
order (`scene1_hook` → `scene5_close`) regardless of the chronological
order the recorder hit them in.

## Tuning the take

Edit `Frontend/tests/record-demo.spec.ts`:
- `waitForTimeout(n)` lines control the "hold" length per scene
- subtitle text → `showSubtitle(page, "...")` calls
- new scenes → add a `markScene(...)` call AND extend the `$order`
  array in `scripts/compose_demo_video.ps1`

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `login alice@example.com failed: 401` | re-run `seed_demo_data.py` (truncates + recreates users) |
| Selector timeout on `proposed-summary` | Modal cold start — warm it up, then re-record |
| `missing alice.webm in tmp/demo/raw` | recorder crashed before rename; check Playwright stderr |
| Scene out of order in final.mp4 | `$order` array in compositor doesn't include a scene the recorder emitted — `markers.json` lists actual names |
| Final mp4 way longer than 30s | LLM waits not getting cut — confirm `markWait(...)` calls bracket each compose round-trip |
