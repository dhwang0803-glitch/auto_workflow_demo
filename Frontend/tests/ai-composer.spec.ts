import { test, expect } from "@playwright/test";

// Mock-only smoke for the AI Composer SSE flow (PR D). Playwright intercepts
// the fetch boundary so no Anthropic credentials (or LLM cost) are required.
// The mock returns the response body all at once; the client parser still
// splits it on `\n\n`, so we exercise the same dispatch path as a real
// chunked stream.

const sse = (frames: Array<{ event: string; data: unknown }>): string =>
  frames
    .map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`)
    .join("");

test("AI Composer: stream → draft → apply populates canvas", async ({ page }) => {
  await page.route("**/api/v1/ai/compose**", (route) => {
    const body = sse([
      { event: "session", data: { session_id: "00000000-0000-0000-0000-000000000001" } },
      { event: "rationale_delta", data: { token: "Fetch " } },
      { event: "rationale_delta", data: { token: "the URL." } },
      {
        event: "result",
        data: {
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
            rationale: "Fetch the URL.",
          },
        },
      },
    ]);
    route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/workflows/new");

  await page.getByTestId("toggle-ai-composer").click();
  await expect(
    page.getByRole("complementary", { name: "AI Composer chat" }),
  ).toBeVisible();

  await page.getByTestId("chat-input").fill("Fetch https://example.com");
  await page.getByRole("button", { name: "Send" }).click();

  // Final assistant bubble renders once the `result` frame arrives.
  await expect(page.getByText("Fetch the URL.", { exact: false })).toBeVisible();
  await expect(page.getByTestId("proposed-summary")).toHaveText(/1 nodes/);
  // Streaming bubble disappears after the result frame promotes the text.
  await expect(page.getByTestId("streaming-bubble")).toHaveCount(0);

  await page.getByTestId("apply-draft").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.getByText("Unsaved changes")).toBeVisible();
});

test("AI Composer: clarify intent shows question list", async ({ page }) => {
  await page.route("**/api/v1/ai/compose**", (route) => {
    const body = sse([
      { event: "session", data: { session_id: "00000000-0000-0000-0000-000000000002" } },
      {
        event: "result",
        data: {
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
        },
      },
    ]);
    route.fulfill({ status: 200, contentType: "text/event-stream", body });
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

test("AI Composer: refine intent renders diff summary", async ({ page }) => {
  await page.route("**/api/v1/ai/compose**", (route) => {
    const body = sse([
      { event: "session", data: { session_id: "00000000-0000-0000-0000-000000000003" } },
      {
        event: "result",
        data: {
          session_id: "00000000-0000-0000-0000-000000000003",
          result: {
            intent: "refine",
            clarify_questions: null,
            proposed_dag: {
              nodes: [
                {
                  id: "fetch_alpha",
                  type: "http_request",
                  config: { url: "https://example.com/v2" },
                },
              ],
              edges: [],
            },
            diff: {
              added_nodes: [],
              removed_node_ids: [],
              modified_nodes: [
                { id: "fetch_alpha", config: { url: "https://example.com/v2" } },
              ],
            },
            rationale: "Pointed the request at v2.",
          },
        },
      },
    ]);
    route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/workflows/new");
  await page.getByTestId("toggle-ai-composer").click();
  await page.getByTestId("chat-input").fill("Use v2 instead.");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Pointed the request at v2.")).toBeVisible();
  // Diff summary lists the modified node and its config keys.
  const diff = page.getByTestId("diff-summary");
  await expect(diff).toBeVisible();
  await expect(diff.locator("li")).toHaveCount(1);
  await expect(diff.locator("li").first()).toHaveText(/~ fetch_alpha/);
  // Refine path uses the same Apply/Reject bar (just labelled "refinement").
  await expect(page.getByText("Proposed refinement ready")).toBeVisible();
});

test("AI Composer: in-band error frame surfaces as banner", async ({ page }) => {
  await page.route("**/api/v1/ai/compose**", (route) => {
    const body = sse([
      { event: "session", data: { session_id: "00000000-0000-0000-0000-000000000004" } },
      { event: "error", data: { code: "rate_limit", message: "slow down" } },
    ]);
    route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/workflows/new");
  await page.getByTestId("toggle-ai-composer").click();
  await page.getByTestId("chat-input").fill("anything");
  await page.getByRole("button", { name: "Send" }).click();

  const banner = page.getByTestId("chat-error");
  await expect(banner).toContainText("rate_limit");
  await expect(banner).toContainText("slow down");
  // Pending state should clear so the user can retry.
  await expect(page.getByTestId("chat-input")).toBeEnabled();
});
