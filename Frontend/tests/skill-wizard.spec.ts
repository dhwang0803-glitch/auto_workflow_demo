import { test, expect } from "@playwright/test";

// Mock-only smoke for the Persona A interview wizard (PLAN_12 W2-5/6 + W2-5b).
// Playwright intercepts /api/v1/skills/* so the test exercises the
// frontend state machine without API_Server / AI_Agent dependencies.

interface AnswersBody {
  session_id: string;
  domain: string;
  policy_id: string;
  answers: { parameter: string; answer: string }[];
}

const draftFor = (
  body: AnswersBody,
  overrides: Partial<{
    needs_clarification: boolean;
    clarification_hint: string;
  }> = {},
) => ({
  name: `${body.policy_id}_skill`,
  description: "",
  condition: `policy:${body.policy_id}`,
  // Concatenate the batch answers into a single action string for the
  // SkillCard preview — mirrors what AI_Agent's stub backend does.
  action: body.answers.map((a) => `${a.parameter}=${a.answer}`).join("; "),
  rationale: "",
  needs_clarification: overrides.needs_clarification ?? false,
  clarification_hint: overrides.clarification_hint ?? "",
});

// Two-policy gap fixture covering the W2-5b polish surfaces:
// - one synthesized policy with no sources, no help_text on its first param
// - one industry-baseline policy with default_baseline + baseline_source
//   + help_text + example_answer on every param
const REFUND_GAP = {
  policy_id: "ecommerce.refund_threshold",
  policy_name: "Refund threshold escalation",
  source_kind: "industry-baseline",
  sources: [
    {
      title: "Stripe — Refunds documentation",
      url: "https://docs.stripe.com/refunds",
    },
  ],
  parameters: [
    {
      text: "What dollar amount can you refund without manager approval?",
      parameter: "REFUND_AUTO_APPROVE_LIMIT",
      default_baseline: "$50",
      baseline_source: "Synthesized — small merchants typically auto-approve up to $50.",
      help_text:
        "Refunds under this dollar amount go through automatically; anything over routes to your approver.",
      example_answer: "$100",
    },
    {
      text: "Who approves refunds above that limit?",
      parameter: "REFUND_APPROVER",
      default_baseline: "Operations lead",
      baseline_source: "Synthesized — team-specific role.",
      help_text: "",
      example_answer: "Sarah (co-founder)",
    },
  ],
  questions: [],
};

const SHIPPING_GAP = {
  policy_id: "ecommerce.shipping_threshold",
  policy_name: "Free shipping threshold",
  source_kind: "synthesized",
  sources: [],
  parameters: [
    {
      text: "What's the free shipping threshold?",
      parameter: "FREE_SHIPPING_AMOUNT",
      default_baseline: "",
      baseline_source: "",
      help_text: "",
      example_answer: "",
    },
  ],
  questions: [],
};

test("Skill wizard: pick domain → batch-answer 2 policies → approve + reject", async ({
  page,
}) => {
  let answersCalls = 0;
  const skillIds = [
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002",
  ];
  const seenAnswerBodies: AnswersBody[] = [];

  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.domain).toBe("ecommerce");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        missing: [REFUND_GAP, SHIPPING_GAP],
      }),
    });
  });

  await page.route("**/api/v1/skills/answers", async (route) => {
    const body = route.request().postDataJSON() as AnswersBody;
    seenAnswerBodies.push(body);
    const skill_id = skillIds[answersCalls];
    answersCalls += 1;
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

  await page.route(
    `**/api/v1/skills/${skillIds[0]}/approve`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: skillIds[0],
          name: "ecommerce.refund_threshold_skill",
          description: null,
          condition: { text: "policy:ecommerce.refund_threshold" },
          action: { text: "REFUND_AUTO_APPROVE_LIMIT=$50; REFUND_APPROVER=Sarah" },
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
          action: { text: "FREE_SHIPPING_AMOUNT=$30" },
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

  // First policy renders both parameter cards. Submit is gated until
  // every parameter has an answer.
  const policyTurn = page.getByTestId("wizard-current-policy");
  await expect(policyTurn).toContainText("Refund threshold escalation");

  // Source-kind pill is shown for industry-baseline policies.
  await expect(
    page.getByTestId("source-kind-industry-baseline"),
  ).toBeVisible();

  // Use baseline button on first parameter — fills the textarea with $50.
  await page.getByTestId("use-baseline-REFUND_AUTO_APPROVE_LIMIT").click();
  await expect(
    page.getByTestId("answer-input-REFUND_AUTO_APPROVE_LIMIT"),
  ).toHaveValue("$50");

  // Manual fill on second parameter.
  await page
    .getByTestId("answer-input-REFUND_APPROVER")
    .fill("Sarah (co-founder)");

  // Submit batch.
  await page.getByTestId("wizard-submit-policy").click();

  // Second policy turn renders.
  await expect(policyTurn).toContainText("Free shipping threshold");
  // Synthesized policy → no source-kind pill.
  await expect(page.getByTestId("source-kind-industry-baseline")).toHaveCount(
    0,
  );

  await page.getByTestId("answer-input-FREE_SHIPPING_AMOUNT").fill("$30");
  await page.getByTestId("wizard-submit-policy").click();

  // Review phase: full skill cards with controls.
  await expect(page.getByTestId("wizard-review")).toBeVisible();
  const cards = page.locator('[data-testid^="skill-card-"]');
  await expect(cards).toHaveCount(2);
  await expect(page.getByTestId("wizard-submit-policy")).toHaveCount(0);

  const card1 = page.getByTestId(`skill-card-${skillIds[0]}`);
  await expect(card1).toContainText("REFUND_AUTO_APPROVE_LIMIT=$50");
  await expect(card1).toHaveAttribute("data-action-status", "pending");
  // W2-6b: SkillCard carries the same source-kind pill the wizard
  // surfaced mid-flow, scoped per card so multiple drafts don't collide.
  await expect(
    page.getByTestId(`source-kind-industry-baseline-card-${skillIds[0]}`),
  ).toBeVisible();
  await expect(
    page.getByTestId(`source-kind-synthesized-card-${skillIds[1]}`),
  ).toBeVisible();

  // Approve → active.
  await page.getByTestId(`approve-${skillIds[0]}`).click();
  await expect(card1).toHaveAttribute("data-action-status", "approved");

  // Reject → rejected.
  await page.getByTestId(`reject-${skillIds[1]}`).click();
  const card2 = page.getByTestId(`skill-card-${skillIds[1]}`);
  await expect(card2).toHaveAttribute("data-action-status", "rejected");

  // Wire shape: each /answers call carried policy_id + answers array.
  expect(seenAnswerBodies).toHaveLength(2);
  expect(seenAnswerBodies[0].policy_id).toBe("ecommerce.refund_threshold");
  expect(seenAnswerBodies[0].answers).toEqual([
    { parameter: "REFUND_AUTO_APPROVE_LIMIT", answer: "$50" },
    { parameter: "REFUND_APPROVER", answer: "Sarah (co-founder)" },
  ]);
  expect(seenAnswerBodies[1].policy_id).toBe("ecommerce.shipping_threshold");
});

test("Skill wizard: help_text expander toggles on click", async ({ page }) => {
  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        missing: [REFUND_GAP],
      }),
    });
  });

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-ecommerce").click();

  // help_text is hidden until the user clicks "What is this?".
  await expect(
    page.getByTestId("help-text-REFUND_AUTO_APPROVE_LIMIT"),
  ).toHaveCount(0);

  await page.getByTestId("help-toggle-REFUND_AUTO_APPROVE_LIMIT").click();
  await expect(
    page.getByTestId("help-text-REFUND_AUTO_APPROVE_LIMIT"),
  ).toContainText("Refunds under this dollar amount");

  // Second click hides it again.
  await page.getByTestId("help-toggle-REFUND_AUTO_APPROVE_LIMIT").click();
  await expect(
    page.getByTestId("help-text-REFUND_AUTO_APPROVE_LIMIT"),
  ).toHaveCount(0);

  // Parameter without help_text doesn't render the toggle.
  await expect(page.getByTestId("help-toggle-REFUND_APPROVER")).toHaveCount(0);
});

test("Skill wizard: needs_clarification surfaces follow-up", async ({
  page,
}) => {
  const skillIds = [
    "00000000-0000-0000-0000-000000000010",
    "00000000-0000-0000-0000-000000000011",
  ];
  let answersCalls = 0;

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
            source_kind: "synthesized",
            sources: [],
            questions: [],
            parameters: [
              {
                text: "How do you handle scope changes mid-engagement?",
                parameter: "SCOPE_PROCESS",
                default_baseline: "",
                baseline_source: "",
                help_text: "",
                example_answer: "",
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/skills/answers", async (route) => {
    const body = route.request().postDataJSON() as AnswersBody;
    const skill_id = skillIds[answersCalls];
    const isFirst = answersCalls === 0;
    answersCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        skill_id,
        draft: draftFor(body, {
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
  await page.getByTestId("answer-input-SCOPE_PROCESS").fill("we figure it out");
  await page.getByTestId("wizard-submit-policy").click();

  const flagged = page.getByTestId(`skill-card-${skillIds[0]}`);
  await expect(flagged).toBeVisible();
  await expect(
    page.getByTestId(`clarification-hint-${skillIds[0]}`),
  ).toContainText("dollar threshold");

  await page.getByTestId(`follow-up-${skillIds[0]}`).click();

  // Wizard re-enters asking with the clarification hint as the new
  // parameter prompt; the policy turn shows it.
  await expect(page.getByTestId("wizard-current-policy")).toContainText(
    "dollar threshold",
  );

  await page
    .getByTestId("answer-input-SCOPE_PROCESS")
    .fill("$5,000+ requires written CO");
  await page.getByTestId("wizard-submit-policy").click();

  // Both drafts visible; second one not flagged.
  await expect(page.locator('[data-testid^="skill-card-"]')).toHaveCount(2);
  await expect(
    page.getByTestId(`clarification-hint-${skillIds[1]}`),
  ).toHaveCount(0);
  expect(answersCalls).toBe(2);
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
  await expect(page.getByTestId("wizard-submit-policy")).toHaveCount(0);
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
            source_kind: "synthesized",
            sources: [],
            questions: [],
            parameters: [
              {
                text: "Q?",
                parameter: "P",
                default_baseline: "",
                baseline_source: "",
                help_text: "",
                example_answer: "",
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/skills/answers", async (route) => {
    const body = route.request().postDataJSON() as AnswersBody;
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
  await page.getByTestId("answer-input-P").fill("answer");
  await page.getByTestId("wizard-submit-policy").click();

  await page.getByTestId(`approve-${skillId}`).click();

  await expect(page.getByTestId(`action-error-${skillId}`)).toContainText(
    "500",
  );
  await expect(page.getByTestId(`approve-${skillId}`)).toBeVisible();
  await expect(
    page.getByTestId(`skill-card-${skillId}`),
  ).toHaveAttribute("data-action-status", "failed");
});
