import { test, chromium, Browser, BrowserContext, Page } from "@playwright/test";
import fs from "fs";
import path from "path";

// 30-second Kaggle demo recorder. Drives two BrowserContexts (alice/bob)
// against the real, locally-running stack and emits per-context webm
// videos + a markers.json that scripts/compose_demo_video.ps1 uses to
// cross-cut + excise LLM waits into the final mp4.
//
// Run via `playwright.record.config.ts`. See that file's header for the
// prerequisite checklist (seed, API_Server, Frontend dev, Modal warm-up).

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "tmp", "demo", "raw");
const MARKERS_PATH = path.join(REPO_ROOT, "tmp", "demo", "markers.json");
const API = process.env.DEMO_API_URL ?? "http://127.0.0.1:8000";
const WEB = process.env.DEMO_WEB_URL ?? "http://127.0.0.1:3000";
const ALICE = "alice@example.com";
const BOB = "bob@example.com";
const PASSWORD = "demo-take-five-9";

interface Marker {
  context: "alice" | "bob";
  scene: string;
  startMs: number;
  endMs: number;
  kind: "scene" | "wait";
}

const markers: Marker[] = [];
const ctxStart: Record<string, number> = {};

const elapsed = (ctx: "alice" | "bob") => Date.now() - ctxStart[ctx];

const markScene = (ctx: "alice" | "bob", scene: string, startMs: number, endMs: number) =>
  markers.push({ context: ctx, scene, startMs, endMs, kind: "scene" });

const markWait = (ctx: "alice" | "bob", scene: string, startMs: number, endMs: number) =>
  markers.push({ context: ctx, scene, startMs, endMs, kind: "wait" });

async function login(email: string): Promise<string> {
  // /api/v1/auth/login uses FastAPI's OAuth2PasswordRequestForm — must be
  // form-encoded with `username` (not `email`) + `password`.
  const body = new URLSearchParams({ username: email, password: PASSWORD });
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) {
    throw new Error(`login ${email} failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()).access_token as string;
}

async function newRecordContext(
  browser: Browser,
  token: string,
  name: "alice" | "bob",
): Promise<BrowserContext> {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const ctx = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: OUT_DIR, size: { width: 1920, height: 1080 } },
  });
  // window.fetch monkey-patch: Frontend uses a single build-time
  // NEXT_PUBLIC_DEV_TOKEN, but we want one Frontend serving both demo
  // users in parallel. Per-context Authorization override does that
  // without rebuilding the Next bundle.
  await ctx.addInitScript((t) => {
    const orig = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const headers = new Headers((init && init.headers) || {});
      headers.set("Authorization", `Bearer ${t}`);
      return orig(input, { ...(init || {}), headers });
    };
  }, token);
  ctxStart[name] = Date.now();
  return ctx;
}

async function showSubtitle(page: Page, html: string) {
  await page.evaluate((caption) => {
    let el = document.getElementById("__demo_subtitle__");
    if (!el) {
      el = document.createElement("div");
      el.id = "__demo_subtitle__";
      Object.assign(el.style, {
        position: "fixed",
        left: "0",
        right: "0",
        bottom: "40px",
        textAlign: "center",
        zIndex: "999999",
        pointerEvents: "none",
        font: "600 32px/1.3 system-ui, sans-serif",
        color: "#fff",
        textShadow:
          "0 2px 8px rgba(0,0,0,0.85), 0 0 4px rgba(0,0,0,0.85)",
        padding: "12px 24px",
      });
      document.body.appendChild(el);
    }
    el.innerHTML = caption;
  }, html);
}

async function clearSubtitle(page: Page) {
  await page.evaluate(() => {
    const el = document.getElementById("__demo_subtitle__");
    if (el) el.remove();
  });
}

async function fullScreenBlack(page: Page, html: string) {
  await page.goto("about:blank");
  await page.evaluate((caption) => {
    document.body.style.background = "#000";
    document.body.style.margin = "0";
    const el = document.createElement("div");
    Object.assign(el.style, {
      position: "fixed",
      inset: "0",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      color: "#fff",
      font: "600 56px/1.4 system-ui, sans-serif",
      textAlign: "center",
      padding: "0 120px",
    });
    el.innerHTML = caption;
    document.body.appendChild(el);
  }, html);
}

test("record 30-second demo", async ({}, testInfo) => {
  testInfo.setTimeout(5 * 60_000);
  fs.mkdirSync(path.dirname(MARKERS_PATH), { recursive: true });

  const browser = await chromium.launch({ headless: false });

  const [aliceToken, bobToken] = await Promise.all([login(ALICE), login(BOB)]);

  const aliceCtx = await newRecordContext(browser, aliceToken, "alice");
  const bobCtx = await newRecordContext(browser, bobToken, "bob");
  const alice = await aliceCtx.newPage();
  // bob page is created lazily — when both contexts open simultaneously
  // in headed mode, the idle window tends to drop out before the scene
  // that uses it actually runs.
  let bob!: Page;
  const ensureBob = async () => {
    if (!bob || bob.isClosed()) bob = await bobCtx.newPage();
    return bob;
  };

  // ─── Scene 1 — Hook (0-5s) ────────────────────────────────────
  {
    const t0 = elapsed("alice");
    await fullScreenBlack(
      alice,
      "Teams don&rsquo;t want to teach automation.<br/>They want it to learn them.",
    );
    await alice.waitForTimeout(5000);
    markScene("alice", "scene1_hook", t0, elapsed("alice"));
  }

  // ─── Scene 2 — Marketplace (5-14s) ───────────────────────────
  // 2a: alice /skills — explicit "Team marketplace" section dwell so the
  // viewer sees the destination before bob's flow shows what it buys
  // them. Active count chip is the focal point.
  {
    const t0 = elapsed("alice");
    await alice.goto(`${WEB}/skills`);
    await alice.getByTestId("team-marketplace").waitFor({ timeout: 15_000 });
    await alice.waitForSelector("text=Notify finance on invoices", { timeout: 15_000 });
    await showSubtitle(
      alice,
      "alice&rsquo;s team marketplace &mdash; one active policy &rarr;",
    );
    await alice.waitForTimeout(3200);
    await clearSubtitle(alice);
    markScene("alice", "scene2a_skills", t0, elapsed("alice"));
  }
  // 2b: bob's first message — phrased so it overlaps with alice's
  // workspace skill `condition`, which lets the LLM recognise the
  // policy and surface it as a clarify option.
  {
    await ensureBob();
    const t0 = elapsed("bob");
    await bob.goto(`${WEB}/workflows/new`);
    await bob.getByTestId("toggle-ai-composer").click();
    await bob
      .getByTestId("chat-input")
      .fill("When a new invoice arrives in our shared inbox, notify the team.");
    await bob.getByRole("button", { name: "Send" }).click();
    markScene("bob", "scene2b_chat_send", t0, elapsed("bob"));

    // The LLM may answer either intent (Gemma is non-deterministic
    // here). Wait for whichever bubble shows up and branch:
    //   - clarify → render personalization options, then bob's second
    //     message fully specifies the workflow → draft
    //   - draft   → skip the second round, just apply
    const w0 = elapsed("bob");
    await Promise.race([
      bob.getByTestId("clarify-questions").waitFor({ timeout: 90_000 }),
      bob.getByTestId("proposed-summary").waitFor({ timeout: 90_000 }),
    ]);
    markWait("bob", "wait_compose_2_first", w0, elapsed("bob"));

    const isClarify = await bob
      .getByTestId("clarify-questions")
      .isVisible()
      .catch(() => false);

    const t1 = elapsed("bob");
    await showSubtitle(
      bob,
      isClarify
        ? "your team&rsquo;s policies <em>become</em> options"
        : "&rarr; draft from one line",
    );
    await bob.waitForTimeout(2200);
    await clearSubtitle(bob);
    markScene("bob", "scene2c_clarify_shown", t1, elapsed("bob"));

    if (isClarify) {
      const t2 = elapsed("bob");
      await bob
        .getByTestId("chat-input")
        .fill(
          "Use the 'Notify finance on invoices' skill. Poll https://api.acme.test/invoices every hour with http_request, then post a one-line summary to slack #finance.",
        );
      await bob.getByRole("button", { name: "Send" }).click();
      markScene("bob", "scene2d_pick", t2, elapsed("bob"));

      const w1 = elapsed("bob");
      await bob.getByTestId("proposed-summary").waitFor({ timeout: 90_000 });
      markWait("bob", "wait_compose_2_draft", w1, elapsed("bob"));
    }

    const t3 = elapsed("bob");
    await bob.getByTestId("apply-draft").click();
    await showSubtitle(bob, "&rarr; first draft, powered by alice");
    await bob.waitForTimeout(2000);
    await clearSubtitle(bob);
    markScene("bob", "scene2e_dag", t3, elapsed("bob"));
  }

  // ─── Scene 3 — Personalization (12-20s, alice only) ──────────
  // PR-G's LLM extract is non-deterministic across takes, so the
  // candidate is pre-seeded by `seed_demo_data.py` and Scene 3a is a
  // cosmetic "alice tweaks a node" beat — no actual save, no extract
  // round-trip. The activated candidate then drives Scenes 3d/3e and
  // Scene 4 (share).
  {
    const t0 = elapsed("alice");
    await alice.goto(`${WEB}/`);
    await alice.getByText("Invoice Pipeline").first().click();
    await alice
      .locator(".react-flow__node")
      .filter({ hasText: /http_request|fetch_invoices/ })
      .first()
      .click();
    await showSubtitle(alice, "your edits &rarr;");
    await alice.waitForTimeout(2200);
    await clearSubtitle(alice);
    markScene("alice", "scene3a_edit", t0, elapsed("alice"));

    // No save step — we're not waiting on a real diff-extract. Keep the
    // scene boundaries so the compositor's $order array still finds them.
    const t1 = elapsed("alice");
    markScene("alice", "scene3b_save", t1, elapsed("alice"));

    const w0 = elapsed("alice");
    await alice.goto(`${WEB}/skills`);
    await alice.getByTestId("your-patterns").waitFor({ timeout: 30_000 });
    await alice
      .locator('[data-testid^="suggested-row-"]')
      .first()
      .waitFor({ timeout: 30_000 });
    markWait("alice", "wait_skills_load", w0, elapsed("alice"));

    const t2 = elapsed("alice");
    await showSubtitle(alice, "your patterns &rarr;");
    const firstRow = alice.locator('[data-testid^="suggested-row-"]').first();
    const candidateId = (await firstRow.getAttribute("data-testid"))!.replace(
      "suggested-row-",
      "",
    );
    await alice.getByTestId(`suggested-activate-${candidateId}`).click();
    // Force a fresh fetch — React Query's prefix invalidate is in place
    // but the active query occasionally races the wait below in real
    // (non-mocked) backends. A reload makes the demo deterministic.
    await alice.waitForTimeout(800);
    await alice.reload();
    await alice.getByTestId("active-personal-section").waitFor({ timeout: 15_000 });
    await alice.waitForTimeout(1500);
    await clearSubtitle(alice);
    markScene("alice", "scene3c_activate", t2, elapsed("alice"));

    // Specific enough that the LLM goes straight to draft (avoids the
    // 2-round clarify path Scene 2 uses).
    const t3 = elapsed("alice");
    await alice.goto(`${WEB}/workflows/new`);
    await alice.getByTestId("toggle-ai-composer").click();
    await alice
      .getByTestId("chat-input")
      .fill("Build a workflow that polls https://example.com/data every 5 minutes and posts the result to slack #ops.");
    await alice.getByRole("button", { name: "Send" }).click();
    markScene("alice", "scene3d_chat_send", t3, elapsed("alice"));

    const w1 = elapsed("alice");
    await alice.getByTestId("proposed-summary").waitFor({ timeout: 90_000 });
    markWait("alice", "wait_compose_3", w1, elapsed("alice"));

    const t4 = elapsed("alice");
    await showSubtitle(alice, "&rarr; next draft");
    await alice.waitForTimeout(2000);
    await clearSubtitle(alice);
    markScene("alice", "scene3e_dag", t4, elapsed("alice"));
  }

  // ─── Scene 4 — Share (alice → team marketplace) ──────────────
  //
  // The promotion is the most narratively loaded beat in the take. The
  // refactored /skills page does the storytelling for us:
  //   - card visually flies UP from "Your patterns" toward the
  //     marketplace section (600ms CSS transform),
  //   - marketplace count chip pulses (parent triggers via callback),
  //   - a promotion banner replaces the row to name what happened.
  // We wait on the banner before clearing the subtitle so the camera
  // captures the whole cause→effect.
  {
    const t0 = elapsed("alice");
    await alice.goto(`${WEB}/skills`);
    await alice.getByTestId("active-personal-section").waitFor({ timeout: 15_000 });
    const activeRow = alice.locator('[data-testid^="active-personal-row-"]').first();
    const activeId = (await activeRow.getAttribute("data-testid"))!.replace(
      "active-personal-row-",
      "",
    );
    await showSubtitle(alice, "share what you discovered &rarr;");
    await alice.waitForTimeout(800);
    await alice.getByTestId(`active-personal-share-${activeId}`).click();
    // Banner only renders after the flying animation finishes AND the
    // share API succeeds (whichever is later). The Modal-backed share
    // call can take ~3s warm, so give the banner generous headroom.
    await alice
      .getByTestId("promotion-banner")
      .waitFor({ timeout: 15_000 })
      .catch(() => {
        // Banner is best-effort cosmetic; the marketplace count + new row
        // already tell the story even if the banner missed its window.
      });
    await showSubtitle(alice, "&rarr; promoted to the team marketplace");
    await alice.waitForTimeout(2500);
    await clearSubtitle(alice);
    markScene("alice", "scene4a_share", t0, elapsed("alice"));

    await ensureBob();
    const t1 = elapsed("bob");
    await bob.goto(`${WEB}/workflows/new`);
    await bob.getByTestId("toggle-ai-composer").click();
    // Specific phrasing → straight to draft. retry_interval=300 is the
    // pattern alice shared via Track C — if it shows up in the DAG
    // config, the cross-user lift is live.
    await bob
      .getByTestId("chat-input")
      .fill("Build a webhook listener that on receive calls https://api.example.com/data with HTTP retry and notifies slack #alerts.");
    await bob.getByRole("button", { name: "Send" }).click();
    markScene("bob", "scene4b_chat_send", t1, elapsed("bob"));

    const w0 = elapsed("bob");
    await bob.getByTestId("proposed-summary").waitFor({ timeout: 90_000 });
    markWait("bob", "wait_compose_4", w0, elapsed("bob"));

    const t2 = elapsed("bob");
    await showSubtitle(bob, "&rarr; lift the team");
    await bob.waitForTimeout(2000);
    await clearSubtitle(bob);
    markScene("bob", "scene4c_dag", t2, elapsed("bob"));
  }

  // ─── Scene 5 — Close (27-30s) ────────────────────────────────
  {
    const t0 = elapsed("alice");
    await fullScreenBlack(
      alice,
      "<div>Skills marketplace &middot; Personalization &middot; Team lift</div>" +
        "<div style='font-size:28px; opacity:0.7; margin-top:24px'>auto_workflow_demo &middot; Gemma 4 hackathon</div>",
    );
    await alice.waitForTimeout(3000);
    markScene("alice", "scene5_close", t0, elapsed("alice"));
  }

  await aliceCtx.close();
  await bobCtx.close();
  await browser.close();

  // Playwright names webms with random hex; tag them by open order
  // (alice opened first → older mtime) so the compositor can find them.
  const files = fs
    .readdirSync(OUT_DIR)
    .filter((f) => f.endsWith(".webm") && f !== "alice.webm" && f !== "bob.webm")
    .map((f) => ({ name: f, mtime: fs.statSync(path.join(OUT_DIR, f)).mtimeMs }))
    .sort((a, b) => a.mtime - b.mtime)
    .slice(-2);
  if (files.length === 2) {
    for (const target of ["alice.webm", "bob.webm"]) {
      const dest = path.join(OUT_DIR, target);
      if (fs.existsSync(dest)) fs.unlinkSync(dest);
    }
    fs.renameSync(path.join(OUT_DIR, files[0].name), path.join(OUT_DIR, "alice.webm"));
    fs.renameSync(path.join(OUT_DIR, files[1].name), path.join(OUT_DIR, "bob.webm"));
  }

  fs.writeFileSync(
    MARKERS_PATH,
    JSON.stringify(
      {
        contexts: {
          alice: { recordingStartMs: ctxStart.alice },
          bob: { recordingStartMs: ctxStart.bob },
        },
        markers,
      },
      null,
      2,
    ),
  );

  console.log(`\nRaw webms : ${OUT_DIR}`);
  console.log(`Markers   : ${MARKERS_PATH}`);
  console.log(`Next      : pwsh scripts/compose_demo_video.ps1\n`);
});
