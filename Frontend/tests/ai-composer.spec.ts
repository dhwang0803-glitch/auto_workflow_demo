import { test, expect } from "@playwright/test";

// Mock-only smoke: the AI Composer endpoint is intercepted at the fetch
// boundary so no Anthropic credentials (or LLM cost) are required. We
// verify the UI contract: toggle → send → assistant bubble → Apply draft
// populates the canvas.
test("AI Composer: send → draft → apply populates canvas", async ({ page }) => {
  await page.route("**/api/v1/ai/compose", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "00000000-0000-0000-0000-000000000001",
        result: {
          intent: "draft",
          clarify_questions: null,
          proposed_dag: {
            nodes: [
              {
                id: "fetch_alpha",
                type: "http_request",
                config: { url: "https://example.com" },
              },
            ],
            edges: [],
          },
          diff: null,
          rationale: "Fetch the URL and stop.",
        },
      }),
    });
  });

  await page.goto("/workflows/new");

  await page.getByTestId("toggle-ai-composer").click();
  await expect(
    page.getByRole("complementary", { name: "AI Composer chat" }),
  ).toBeVisible();

  await page.getByTestId("chat-input").fill("Fetch https://example.com");
  await page.getByRole("button", { name: "Send" }).click();

  // Assistant rationale bubble renders once the mocked response resolves.
  await expect(
    page.getByText("Fetch the URL and stop.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByTestId("proposed-summary")).toHaveText(
    /1 nodes/,
  );

  // Apply populates the canvas with the proposed node.
  await page.getByTestId("apply-draft").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.getByText("Unsaved changes")).toBeVisible();
});

test("AI Composer: clarify intent shows question list", async ({ page }) => {
  await page.route("**/api/v1/ai/compose", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: "00000000-0000-0000-0000-000000000002",
        result: {
          intent: "clarify",
          clarify_questions: [
            "Which data source?",
            "Who should receive the report?",
          ],
          proposed_dag: null,
          diff: null,
          rationale: "Need more detail before drafting.",
        },
      }),
    });
  });

  await page.goto("/workflows/new");
  await page.getByTestId("toggle-ai-composer").click();
  await page.getByTestId("chat-input").fill("Send a report.");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Need more detail before drafting.")).toBeVisible();
  const questions = page.getByTestId("clarify-questions").locator("li");
  await expect(questions).toHaveCount(2);
  await expect(questions.first()).toHaveText("Which data source?");

  // No "Apply" affordance should appear on a clarify turn.
  await expect(page.getByTestId("apply-draft")).toHaveCount(0);
});
