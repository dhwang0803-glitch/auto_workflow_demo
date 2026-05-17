# Live demo bundle — recording spec

This directory mirrors the scripts that produced
[`../media/teamlift.mp4`](../media/teamlift.mp4) (the
primary demo, ~74 s, narration + subtitles) and its silent companion
[`../media/demo_30s.mp4`](../media/demo_30s.mp4) (~70 s, subtitles
only). It is **spec / reference material**, not a one-click runnable
demo — the recorder talks to a live AI_Agent backend (Modal-hosted
Gemma 4 on an L4 GPU) that is not portable to a judge's machine, and
the narration was generated separately via ElevenLabs TTS.

The video is the primary demo deliverable; this bundle is here so the
recording process is fully transparent.

## What's here

| File | Purpose |
|---|---|
| `seed_demo_data.py` | Truncates and reseeds Postgres with `alice@example.com` + `bob@example.com` + one workspace skill + one personal-skill candidate |
| `run_demo_scenarios.py` | HTTP-only driver for the three demo tracks. Used in dev for live verification; the video itself was driven by Playwright (see `RECORD_DEMO.md`) |
| `RECORD_DEMO.md` | Playwright + ffmpeg recipe used to record the video |
| `compose_demo_video.ps1` | ffmpeg compositor invoked by the recorder |

## Reproducing the recording

Requires either:

- **a Modal account** with `modal deploy AI_Agent/scripts/modal_app.py`
  (~$1 / hour while the L4 container is hot), **OR**
- **local llama.cpp** on a 24 GB GPU with the
  `unsloth/gemma-4-26B-A4B-it-GGUF` UD-Q4_K_M weights

Stub mode (`AI_COMPOSER_USE_STUB=1`) only exercises the Frontend
without an LLM, so it does not reproduce the AI behaviour the demo is
actually about.

## What the scenarios verify

If you do bring up a live backend, `python run_demo_scenarios.py --all`
asserts each track end-to-end over HTTP (no UI):

- **Track A** — alice's `POST /v1/skills/extract` returns a SkillCard,
  then bob can `GET /api/v1/skills` and the new workspace skill is in
  the list.
- **Track B** — alice saves an edited workflow, the next
  `POST /v1/personalization/extract_from_diff` produces a candidate
  marked `pending_review`, and a fresh `POST /v1/compose` retrieves it
  alongside workspace skills.
- **Track C** — `POST /v1/personalization/{id}/share` flips the
  candidate's `scope` from `user` → `workspace`. From bob's session,
  the same row now appears in `/api/v1/skills` (team marketplace).

Architecture overview and per-track code locations: see the repo root
[`README.md`](../../README.md#code-tour).
