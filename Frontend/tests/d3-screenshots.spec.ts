import { test } from "@playwright/test";
import path from "path";
import fs from "fs";

// D3 demo-evidence screenshot capture (PLAN_12 W3 wrap-up).
//
// Walks the Persona A wizard through the doc-paste → reflective extract →
// review path with mocked /skills/* responses, saving a PNG at each
// phase under `docs/demo/d3_smoke/screenshots/`. The mocked candidates
// mirror the shape live Modal returns so the screenshots double as the
// hackathon submission's visual narrative when paired with the live
// NDJSON captures alongside.
//
// Run after `pnpm dev` is up on :3000 (Playwright's webServer hook will
// start one if not). `npx playwright test tests/d3-screenshots.spec.ts`
// regenerates the PNGs.

const OUT_DIR = path.resolve(__dirname, "..", "..", "docs", "demo", "d3_smoke", "screenshots");

const PASTED_TEXT = `Refunds within 30 days of delivery, full price minus original
shipping. Defective items get full refund including shipping if
flagged within 7 days. Restocking fee 15% on opened non-defective
returns. Same-SKU exchanges free both directions; different-SKU
processed as refund + rebuy.`;

const CANDIDATES = [
  {
    name: "Refund window",
    description: "30-day refund window from delivery date.",
    condition: "Customer requests refund within 30 days of delivery",
    action: "Approve at full price minus original shipping",
    rationale: "",
    needs_clarification: false,
    clarification_hint: "",
  },
  {
    name: "Defective item refund",
    description: "DOA flagged within 7 days = full refund.",
    condition: "Item arrives defective AND customer flags within 7 days",
    action: "Refund full purchase price including shipping",
    rationale: "",
    needs_clarification: false,
    clarification_hint: "",
  },
  {
    name: "Restocking fee",
    description: "15% on opened non-defective returns.",
    condition: "Return is opened AND not defective",
    action: "Charge 15% restocking fee on item subtotal",
    rationale: "",
    needs_clarification: false,
    clarification_hint: "",
  },
  {
    name: "Same-SKU exchange",
    description: "Free shipping both directions for size/color swaps.",
    condition: "Exchange request keeps the same SKU within 30 days",
    action: "Cover both return shipping and replacement shipping",
    rationale: "",
    needs_clarification: false,
    clarification_hint: "",
  },
];

const AGENT_TRACE = {
  iterations: [
    {
      drafts: [CANDIDATES[0]],
      eval: {
        decision: "retry",
        coverage_concerns: [
          "Defective-item rule not captured",
          "Restocking fee not captured",
          "Exchange-shipping coverage not captured",
        ],
        schema_issues: [],
        rationale: "Iter 1 caught the headline 30-day rule but missed three sibling rules.",
      },
      prompt_hint: "",
    },
    {
      drafts: CANDIDATES,
      eval: {
        decision: "converge",
        coverage_concerns: [],
        schema_issues: [],
        rationale: "All four rules captured.",
      },
      prompt_hint: "Capture every distinct conditional in the chunk, not just the lead rule.",
    },
  ],
  terminated: true,
  reason: "converge",
};

const SKILL_DRAFTS = CANDIDATES.map((c, i) => ({
  skill_id: `00000000-0000-0000-0000-${(i + 1).toString().padStart(12, "0")}`,
  name: c.name,
  description: c.description,
  condition: c.condition,
  action: c.action,
}));

test("D3 demo: capture wizard phase screenshots end-to-end", async ({ page }) => {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  // Mock the reflective extract endpoint with the four-candidate ecommerce
  // narrative plus a two-iteration trace.
  await page.route("**/api/v1/skills/extract", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        candidates: CANDIDATES,
        agent_trace: AGENT_TRACE,
        langsmith_run_id: "demo-run-d3-ecommerce",
      }),
    }),
  );

  // Bootstrap returns no remaining gaps — the four extracted candidates
  // covered everything for this demo so we land straight on the review
  // phase with four skill cards.
  let answersCalls = 0;
  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        // No remaining gaps — every candidate was approved by the user
        // already; the wizard short-circuits to the review phase using
        // the persisted skills the bootstrap response includes.
        missing: [],
        // The bootstrap path with extracted_skills inlines the persisted
        // drafts so the review surface has cards to show without a
        // round-trip per skill. (Mirrors the API_Server response shape
        // in this demo path.)
        drafts: SKILL_DRAFTS,
      }),
    });
  });

  // /answers is unused on the doc-extract path (no remaining gaps) but
  // we stub it anyway so a stray call doesn't 404 and break navigation.
  await page.route("**/api/v1/skills/answers", (route) => {
    answersCalls += 1;
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: route.request().postDataJSON().session_id,
        skill_id: "unused",
        draft: { name: "unused", description: "", condition: "", action: "", rationale: "", needs_clarification: false, clarification_hint: "" },
      }),
    });
  });

  await page.goto("/skills/new");

  // 01 — domain picker (first phase the user ever sees).
  await page.screenshot({
    path: path.join(OUT_DIR, "01_domain_picker.png"),
    fullPage: true,
  });

  await page.getByTestId("domain-chip-ecommerce").click();

  // 02 — doc-choice screen with empty textarea, both buttons offered.
  await page.screenshot({
    path: path.join(OUT_DIR, "02_doc_choice_empty.png"),
    fullPage: true,
  });

  await page.getByTestId("doc-paste-input").fill(PASTED_TEXT);

  // 03 — doc-choice with policy text pasted, character counter visible.
  await page.screenshot({
    path: path.join(OUT_DIR, "03_doc_choice_pasted.png"),
    fullPage: true,
  });

  await page.getByTestId("doc-extract").click();
  await page.getByTestId("extract-review").waitFor();

  // 04 — extract-review with four default-selected candidates.
  await page.screenshot({
    path: path.join(OUT_DIR, "04_extract_review.png"),
    fullPage: true,
  });

  // 05 — agent_trace toggle expanded, showing iter 1 retry → iter 2 converge.
  await page.getByTestId("agent-trace-toggle").click();
  await page.getByTestId("agent-trace-detail").waitFor();
  await page.screenshot({
    path: path.join(OUT_DIR, "05_agent_trace_expanded.png"),
    fullPage: true,
  });

  // The bootstrap response has `missing: []` so Continue lands on the
  // no-gaps banner immediately; we don't take a separate screenshot of
  // that since it's not the demo's centerpiece. The four candidates the
  // user just reviewed ARE the deliverable.

  // Sanity: /answers should never have been invoked on this path.
  if (answersCalls !== 0) {
    throw new Error(`expected 0 /answers calls, got ${answersCalls}`);
  }
});
