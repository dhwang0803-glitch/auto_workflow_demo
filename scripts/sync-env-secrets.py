"""Pull secrets from GCP Secret Manager and write them into local .env files.

Run from the repo root in git bash (or any shell with `gcloud` on PATH and
already authenticated):

    python scripts/sync-env-secrets.py

The secret payload never crosses our process boundary as a stream -- gcloud
writes it directly to a tempfile via `--out-file=`, the script reads the
tempfile in-process, overwrites with zeros, and unlinks before continuing.
The only thing that hits stdout is a per-row status line of the form
`[replaced] AI_Agent/.env :: AGENT_BEARER_TOKEN (from agent-bearer-token-
staging, 48 chars)` -- the secret value itself is never echoed.

Compatible with the PreToolUse hook at `~/.claude/hooks/block-secret-leak.sh`
because every gcloud invocation here uses `--out-file=` (the hook's allowed-
pattern set).

Mapping is hardcoded below. Add a row when a new (env_file, env_var, secret)
combination shows up; do NOT mirror local-dev defaults like Postgres
passwords -- only mirror the values that local dev legitimately needs to
share with staging-like infra (bearer tokens, OAuth client secret, master
key for credential encryption, etc.).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = "autoworkflowdemo"

# On Windows + git bash, the gcloud entry point is gcloud.cmd, which Python's
# subprocess won't resolve from a bare "gcloud" without shell=True. Resolve
# at startup so a missing install errors clearly instead of as WinError 2.
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"

# (relative path from repo root, env var name, GCP secret name)
# Keep this list small. Each row is a real boundary between local-dev and
# staging-shared infra. Adding a row says "local dev MUST use the same value
# as staging." If that's not true for the row in question, leave .env to its
# committed default and skip the sync.
MAPPING: list[tuple[str, str, str]] = [
    ("AI_Agent/.env",    "AGENT_BEARER_TOKEN", "agent-bearer-token-staging"),
    ("API_Server/.env",  "AGENT_BEARER_TOKEN", "agent-bearer-token-staging"),
    # Opt-in extensions (uncomment if local dev needs to talk to staging
    # OAuth / encrypted-credential storage):
    # ("API_Server/.env",       "JWT_SECRET",                 "jwt-secret-staging"),
    # ("API_Server/.env",       "CREDENTIAL_MASTER_KEY",      "credential-master-key-staging"),
    # ("Execution_Engine/.env", "CREDENTIAL_MASTER_KEY",      "credential-master-key-staging"),
    # ("API_Server/.env",       "GOOGLE_OAUTH_CLIENT_ID",     "google-oauth-client-id-staging"),
    # ("API_Server/.env",       "GOOGLE_OAUTH_CLIENT_SECRET", "google-oauth-client-secret-staging"),
    # ("API_Server/.env",       "GOOGLE_OAUTH_REDIRECT_URI",  "google-oauth-redirect-uri-staging"),
]


def fetch_secret(secret_name: str) -> str:
    """Pull `latest` of a GCP Secret Manager secret without exposing stdout.

    `gcloud --out-file=PATH` writes the raw payload directly to PATH (no
    trailing newline added), so the value never crosses subprocess.PIPE as
    a stream. We then read the tempfile, decode UTF-8, overwrite with zeros,
    and unlink before returning.
    """
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
        # Best-effort hygiene: zero out the tempfile before unlinking so an
        # opportunistic tempdir scanner doesn't pick up stale plaintext.
        # On a single-user dev machine this is belt-and-suspenders rather
        # than a real defense.
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


def update_env_line(env_path: Path, var: str, value: str) -> str:
    """Replace VAR=... line in env_path, or append if missing.

    Atomic on POSIX (os.replace). On Windows the rename is atomic from the
    perspective of any reader holding the destination open, which matches
    git bash's behavior. Original line endings are preserved per-line so
    we don't churn CRLF/LF mixes that already live in the file.

    Returns 'replaced', 'appended', or 'unchanged'.
    """
    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(f"{var}={value}\n", encoding="utf-8")
        return "appended"

    text = env_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    new_line_no_eol = f"{var}={value}"
    found = False
    changed = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        # Match `VAR=` or `VAR =` at the start. Accept either since some
        # editors auto-format env files with spaces around =.
        if stripped.startswith(f"{var}=") or stripped.startswith(f"{var} ="):
            found = True
            if stripped == new_line_no_eol:
                break  # already correct
            eol = line[len(stripped):] or "\n"
            lines[i] = new_line_no_eol + eol
            changed = True
            break

    if not found:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(new_line_no_eol + "\n")
        changed = True

    if not changed:
        return "unchanged"

    # Write to a sibling tempfile and atomically replace -- avoids the
    # half-written window if the process is killed between truncate and
    # write. newline="" preserves whatever EOL bytes we just composed.
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8", newline="")
    os.replace(tmp, env_path)
    return "replaced" if found else "appended"


def main() -> int:
    failures = 0
    for rel_path, var, secret in MAPPING:
        env_path = REPO / rel_path
        try:
            value = fetch_secret(secret)
        except Exception as exc:
            print(f"[FAIL] {rel_path} :: {var} <- {secret}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if not value:
            print(
                f"[FAIL] {rel_path} :: {var} <- {secret}: empty payload",
                file=sys.stderr,
            )
            failures += 1
            continue
        action = update_env_line(env_path, var, value)
        # Print only metadata -- never the value, never even a prefix.
        # `len(value)` gives a sanity signal without leaking content.
        print(f"[{action}] {rel_path} :: {var} (from {secret}, {len(value)} chars)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
