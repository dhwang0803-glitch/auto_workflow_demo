import { test, expect } from "@playwright/test";

// Local smoke: drive the editor end-to-end against a live API_Server.
// Because the test mutates real workflows, we always start a fresh one
// via the "+ New workflow" flow. The dev token comes from the same
// NEXT_PUBLIC_DEV_TOKEN the app uses, so no extra auth setup is needed.
test("create → save → execute a trivial workflow", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Workflows" })).toBeVisible();

  await page.getByRole("link", { name: "+ New workflow" }).click();
  await expect(page).toHaveURL(/\/workflows\/new$/);

  // Rename so the workflow is easy to spot in Postgres if it sticks around.
  const name = `smoke-${Date.now()}`;
  const nameInput = page.locator('input[type="text"]').first();
  await nameInput.fill(name);

  // Drag the first node from the palette onto the canvas. React Flow
  // registers drops against the .react-flow__renderer element.
  const firstPaletteItem = page.locator('[draggable="true"]').first();
  const canvas = page.locator(".react-flow");
  await firstPaletteItem.dragTo(canvas, {
    targetPosition: { x: 300, y: 200 },
  });

  // Save — this creates the workflow and redirects to /workflows/{id}.
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/, {
    timeout: 15_000,
  });
  await expect(page.getByText("Saved")).toBeVisible();

  // Execute and wait for the drawer to report a terminal status.
  await page.getByRole("button", { name: "Execute" }).click();
  await expect(page.getByRole("complementary", { name: "Execution result" }))
    .toBeVisible();

  const pill = page
    .getByRole("complementary", { name: "Execution result" })
    .locator("header span")
    .first();
  await expect(pill).toHaveText(/success|failed/, { timeout: 30_000 });
});
