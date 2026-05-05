"""Generate the GitLab handbook PDF fixture for document_parser tests.

This is a one-shot script run by hand when we want to regenerate the fixture
PDF from the upstream GitLab handbook page. The output is committed under
``tests/fixtures/`` so test runs do not need network access or chromium.

Source: ``content/handbook/support/license-and-renewals/_index.md`` from
``gitlab-com/content-sites/handbook`` (MIT-licensed — see fixture NOTICE.md).
We pick this page because it carries substantive subscription/license
policy text (~18 KB) with cross-references and external links, exercising
the real markdown noise our parser needs to handle.

Pipeline:
    raw md  →  HTML (python-markdown)  →  Edge/Chrome headless print-to-PDF

We deliberately leave the chromium default print headers/footers (URL,
date, page number) ON — that noise IS the realistic real-world simulation.
A pandoc-LaTeX clean PDF would not exercise it.

Usage:
    python scripts/generate_handbook_fixture.py

Requires the one-off ``markdown`` package (``pip install markdown``) which
is NOT a runtime dependency of the AI_Agent service — only the script.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import markdown

REPO = "gitlab-com/content-sites/handbook"
PAGE_PATH = "content/handbook/support/license-and-renewals/_index.md"
RAW_URL = f"https://gitlab.com/{REPO}/-/raw/main/{PAGE_PATH}"
COMMITS_API = (
    f"https://gitlab.com/api/v4/projects/{REPO.replace('/', '%2F')}"
    f"/repository/commits?path={PAGE_PATH}&per_page=1"
)
LICENSE_URL = f"https://gitlab.com/{REPO}/-/raw/main/LICENSE"

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PDF_OUT = FIXTURE_DIR / "gitlab_handbook_excerpt.pdf"
NOTICE_OUT = FIXTURE_DIR / "NOTICE.md"

# Real-world print stylesheet — neutral typography + visible link underlines
# so chromium's PDF rendering stays close to what an L&R staffer would see.
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font-family: Georgia, serif; max-width: 720px; margin: 2em auto;
            line-height: 1.5; color: #222; }}
    h1, h2, h3, h4 {{ font-family: Helvetica, Arial, sans-serif;
                       color: #1a1a1a; margin-top: 1.4em; }}
    a {{ color: #1f6feb; text-decoration: underline; }}
    code {{ background: #f3f3f3; padding: 1px 4px; border-radius: 3px; }}
    blockquote {{ border-left: 3px solid #ccc; margin-left: 0;
                   padding-left: 1em; color: #555; }}
    ul, ol {{ padding-left: 1.4em; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def _find_chromium() -> str:
    """Return the first chromium-family executable we can find on PATH/Windows."""
    for cand in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if Path(cand).exists():
            return cand
    found = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    if found:
        return found
    raise RuntimeError(
        "No chromium-family browser found. Install Edge or Chrome, or set PATH."
    )


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _fetch_commit_id() -> tuple[str, str]:
    """Return (commit_short_sha, committed_date_iso) for the page's last commit."""
    import json

    data = json.loads(_fetch(COMMITS_API))
    return data[0]["id"][:12], data[0]["committed_date"]


def _md_title(md_text: str) -> str:
    """Pull the front-matter title (between leading --- markers) for <title>."""
    if not md_text.startswith("---"):
        return "GitLab Handbook Excerpt"
    end = md_text.find("---", 3)
    if end == -1:
        return "GitLab Handbook Excerpt"
    for line in md_text[3:end].splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip()
    return "GitLab Handbook Excerpt"


def _strip_frontmatter(md_text: str) -> str:
    if not md_text.startswith("---"):
        return md_text
    end = md_text.find("---", 3)
    return md_text[end + 3 :].lstrip() if end != -1 else md_text


def _render_pdf(html_path: Path, pdf_path: Path) -> None:
    chromium = _find_chromium()
    # NOTE: we deliberately omit --no-pdf-header-footer. The default print
    # banners (URL/date/page number) are part of the realistic noise we want
    # the parser to handle, since real handbook PDFs printed from a browser
    # carry the same banners.
    #
    # Force en-US for the print banner date format. This fixture targets
    # Kaggle judges and downstream LLMs that expect English text — a host
    # locale of ko-KR otherwise emits "26. 5. 5." with non-ASCII glyphs
    # for "년 월 일" that pypdf decodes as replacement chars.
    env = {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
    subprocess.run(
        [
            chromium,
            "--headless",
            "--disable-gpu",
            "--lang=en-US",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        check=True,
        capture_output=True,
        env=env,
    )


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] fetching {RAW_URL}")
    md_text = _fetch(RAW_URL)
    print(f"      {len(md_text):,} bytes")

    print(f"[2/5] fetching commit metadata")
    commit_sha, committed_date = _fetch_commit_id()
    print(f"      commit {commit_sha} @ {committed_date}")

    print(f"[3/5] fetching LICENSE")
    license_text = _fetch(LICENSE_URL)

    title = _md_title(md_text)
    body = markdown.markdown(
        _strip_frontmatter(md_text),
        extensions=["extra", "sane_lists"],
    )
    html = HTML_TEMPLATE.format(title=title, body=body)

    print(f"[4/5] rendering PDF via chromium headless")
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(html)
        tmp_path = Path(tmp.name)
    try:
        _render_pdf(tmp_path, PDF_OUT)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"      wrote {PDF_OUT.relative_to(Path.cwd())} "
          f"({PDF_OUT.stat().st_size:,} bytes)")

    print(f"[5/5] writing NOTICE.md")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    NOTICE_OUT.write_text(
        f"""# Fixture Source Attribution

## gitlab_handbook_excerpt.pdf

- **Source page**: <{RAW_URL}>
- **Repository**: <https://gitlab.com/{REPO}>
- **Page commit at conversion**: `{commit_sha}` ({committed_date})
- **Converted on**: {generated_at}
- **Pipeline**: raw md → python-markdown (extra+sane_lists) → chromium headless print-to-PDF (default headers/footers retained)

## License

The upstream repository is MIT-licensed. The full LICENSE text is reproduced
below per the MIT requirement to include the copyright notice and permission
notice in all copies or substantial portions.

```
{license_text}
```
""",
        encoding="utf-8",
    )
    print(f"      wrote {NOTICE_OUT.relative_to(Path.cwd())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
