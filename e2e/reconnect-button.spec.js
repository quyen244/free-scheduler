// Verify Reconnect YouTube button renders on auth-error posts.
// Seeds one failed post (status=failed, auth-type error) directly in the DB,
// checks the queue card shows the Reconnect YouTube button, then cleans up.
const { chromium } = require("playwright");
const { execFile } = require("child_process");
const { promisify } = require("util");
const run = promisify(execFile);

const APP = "http://localhost:3000";

async function dbSeed() {
  // node --env-file passes DATABASE_URL; script does the prisma work
  const { stdout } = await run("node", ["--env-file=.env", "scripts/db-seed-failed-post.js"], {
    cwd: process.cwd(),
  });
  return JSON.parse(stdout);
}

(async () => {
  const seed = await dbSeed();
  console.log("seeded failed post:", seed.id);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  await page.goto(APP + "/");
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500);

  const hasButton = await page
    .locator("button:has-text('Reconnect YouTube')")
    .first()
    .isVisible({ timeout: 5000 })
    .catch(() => false);

  console.log("Reconnect YouTube button visible:", hasButton);

  const hasNonAuthRetry = await page
    .locator("div:has-text('[Button Test] auth-failed post') button:has-text('Reconnect YouTube')")
    .first()
    .isVisible({ timeout: 3000 })
    .catch(() => false);
  console.log("Button scoped to seeded post card:", hasNonAuthRetry);

  await browser.close();

  // Cleanup via API (removes seeded row)
  const del = await fetch(APP + "/api/posts/" + seed.id, { method: "DELETE" });
  console.log("cleanup delete:", del.status);

  if (!hasButton) {
    console.error("BUTTON TEST FAILED: button not visible on failed auth post");
    process.exit(1);
  }
  console.log("BUTTON TEST PASSED");
})().catch((err) => {
  console.error("BUTTON TEST FAILED:", err.message);
  process.exit(1);
});
