"""Push secrets from GCP Secret Manager into Modal Secrets.

Direction is GCP -> Modal. The two stores must hold the same value for the
bearer-auth wiring to work: API_Server (and our smoke tests) read the
header value from GCP Secret Manager, while the AI_Agent FastAPI app
running on Modal compares incoming requests against the value Modal
injects from its own Secret store. After rotating the GCP secret, run
this script to mirror the new value into Modal.

Run from the repo root in git bash (or any shell with `gcloud` and
`modal` on PATH and authenticated):

    python scripts/sync-modal-secrets.py

The secret payload never crosses our process boundary as a stream and
never lands in argv:
- gcloud writes the GCP value to tempfile1 via `--out-file=`
- The script writes a single dotenv line `KEY="value"` into tempfile2
- modal CLI ingests tempfile2 via `--from-dotenv=` (no KEY=VALUE on argv)
- Both tempfiles are zeroed and unlinked

Important: Modal injects secrets at CONTAINER START. Containers already
running keep their old values until they recycle. After this script
succeeds, force a fresh container start with one of:

    modal app stop auto-workflow-agent   # next request cold-starts
    modal deploy AI_Agent/scripts/modal_app.py   # full redeploy

or just wait `scaledown_window` seconds (300s in modal_app.py) and let
the next request pick up the new value.

Mapping is hardcoded below. Add a row when a new (modal_secret, env_var,
gcp_secret) combination shows up.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

PROJECT = "autoworkflowdemo"

# On Windows + git bash, both `gcloud` and `modal` ship as .cmd batch
# wrappers. Resolve at startup so a missing install fails clearly instead
# of as WinError 2 from inside subprocess.
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"
MODAL = shutil.which("modal") or shutil.which("modal.cmd") or "modal"

# (modal_secret_name, env_var_inside_modal_secret, gcp_secret_name)
# Each row says "Modal Secret X exposes env var Y, and Y must equal the
# latest version of GCP Secret Z." If that's not true for some pair,
# don't add the row.
MAPPING: list[tuple[str, str, str]] = [
    ("agent-bearer-token", "AGENT_BEARER_TOKEN", "agent-bearer-token-staging"),
]


def fetch_gcp_secret(secret_name: str) -> str:
    """Pull `latest` of a GCP Secret Manager secret without exposing stdout."""
    fd, tmp_path = tempfile.mkstemp(prefix="gcp-sec-", suffix=".bin")
    os.close(fd)
    try:
        cmd = [
            GCLOUD, "secrets", "versions", "access", "latest",
            "--secret", secret_name,
            "--project", PROJECT,
            f"--out-file={tmp_path}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"gcloud failed for {secret_name}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )
        with open(tmp_path, "rb") as f:
            raw = f.read()
        try:
            with open(tmp_path, "wb") as f:
                f.write(b"\x00" * len(raw))
        except OSError:
            pass
        return raw.decode("utf-8").rstrip("\r\n")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def push_modal_secret(modal_name: str, env_var: str, value: str) -> None:
    """Push {env_var: value} into the Modal Secret named `modal_name`.

    Uses `modal secret create --from-dotenv=PATH --force` so the value never
    appears in argv (no `ps`, shell history, or process-listing exposure).
    The dotenv tempfile lives in OS tempdir, is zeroed and unlinked.

    `--force` overwrites the existing Modal Secret in place. New container
    starts pick up the new value; already-running containers keep the old
    one until they recycle.
    """
    if "\n" in value or "\r" in value:
        # We write KEY="value" on one line; embedded newlines would break
        # the dotenv parser. Bearer tokens don't contain newlines, but
        # guard anyway to fail loudly instead of silently truncating.
        raise ValueError(
            f"value for {env_var} contains a newline -- dotenv format unsupported"
        )

    fd, tmp_path = tempfile.mkstemp(prefix="modal-sec-", suffix=".env")
    os.close(fd)
    try:
        # Double-quoted form lets python-dotenv (modal's parser) handle
        # special characters cleanly. Internal `"` would break this; raise
        # if we ever hit one — bearer tokens shouldn't contain quotes.
        if '"' in value:
            raise ValueError(
                f"value for {env_var} contains a double quote -- "
                "tighten escape handling before pushing"
            )
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'{env_var}="{value}"\n')

        cmd = [
            MODAL, "secret", "create", modal_name,
            f"--from-dotenv={tmp_path}",
            "--force",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"modal secret create failed for {modal_name}: "
                f"{result.stderr.strip() or result.stdout.strip() or '(no output)'}"
            )
    finally:
        try:
            # Best-effort zero before unlink. tmp_path may be on a slow
            # filesystem (Windows tempdir on a spinning disk); ignore
            # errors so we still unlink.
            with open(tmp_path, "wb") as f:
                f.write(b"\x00" * 4096)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main() -> int:
    failures = 0
    for modal_name, env_var, gcp_secret in MAPPING:
        try:
            value = fetch_gcp_secret(gcp_secret)
            if not value:
                raise RuntimeError("empty payload from GCP")
            push_modal_secret(modal_name, env_var, value)
        except Exception as exc:
            print(
                f"[FAIL] modal:{modal_name} :: {env_var} <- gcp:{gcp_secret}: {exc}",
                file=sys.stderr,
            )
            failures += 1
            continue
        # Print only metadata -- never the value, never even a prefix.
        print(
            f"[pushed] modal:{modal_name} :: {env_var} "
            f"(from gcp:{gcp_secret}, {len(value)} chars)"
        )
    if not failures:
        print(
            "Note: Modal injects secrets at container start. Force a fresh "
            "container with `modal app stop auto-workflow-agent` or redeploy "
            "to pick up the new value immediately.",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
