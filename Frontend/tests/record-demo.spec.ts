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
    let wrapper = document.getElementById("__demo_subtitle__");
    let inner: HTMLElement;
    if (!wrapper) {
      wrapper = document.createElement("div");
      wrapper.id = "__demo_subtitle__";
      Object.assign(wrapper.style, {
        position: "fixed",
        left: "0",
        right: "0",
        bottom: "80px",
        textAlign: "center",
        zIndex: "999999",
        pointerEvents: "none",
      });
      inner = document.createElement("span");
      inner.id = "__demo_subtitle_inner__";
      Object.assign(inner.style, {
        display: "inline-block",
        font: "600 44px/1.3 system-ui, sans-serif",
        color: "#fff",
        background: "rgba(0,0,0,0.72)",
        padding: "16px 32px",
        borderRadius: "16px",
        textShadow:
          "0 2px 8px rgba(0,0,0,0.85), 0 0 4px rgba(0,0,0,0.85)",
        maxWidth: "85vw",
      });
      wrapper.appendChild(inner);
      document.body.appendChild(wrapper);
    } else {
      inner = document.getElementById("__demo_subtitle_inner__")!;
    }
    inner.innerHTML = caption;
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
  testInfo.setTimeout(15 * 60_000);  // 4 LLM compose calls × up to 3min cold + scene dwells
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
    if (!bob || bob.isClosed()) {
      bob = await bobCtx.newPage();
      // Critical: Playwright's per-context webm timestamp starts at the
      // first newPage(), not at ctx creation. Without this reset,
      // elapsed("bob") (measured from ctxStart, set in newRecordContext)
      // is ~10-12 s ahead of bob.webm's timeline, and compose_demo_video
      // cuts the wrong raw frames (bob scenes look like the next scene's
      // post-LLM state instead of their own input/dwell content).
      ctxStart["bob"] = Date.now();
    }
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
    // Pre-scene navigate is folded into a wait marker so the page-load
    // flicker (white screen → loading → content) is cut from final.mp4.
    const w0a = elapsed("alice");
    await alice.goto(`${WEB}/skills`);
    await alice.getByTestId("team-marketplace").waitFor({ timeout: 15_000 });
    await alice.waitForSelector("text=Notify finance on invoices", { timeout: 15_000 });
    markWait("alice", "wait_alice_nav_skills_1", w0a, elapsed("alice"));

    const t0 = elapsed("alice");
    await showSubtitle(
      alice,
      "This is alice&rsquo;s team marketplace, with one active policy that everyone benefits from.",
    );
    // 6.5 s — TTS narration for this sentence is 5.7 s, so we need at
    // least 6.2 s of dwell + a 0.5 s tail before the scene transitions.
    await alice.waitForTimeout(6500);
    markScene("alice", "scene2a_skills", t0, elapsed("alice"));
    // subtitle persists — next scene runs on bob (different page DOM)
  }
  // 2b: bob's first message — phrased so it overlaps with alice's
  // workspace skill `condition`, which lets the LLM recognise the
  // policy and surface it as a clarify option.
  {
    await ensureBob();
    // Navigate + composer-ready wait folded into markWait → cut from final.
    const w0b = elapsed("bob");
    await bob.goto(`${WEB}/workflows/new`);
    await bob.getByTestId("toggle-ai-composer").click();
    await bob.getByTestId("chat-input").waitFor({ timeout: 15_000 });
    markWait("bob", "wait_bob_nav_compose_1", w0b, elapsed("bob"));

    const t0 = elapsed("bob");
    // Subtitle goes up BEFORE the input action so viewers can read it
    // while bob is typing + sending. Stays up until next scene overwrites.
    await showSubtitle(bob, "Bob asks for an invoice flow in his own words.");
    await bob
      .getByTestId("chat-input")
      .fill("When a new invoice arrives in our shared inbox, notify the team.");
    await bob.getByRole("button", { name: "Send" }).click();
    await bob.waitForTimeout(4500);  // ≥5s scene total
    markScene("bob", "scene2b_chat_send", t0, elapsed("bob"));
    // subtitle persists into the LLM wait + next scene's showSubtitle takes over

    // The LLM may answer either intent (Gemma is non-deterministic
    // here). Wait for whichever bubble shows up and branch:
    //   - clarify → render personalization options, then bob's second
    //     message fully specifies the workflow → draft
    //   - draft   → skip the second round, just apply
    const w0 = elapsed("bob");
    await Promise.race([
      bob.getByTestId("clarify-questions").waitFor({ timeout: 180_000 }),
      bob.getByTestId("proposed-summary").waitFor({ timeout: 180_000 }),
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
        ? "Alice&rsquo;s team policy shows up as a clarify option, so bob can just pick it."
        : "A complete draft from a single line.",
    );
    // 7s — viewer reads the subtitle AND the actual option text on screen.
    await bob.waitForTimeout(7000);
    markScene("bob", "scene2c_clarify_shown", t1, elapsed("bob"));
    // subtitle persists into scene2d/2e

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
      await bob.getByTestId("proposed-summary").waitFor({ timeout: 180_000 });
      markWait("bob", "wait_compose_2_draft", w1, elapsed("bob"));
    }

    const t3 = elapsed("bob");
    // Subtitle BEFORE apply click so it's already up when the DAG flies in.
    await showSubtitle(bob, "Bob applies the draft, and the workflow is composed automatically.");
    await bob.getByTestId("apply-draft").click();
    await bob.waitForTimeout(5000);  // ≥5s, viewer reads + sees DAG
    markScene("bob", "scene2e_dag", t3, elapsed("bob"));
    // subtitle persists — alice's next scene runs in a different context
  }

  // ─── Scene 3 — Personalization (12-20s, alice only) ──────────
  // PR-G's LLM extract is non-deterministic across takes, so the
  // candidate is pre-seeded by `seed_demo_data.py` and Scene 3a is a
  // cosmetic "alice tweaks a node" beat — no actual save, no extract
  // round-trip. The activated candidate then drives Scenes 3d/3e and
  // Scene 4 (share).
  {
    // Double-navigate (home → workflow page) + canvas render wait folded
    // into markWait → the flicker the user spotted at 26-27s is cut.
    const w0a = elapsed("alice");
    await alice.goto(`${WEB}/`);
    await alice.getByText("Invoice Pipeline").first().click();
    await alice.locator(".react-flow__node").first().waitFor({ timeout: 15_000 });
    markWait("alice", "wait_alice_nav_workflow", w0a, elapsed("alice"));

    const t0 = elapsed("alice");
    // Subtitle goes up on a stable workflow page; viewer sees the caption
    // while the node is being highlighted + the properties panel appears.
    await showSubtitle(alice, "Now alice opens her own workflow and tweaks a node.");
    await alice
      .locator(".react-flow__node")
      .filter({ hasText: /http_request|fetch_invoices/ })
      .first()
      .click();
    await alice.waitForTimeout(5000);  // ≥5s stable dwell
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
    const activateCaption =
      "The system noticed her edit, and alice activates it as her personal pattern.";
    await showSubtitle(alice, activateCaption);
    const firstRow = alice.locator('[data-testid^="suggested-row-"]').first();
    const candidateId = (await firstRow.getAttribute("data-testid"))!.replace(
      "suggested-row-",
      "",
    );
    await alice.getByTestId(`suggested-activate-${candidateId}`).click();

    // Trust React Query's invalidate first — the user sees a smooth
    // in-place transition (suggested row vanishes, active-personal-section
    // appears) without the jarring full-page reload. If the refetch
    // races (PR-J SuggestedFromEdits invalidate sometimes misses in
    // real backends), fall back to an explicit reload.
    const sawActive = await alice
      .getByTestId("active-personal-section")
      .waitFor({ timeout: 8_000 })
      .then(() => true)
      .catch(() => false);

    if (!sawActive) {
      await alice.reload();
      await alice.getByTestId("active-personal-section").waitFor({ timeout: 15_000 });
      // page.reload() wipes the subtitle DOM — re-inject so the caption
      // persists for the rest of the scene.
      await showSubtitle(alice, activateCaption);
    }

    await alice.waitForTimeout(4000);  // ≥5s scene total
    markScene("alice", "scene3c_activate", t2, elapsed("alice"));

    // Specific enough that the LLM goes straight to draft (avoids the
    // 2-round clarify path Scene 2 uses).
    // Navigate + composer-ready wait folded into markWait → cut from final.
    const w3 = elapsed("alice");
    await alice.goto(`${WEB}/workflows/new`);
    await alice.getByTestId("toggle-ai-composer").click();
    await alice.getByTestId("chat-input").waitFor({ timeout: 15_000 });
    markWait("alice", "wait_alice_nav_compose", w3, elapsed("alice"));

    const t3 = elapsed("alice");
    // Subtitle BEFORE the fill so it stays up while alice types + sends.
    await showSubtitle(alice, "Then alice asks for her own polling workflow.");
    await alice
      .getByTestId("chat-input")
      .fill("Build a workflow that polls https://example.com/data every 5 minutes and posts the result to slack #ops.");
    await alice.getByRole("button", { name: "Send" }).click();
    await alice.waitForTimeout(4500);  // ≥5s scene total
    markScene("alice", "scene3d_chat_send", t3, elapsed("alice"));

    const w1 = elapsed("alice");
    await alice.getByTestId("proposed-summary").waitFor({ timeout: 180_000 });
    markWait("alice", "wait_compose_3", w1, elapsed("alice"));

    const t4 = elapsed("alice");
    await showSubtitle(alice, "And gets a draft tailored to her active pattern.");
    // Apply the draft so the DAG actually lays out on the canvas — that
    // visual change is what makes "alice built a workflow" land for the
    // viewer (without apply, the scene is just the AI panel + an idle
    // empty canvas).
    await alice.getByTestId("apply-draft").click();
    await alice.waitForTimeout(5000);  // ≥5s, viewer reads + sees DAG
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
    // Navigate + active-section wait folded into markWait → cut from final.
    const w0share = elapsed("alice");
    await alice.goto(`${WEB}/skills`);
    await alice.getByTestId("active-personal-section").waitFor({ timeout: 15_000 });
    const activeRow = alice.locator('[data-testid^="active-personal-row-"]').first();
    const activeId = (await activeRow.getAttribute("data-testid"))!.replace(
      "active-personal-row-",
      "",
    );
    markWait("alice", "wait_alice_nav_skills_share", w0share, elapsed("alice"));

    const t0 = elapsed("alice");
    // Single unified subtitle for the entire share climax — the visual
    // story (card fly-up + count pulse + banner + new marketplace row)
    // unfolds across the whole 10s while the same caption stays on screen.
    await showSubtitle(
      alice,
      "Watch alice promote her pattern.<br/>It lands in the team marketplace, ready for the whole team.",
    );
    await alice.waitForTimeout(2000);  // viewer reads the caption first
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
    // 7s post-banner dwell — the share climax is the demo narrative
    // centerpiece; viewer needs time to register the card moved AND see
    // the marketplace count + new row settled.
    await alice.waitForTimeout(7000);
    markScene("alice", "scene4a_share", t0, elapsed("alice"));

    await ensureBob();
    // Navigate + composer-ready wait folded into markWait → cut from final.
    const w1 = elapsed("bob");
    await bob.goto(`${WEB}/workflows/new`);
    await bob.getByTestId("toggle-ai-composer").click();
    await bob.getByTestId("chat-input").waitFor({ timeout: 15_000 });
    markWait("bob", "wait_bob_nav_compose_2", w1, elapsed("bob"));

    const t1 = elapsed("bob");
    // Specific phrasing → straight to draft. retry_interval=300 is the
    // pattern alice shared via Track C — if it shows up in the DAG
    // config, the cross-user lift is live.
    // Subtitle BEFORE the fill so it stays up while bob types + sends.
    await showSubtitle(bob, "Bob asks for something new, with alice&rsquo;s pattern already in the mix.");
    await bob
      .getByTestId("chat-input")
      .fill("Build a webhook listener that on receive calls https://api.example.com/data with HTTP retry and notifies slack #alerts.");
    await bob.getByRole("button", { name: "Send" }).click();
    await bob.waitForTimeout(4500);  // ≥5s scene total
    markScene("bob", "scene4b_chat_send", t1, elapsed("bob"));

    const w0 = elapsed("bob");
    await bob.getByTestId("proposed-summary").waitFor({ timeout: 180_000 });
    markWait("bob", "wait_compose_4", w0, elapsed("bob"));

    const t2 = elapsed("bob");
    await showSubtitle(bob, "The whole team gets lifted, every time someone shares.");
    // Apply the draft so the DAG actually lays out on the canvas (same
    // reasoning as scene3e — without apply, the cross-user lift story
    // never reaches a visible payoff on bob's canvas).
    await bob.getByTestId("apply-draft").click();
    await bob.waitForTimeout(5000);  // ≥5s
    markScene("bob", "scene4c_dag", t2, elapsed("bob"));
  }

  // ─── Scene 5 — Close ────────────────────────────────────────
  {
    const t0 = elapsed("alice");
    await fullScreenBlack(
      alice,
      "<div>Skills marketplace, personalization, team lift.</div>" +
        "<div style='font-size:32px; opacity:0.75; margin-top:28px'>Built on Gemma 4 for the hackathon.</div>",
    );
    await alice.waitForTimeout(5000);
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
