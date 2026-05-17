# document_parser — measured findings (2026-05-07)

> Facts confirmed by feeding the actual PDF through the parser during the
> W3-2 fixture verification step (`tests/fixtures/gitlab_handbook_excerpt.pdf`,
> GitLab handbook L&R page, 18.5 KB raw md → 8-page PDF, 256 KB).
> **Reopen this when the parser is being hardened.**

## Adopted decision (option A)

At the current stage there is **no post-hoc noise filtering** — even when chromium's print
banner gets embedded into chunk bodies, leave it alone. Reasons:

- Noise ratio ~5% (measured on 1 fixture, capped by the automatic gate at < 10%)
- The LLM stage (`policy_extract`) is likely to ignore short English lines like
  "Licensing & Renewals" naturally. Adding heuristics before verification risks
  overfitting to a single fixture
- We need to see other PDFs (scans, multi-column, tables) before a real heuristic shape emerges
- Building a sanitize rule from 1 fixture would more likely damage other documents' real body text

## Key findings

### 1. chromium print banner is embedded inside the body

Headless chromium's `--print-to-pdf` defaults to **page headers/footers ON**.
The top of each page contains `5/5/26, 6:40 PM Licensing & Renewals` (date + page title),
and the bottom contains `file:///C:/Users/.../tmpXXXXX.html N/8` (URL + page number).
pypdf's text extraction cannot tell these banners from the body and scoops them in →
they are embedded in chunks #2/#4/#5/#7, etc.

**Current guard**: `test_real_handbook_browser_print_noise_is_bounded` —
enforces the noise ratio < 10% via `file:///` occurrences × estimated 80 chars.

**Hardening proposals**:
- regex-based post-strip of the page header/footer pattern (`<date> <page_title>` / `<url> N/M`)
- use pypdf's `mediabox` coordinates to peel off text in the top/bottom ~50pt regions
- regenerate the fixture with `--no-pdf-header-footer` and keep a noise-free ground truth
  separately (real-world fixture vs. clean fixture, two tracks)

### 2. Host OS locale leaks into the fixture

Running chromium plainly on a ko-KR locale host writes the banner date as
`26. 5. 5. 6:36` + `���` (Korean "year month day" → non-ASCII glyphs → pypdf
decode failure). Since this fixture is meant to simulate Kaggle's English
environment, the fix is to **force `--lang=en-US` + `LANG=en_US.UTF-8` env
override** (see `scripts/generate_handbook_fixture.py`).

**Hardening proposals**:
- Generate the fixture in a build environment like Modal so host locale has 0 influence.
  Move it to a one-shot CI/Modal script
- Users' real PDFs may also break the same way when printed on a ko-KR locale →
  inspect the PDF's `/Producer` metadata / text decoding at upload time and warn early on
  locale-corrupted cases

### 3. Markdown link URLs disappear at the PDF stage

`[a decision was made](https://gitlab.com/.../issues/96)` becomes only "a decision was made"
in the chromium PDF body; the URL is hover-only and goes away. pypdf extraction shows no
URL either. That is, `policy_extract` does not receive the source URL.

**Current policy**: the link text is preserved in the body, so the semantic loss is small.
The URL can be restored adequately by a fixture-level citation under
ADR-022 `source_kind` = `policy_doc` (chunk N → the source page's URL itself).

**Hardening proposals**:
- At the markdown → HTML step, inline the `<a href>` URL into the body
  (e.g., "a decision was made (https://...)") — possible as a markdown extension
- However, real-world handbook PDFs are usually exported from Word / Google Docs, so whether
  the URL inlines depends on the source environment. Hard to generalize

### 4. Paragraph snap works well, but in some cases the chunk boundary cuts through a quote

Most chunks snap cleanly to blank-line (`\n\n`) boundaries, but if the last 1/4 of the
800-char window contains no blank line, the snap falls back to char-level and may cut a word
mid-token (e.g., chunk #1 starts with `l priorities prevented...` — the leftover from the
prior chunk's "business-critica").

**Current policy**: 100-char overlap carry-over offsets word damage at retrieval time.
Embedding is per-chunk anyway, so the semantic damage is small.

**Hardening proposals**:
- Widen the snap window from 1/4 → 1/3, or fall back to a sentence boundary (`. `) when
  there is no blank line
- BPE-aware chunking (BGE-M3 tokenizer) — make boundaries fall exactly on token boundaries

## Hardening triggers

Re-open this document and re-prioritize the items above when any of these occurs:

1. Banner text (e.g., reusing a page title like `Licensing & Renewals`) appears in
   `policy_extract`'s output as a skill description
2. A user-uploaded real PDF (scan / multi-column / table-heavy / non-Latin) breaks the
   current chunking
3. Retrieval recall@5 drops below 60% even on the fixture → suspect the chunking itself
4. The langgraph migration (PLAN_13) triggers a wrong-regression in the "skill re-extract"
   loop because of noisy chunks

## References

- `scripts/generate_handbook_fixture.py` — fixture build (chromium en-US forced)
- `scripts/dump_handbook_parse.py --noise` — dump only the banner-tainted chunks
- `tests/test_document_parser.py` — 7 real-handbook assertions (guard the trade-offs above)
- `tests/fixtures/NOTICE.md` — fixture source/license (MIT)
- ADR-022 `source_kind = policy_doc` — source-URL restoration policy
