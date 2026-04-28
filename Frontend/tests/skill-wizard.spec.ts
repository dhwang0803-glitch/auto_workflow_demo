import { test, expect } from "@playwright/test";

// Mock-only smoke for the Persona A interview wizard (PLAN_12 W2-5/W2-6).
// Playwright intercepts /api/v1/skills/* so the test exercises the
// frontend state machine without API_Server / AI_Agent dependencies.

interface AnswerBody {
  session_id: string;
  domain: string;
  policy_id: string;
  question: string;
  answer: string;
}

const draftFor = (
  body: AnswerBody,
  overrides: Partial<{
    needs_clarification: boolean;
    clarification_hint: string;
  }> = {},
) => ({
  name: `${body.policy_id}_skill`,
  description: "",
  condition: `policy:${body.policy_id}`,
  action: `value:${body.answer}`,
  rationale: "",
  needs_clarification: overrides.needs_clarification ?? false,
  clarification_hint: overrides.clarification_hint ?? "",
});

test("Skill wizard: pick domain → answer 2 questions → approve + reject", async ({
  page,
}) => {
  let answerCalls = 0;
  const skillIds = [
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002",
  ];

  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.domain).toBe("ecommerce");
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
    const body = route.request().postDataJSON();
    const skill_id = skillIds[answerCalls];
    answerCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        skill_id,
        draft: draftFor(body),
      }),
    });
  });

  // Approve / reject return the persisted skill row with its new status.
  await page.route(
    `**/api/v1/skills/${skillIds[0]}/approve`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: skillIds[0],
          name: "ecommerce.refund_window_skill",
          description: null,
          condition: { text: "policy:ecommerce.refund_window" },
          action: { text: "value:14 days" },
          scope: "workspace",
          status: "active",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    },
  );
  await page.route(
    `**/api/v1/skills/${skillIds[1]}/reject`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: skillIds[1],
          name: "ecommerce.shipping_threshold_skill",
          description: null,
          condition: { text: "policy:ecommerce.shipping_threshold" },
          action: { text: "value:$30" },
          scope: "workspace",
          status: "rejected",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    },
  );

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-ecommerce").click();

  await page.getByTestId("wizard-input").fill("14 days");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("wizard-input").fill("$30");
  await page.getByRole("button", { name: "Send" }).click();

  // Review phase: the answered turn bubbles are gone, replaced by full
  // skill cards with controls.
  await expect(page.getByTestId("wizard-review")).toBeVisible();
  const cards = page.locator('[data-testid^="skill-card-"]');
  await expect(cards).toHaveCount(2);
  await expect(page.getByTestId("wizard-input")).toHaveCount(0);

  const card1 = page.getByTestId(`skill-card-${skillIds[0]}`);
  await expect(card1).toContainText("14 days");
  await expect(card1).toContainText("ecommerce.refund_window");
  await expect(card1).toHaveAttribute("data-action-status", "pending");

  // Approve the first card.
  await page.getByTestId(`approve-${skillIds[0]}`).click();
  await expect(card1).toHaveAttribute("data-action-status", "approved");
  // Action buttons disappear after a settled action.
  await expect(page.getByTestId(`approve-${skillIds[0]}`)).toHaveCount(0);
  await expect(page.getByTestId(`reject-${skillIds[0]}`)).toHaveCount(0);

  // Reject the second card.
  const card2 = page.getByTestId(`skill-card-${skillIds[1]}`);
  await page.getByTestId(`reject-${skillIds[1]}`).click();
  await expect(card2).toHaveAttribute("data-action-status", "rejected");
});

test("Skill wizard: needs_clarification card surfaces follow-up", async ({
  page,
}) => {
  const skillIds = [
    "00000000-0000-0000-0000-000000000010",
    "00000000-0000-0000-0000-000000000011",
  ];
  let answerCalls = 0;

  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "consulting",
        missing: [
          {
            policy_id: "consulting.scope_change",
            policy_name: "Scope change handling",
            questions: [
              {
                text: "How do you handle scope changes mid-engagement?",
                parameter: "SCOPE_PROCESS",
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/skills/answer", async (route) => {
    const body = route.request().postDataJSON();
    const skill_id = skillIds[answerCalls];
    const isFirst = answerCalls === 0;
    answerCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        skill_id,
        draft: draftFor(body, {
          // First answer is too vague → LLM flags clarification. Second
          // pass produces a clean draft.
          needs_clarification: isFirst,
          clarification_hint: isFirst
            ? "What dollar threshold triggers a written change order?"
            : "",
        }),
      }),
    });
  });

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-consulting").click();
  await page.getByTestId("wizard-input").fill("we figure it out");
  await page.getByRole("button", { name: "Send" }).click();

  // Review phase shows the flagged card with follow-up affordance.
  const flagged = page.getByTestId(`skill-card-${skillIds[0]}`);
  await expect(flagged).toBeVisible();
  await expect(
    page.getByTestId(`clarification-hint-${skillIds[0]}`),
  ).toContainText("dollar threshold");

  await page.getByTestId(`follow-up-${skillIds[0]}`).click();

  // Wizard re-enters asking with the clarification hint as the new
  // question; input form returns.
  await expect(page.getByTestId("wizard-current-question")).toContainText(
    "dollar threshold",
  );
  await expect(page.getByTestId("wizard-input")).toBeVisible();

  await page.getByTestId("wizard-input").fill("$5,000+ requires written CO");
  await page.getByRole("button", { name: "Send" }).click();

  // Both drafts visible in review; second one not flagged.
  await expect(page.locator('[data-testid^="skill-card-"]')).toHaveCount(2);
  await expect(
    page.getByTestId(`clarification-hint-${skillIds[1]}`),
  ).toHaveCount(0);
  expect(answerCalls).toBe(2);
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

  await banner.getByRole("button", { name: "Start over" }).click();
  await expect(page.getByTestId("domain-picker")).toBeVisible();
});

test("Skill wizard: approve API failure shows error + keeps controls", async ({
  page,
}) => {
  const skillId = "00000000-0000-0000-0000-000000000020";

  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        missing: [
          {
            policy_id: "ecommerce.x",
            policy_name: "X",
            questions: [{ text: "Q?", parameter: "P" }],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/skills/answer", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        skill_id: skillId,
        draft: draftFor(body),
      }),
    });
  });

  await page.route(`**/api/v1/skills/${skillId}/approve`, (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "boom" }),
    }),
  );

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-ecommerce").click();
  await page.getByTestId("wizard-input").fill("answer");
  await page.getByRole("button", { name: "Send" }).click();

  await page.getByTestId(`approve-${skillId}`).click();

  // Action error surfaces; approve button stays so the user can retry.
  await expect(page.getByTestId(`action-error-${skillId}`)).toContainText(
    "500",
  );
  await expect(page.getByTestId(`approve-${skillId}`)).toBeVisible();
  await expect(
    page.getByTestId(`skill-card-${skillId}`),
  ).toHaveAttribute("data-action-status", "failed");
});
