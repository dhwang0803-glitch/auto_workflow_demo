import { test, expect } from "@playwright/test";

// Mock-only smoke for the Persona A interview wizard (PLAN_12 W2-5).
// Playwright intercepts /api/v1/skills/* so the test exercises the
// frontend state machine without API_Server / AI_Agent dependencies.

test("Skill wizard: pick domain → answer 2 questions → done summary", async ({
  page,
}) => {
  let answerCalls = 0;

  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.domain).toBe("ecommerce");
    expect(typeof body.session_id).toBe("string");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        missing: [
          {
            policy_id: "ecommerce.refund_window",
            policy_name: "Refund window",
            questions: [
              {
                text: "How many days is your refund window?",
                parameter: "RETURN_WINDOW_DAYS",
              },
            ],
          },
          {
            policy_id: "ecommerce.shipping_threshold",
            policy_name: "Free shipping threshold",
            questions: [
              {
                text: "What's the free shipping threshold?",
                parameter: "FREE_SHIPPING_AMOUNT",
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/skills/answer", async (route) => {
    answerCalls += 1;
    const body = route.request().postDataJSON();
    const skill_id = `00000000-0000-0000-0000-00000000000${answerCalls}`;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        skill_id,
        draft: {
          name: `${body.policy_id}_skill`,
          description: "",
          condition: `policy:${body.policy_id}`,
          action: `value:${body.answer}`,
          rationale: "",
          needs_clarification: false,
          clarification_hint: "",
        },
      }),
    });
  });

  await page.goto("/skills/new");
  await expect(page.getByTestId("domain-picker")).toBeVisible();

  await page.getByTestId("domain-chip-ecommerce").click();

  // First question — progress 0/2
  await expect(page.getByTestId("wizard-progress")).toContainText("0 / 2");
  await expect(page.getByTestId("wizard-current-question")).toContainText(
    "refund window",
  );

  await page.getByTestId("wizard-input").fill("14 days");
  await page.getByRole("button", { name: "Send" }).click();

  // Second question — progress 1/2
  await expect(page.getByTestId("wizard-progress")).toContainText("1 / 2");
  await expect(page.getByTestId("wizard-turn-1")).toContainText("14 days");
  await expect(page.getByTestId("wizard-current-question")).toContainText(
    "free shipping",
  );

  await page.getByTestId("wizard-input").fill("$30");
  await page.getByRole("button", { name: "Send" }).click();

  // Done
  await expect(page.getByTestId("wizard-done")).toBeVisible();
  await expect(page.getByTestId("wizard-progress")).toContainText("2 / 2");
  await expect(page.getByTestId("wizard-turn-2")).toContainText("$30");
  expect(answerCalls).toBe(2);

  // The input form is gone once the wizard is done.
  await expect(page.getByTestId("wizard-input")).toHaveCount(0);
});

test("Skill wizard: bootstrap with no gaps lands on done state", async ({
  page,
}) => {
  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "other",
        missing: [],
      }),
    });
  });

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-other").click();

  await expect(page.getByTestId("wizard-no-gaps")).toBeVisible();
  await expect(page.getByTestId("wizard-input")).toHaveCount(0);
});

test("Skill wizard: bootstrap error surfaces banner with retry", async ({
  page,
}) => {
  await page.route("**/api/v1/skills/bootstrap", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "ai_agent error 503" }),
    }),
  );

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-services").click();

  const banner = page.getByTestId("wizard-error");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("502");

  // Retry returns the user to the domain picker.
  await banner.getByRole("button", { name: "Start over" }).click();
  await expect(page.getByTestId("domain-picker")).toBeVisible();
});
