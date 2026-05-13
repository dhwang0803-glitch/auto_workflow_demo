import { test, expect } from "@playwright/test";

// "Suggested from your edits" panel (PLAN_14 PR-H).
//
// Mock-based. The panel renders inside the skill library page above
// the workspace list, queries `/api/v1/personalization/candidates`,
// and offers Activate / Reject affordances per candidate. Activate
// fires the activate endpoint and refetches the list; Reject opens a
// reason form and fires the reject endpoint on confirm.

const ALICE = "00000000-0000-0000-0000-aaaaaaaaaaaa";

const PENDING = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    user_id: ALICE,
    hint: "Always add Slack notify after credentials touch",
    diff_signature: "sig:abc123",
    suggestion_hash: "hash-1",
    status: "pending_review",
    created_at: "2026-05-13T09:00:00Z",
    updated_at: "2026-05-13T09:00:00Z",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    user_id: ALICE,
    hint: "Prefer 5min retry over default 30s on HTTP nodes",
    diff_signature: "sig:def456",
    suggestion_hash: "hash-2",
    status: "pending_review",
    created_at: "2026-05-13T09:01:00Z",
    updated_at: "2026-05-13T09:01:00Z",
  },
];

const EMPTY_SKILLS_BODY = { skills: [] };

test("Suggested from your edits: empty state when no candidates", async ({
  page,
}) => {
  await page.route("**/api/v1/personalization/candidates", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ candidates: [] }),
    }),
  );
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_SKILLS_BODY),
    }),
  );

  await page.goto("/skills");
  await expect(page.getByTestId("suggested-from-edits")).toBeVisible();
  await expect(page.getByTestId("suggested-empty")).toBeVisible();
  await expect(page.getByTestId("suggested-count")).toContainText(
    "0 pending",
  );
});

test("Suggested from your edits: lists pending candidates with hints", async ({
  page,
}) => {
  await page.route("**/api/v1/personalization/candidates", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ candidates: PENDING }),
    }),
  );
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_SKILLS_BODY),
    }),
  );

  await page.goto("/skills");
  await expect(page.getByTestId("suggested-count")).toContainText(
    "2 pending",
  );
  const rows = page.locator('[data-testid^="suggested-row-"]');
  await expect(rows).toHaveCount(2);

  // First candidate's hint surfaces verbatim.
  await expect(
    page.getByTestId(`suggested-hint-${PENDING[0].id}`),
  ).toContainText("Always add Slack notify after credentials touch");

  // Diff signature is shown as small mono attribution.
  await expect(
    page.getByTestId(`suggested-diff-${PENDING[0].id}`),
  ).toContainText("sig:abc123");
});

test("Suggested from your edits: activate calls API and refreshes list", async ({
  page,
}) => {
  let listCalls = 0;
  await page.route(
    "**/api/v1/personalization/candidates",
    async (route) => {
      listCalls += 1;
      // First call returns 1 pending; second call (after activate) is empty.
      const candidates = listCalls === 1 ? [PENDING[0]] : [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ candidates }),
      });
    },
  );
  await page.route(
    `**/api/v1/personalization/candidates/${PENDING[0].id}/activate`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...PENDING[0], status: "active" }),
      }),
  );
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_SKILLS_BODY),
    }),
  );

  await page.goto("/skills");
  await expect(
    page.getByTestId(`suggested-row-${PENDING[0].id}`),
  ).toBeVisible();

  await page.getByTestId(`suggested-activate-${PENDING[0].id}`).click();

  // After activate, list refetches and the row disappears.
  await expect(page.getByTestId("suggested-empty")).toBeVisible();
  expect(listCalls).toBeGreaterThanOrEqual(2);
});

test("Suggested from your edits: reject opens reason form and POSTs", async ({
  page,
}) => {
  let listCalls = 0;
  await page.route(
    "**/api/v1/personalization/candidates",
    async (route) => {
      listCalls += 1;
      const candidates = listCalls === 1 ? [PENDING[0]] : [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ candidates }),
      });
    },
  );
  let rejectBody: { reason: string | null } | null = null;
  await page.route(
    `**/api/v1/personalization/candidates/${PENDING[0].id}/reject`,
    async (route) => {
      rejectBody = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...PENDING[0], status: "archived" }),
      });
    },
  );
  await page.route("**/api/v1/skills*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_SKILLS_BODY),
    }),
  );

  await page.goto("/skills");

  // Click Reject — form opens with reason textarea.
  await page
    .getByTestId(`suggested-reject-toggle-${PENDING[0].id}`)
    .click();
  await expect(
    page.getByTestId(`suggested-reject-form-${PENDING[0].id}`),
  ).toBeVisible();

  // Type a reason + confirm — POST goes out with that reason.
  await page
    .getByTestId(`suggested-reject-reason-${PENDING[0].id}`)
    .fill("not generalizable");
  await page
    .getByTestId(`suggested-reject-confirm-${PENDING[0].id}`)
    .click();

  // List refreshes and row disappears.
  await expect(page.getByTestId("suggested-empty")).toBeVisible();
  expect(rejectBody).toMatchObject({ reason: "not generalizable" });
});
