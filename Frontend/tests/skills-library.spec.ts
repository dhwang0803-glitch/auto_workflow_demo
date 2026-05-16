import { test, expect } from "@playwright/test";

// Skill library mock smoke (PLAN_12 W2-9).
//
// /skills lists active skills by default with status tabs for
// pending_review / rejected / archived. SkillRecord shape mirrors
// API_Server's SkillResponse; condition / action are JSONB dicts wrapped
// as { text: "..." } by the wizard.

const ACTIVE_SKILLS = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "ecommerce.refund_threshold_skill",
    description: "Refunds above $500 require manager approval.",
    condition: { text: "Customer requests refund AND amount > $500" },
    action: { text: "Forward to manager via #refunds Slack channel" },
    scope: "workspace",
    status: "active",
    created_at: "2026-04-28T08:00:00Z",
    updated_at: "2026-04-28T08:00:00Z",
    source_kind: "industry-baseline",
    sources: [
      { title: "Stripe — Refund best practices", url: "https://stripe.com/docs/refunds" },
    ],
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "ecommerce.shipping_threshold_skill",
    description: null,
    condition: { text: "Order qualifies for free shipping" },
    action: { text: "Apply free shipping at checkout" },
    scope: "workspace",
    status: "active",
    created_at: "2026-04-28T08:01:00Z",
    updated_at: "2026-04-28T08:01:00Z",
    // Pre-round-trip skill — null source_kind hides the pill instead
    // of mis-labelling as synthesized.
    source_kind: null,
    sources: [],
  },
];

const PENDING_SKILLS = [
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "consulting.scope_change_skill",
    description: null,
    condition: { text: "Scope creep detected" },
    action: { text: "Send change order template" },
    scope: "workspace",
    status: "pending_review",
    created_at: "2026-04-28T08:02:00Z",
    updated_at: "2026-04-28T08:02:00Z",
    source_kind: "synthesized",
    sources: [],
  },
];

// PLAN_14 PR-H — the library page now embeds the "Suggested from your
// edits" panel which fetches /api/v1/personalization/candidates on
// mount. Stub it empty across every test below so the panel renders
// deterministically without affecting the workspace skill assertions.
async function stubEmptyPersonalization(page: import("@playwright/test").Page) {
  // PR-J — the panel now hits two query variants (?status=pending_review
  // and ?status=active). Regex matcher catches both with one route.
  await page.route(
    /\/api\/v1\/personalization\/candidates(\?|$)/,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ candidates: [] }),
      }),
  );
}

test("Skill library: lists active skills by default with markdown rendering", async ({
  page,
}) => {
  await stubEmptyPersonalization(page);
  await page.route("**/api/v1/skills*", async (route) => {
    const url = new URL(route.request().url());
    const status = url.searchParams.get("status");
    const skills = status === "pending_review" ? PENDING_SKILLS : ACTIVE_SKILLS;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills }),
    });
  });

  await page.goto("/skills");

  await expect(page.getByTestId("skills-library")).toBeVisible();
  // Default tab is Active — surfaces both fixtures.
  const list = page.getByTestId("marketplace-list");
  await expect(list).toBeVisible();
  const rows = page.locator('[data-testid^="marketplace-row-"]');
  await expect(rows).toHaveCount(2);

  // First row exposes condition + action prose extracted from the JSONB
  // dict, plus the active status pill.
  const row1 = page.getByTestId(`marketplace-row-${ACTIVE_SKILLS[0].id}`);
  await expect(row1).toContainText(
    "Customer requests refund AND amount > $500",
  );
  await expect(row1).toContainText("Forward to manager via #refunds");
  await expect(
    page.getByTestId(`marketplace-status-${ACTIVE_SKILLS[0].id}`),
  ).toContainText("active");

  // Source-kind pill renders for the industry-baseline skill with the
  // attribution link visible.
  const pill = page.getByTestId(
    `source-kind-industry-baseline-${ACTIVE_SKILLS[0].id}`,
  );
  await expect(pill).toBeVisible();
  await expect(pill).toContainText("Industry baseline");
  await expect(
    pill.getByRole("link", { name: "Stripe — Refund best practices" }),
  ).toHaveAttribute("href", "https://stripe.com/docs/refunds");

  // The second skill has source_kind=null → no pill rendered.
  const row2 = page.getByTestId(`marketplace-row-${ACTIVE_SKILLS[1].id}`);
  await expect(
    row2.locator('[data-testid^="source-kind-"]'),
  ).toHaveCount(0);
});

test("Skill library: status tab switches the query", async ({ page }) => {
  await stubEmptyPersonalization(page);
  await page.route("**/api/v1/skills*", async (route) => {
    const url = new URL(route.request().url());
    const status = url.searchParams.get("status");
    const skills = status === "pending_review" ? PENDING_SKILLS : ACTIVE_SKILLS;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills }),
    });
  });

  await page.goto("/skills");
  // pending_review skills live under the "Other status" disclosure now —
  // expand it, then switch to the pending_review tab and assert the
  // fixture renders in the compact other-status list.
  await page.getByTestId("other-status-disclosure").click();
  await page.getByTestId("other-status-tab-pending_review").click();
  await expect(
    page.getByTestId(`other-status-row-${PENDING_SKILLS[0].id}`),
  ).toBeVisible();
  await expect(
    page.locator('[data-testid^="other-status-row-"]'),
  ).toHaveCount(1);
});

test("Skill library: empty state offers wizard link", async ({ page }) => {
  await stubEmptyPersonalization(page);
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills: [] }),
    }),
  );

  await page.goto("/skills");
  await expect(page.getByTestId("marketplace-empty")).toBeVisible();
  await expect(
    page.getByTestId("marketplace-empty-cta"),
  ).toHaveAttribute("href", "/skills/new");
});

test("Skill library: home → library nav link works", async ({ page }) => {
  await stubEmptyPersonalization(page);
  await page.route("**/api/v1/workflows*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        total: 0,
        limit: 3,
        plan_tier: "light",
      }),
    }),
  );
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills: ACTIVE_SKILLS }),
    }),
  );

  await page.goto("/");
  await page.getByTestId("link-skill-library").click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(page.getByTestId("skills-library")).toBeVisible();
});
