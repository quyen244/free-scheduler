// E2E acceptance test: local app → real YouTube upload of final.mp4
//
// Flow:
// 1. Load workspace at http://localhost:3000 — NO app login required.
// 2. Verify YouTube account appears (from stored Google OAuth row).
//    If Google session expired / refresh token missing, open /login,
//    PAUSE for the user's manual Google sign-in (we NEVER touch the password),
//    then persist storageState for reruns.
// 3. Upload the real video via the app's own upload endpoint + form.
// 4. Submit post → app publishes to YouTube via resumable upload.
// 5. Capture videoId/publishedUrl; verify via oEmbed + watch page.
//
// Security: auth state saved to e2e/.auth/state.json (gitignored). Never logged.

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const APP = "http://localhost:3000";
const VIDEO = "D:\\Projects\\Assignment\\n8n\\data\\3gi_15UH9fQ.bench-backup\\processed\\final.mp4";
const AUTH_DIR = path.join(__dirname, ".auth");
const STATE_FILE = path.join(AUTH_DIR, "state.json");
const RESULT_FILE = path.join(__dirname, "result.json");
const TITLE_PREFIX = "[E2E Local Test]";

async function ensureGoogleAuth(page, context) {
  // A valid NextAuth client session (from persisted storageState on reruns)
  // means Google OAuth is already authorized — skip the manual step.
  const sessRes = await page.request.get(APP + "/api/auth/session");
  const sess = await sessRes.json().catch(() => ({}));
  if (sess?.user) {
    console.log("Existing Google session valid — skipping manual auth.");
    return;
  }

  // No valid session → fresh Google consent required (DB tokens may be expired
  // or missing a refresh token). User completes sign-in themselves.
  await page.goto(APP + "/login?callbackUrl=" + encodeURIComponent(APP + "/integrations"));
  await page.waitForLoadState("networkidle");

  console.log("\n=====================================================");
  console.log("ACTION REQUIRED: Google authentication needed.");
  console.log("Complete the sign-in in the opened browser window.");
  console.log("The test NEVER reads your password — you type it yourself.");
  console.log("=====================================================\n");

  const authBtn = page.locator("button:has-text('Authorize with Google')");
  await authBtn.click();

  // Wait (up to 2 hours) for the NextAuth session to become REAL.
  // Poll /api/auth/session — it stays {} until the user finishes Google sign-in
  // and the OAuth callback lands. waitForURL is unusable here: the current
  // /login URL already matches any app glob pattern.
  const deadline = Date.now() + 7_200_000;
  let signedIn = false;
  while (Date.now() < deadline) {
    if (page.isClosed()) throw new Error("Browser closed before sign-in completed");
    try {
      const sessRes = await page.request.get(APP + "/api/auth/session");
      const sess = await sessRes.json().catch(() => ({}));
      if (sess?.user?.email) {
        signedIn = true;
        break;
      }
    } catch (e) {
      /* server may 401/timeout mid-flight; keep polling */
    }
    await page.waitForTimeout(2000);
  }
  if (!signedIn) throw new Error("Timed out waiting for Google sign-in (2 hours)");

  console.log("Google sign-in complete.");
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  await context.storageState({ path: STATE_FILE });
}

(async () => {
  const browser = await chromium.launch({ headless: false });
  const contextOpts = { acceptDownloads: true };
  if (fs.existsSync(STATE_FILE)) contextOpts.storageState = STATE_FILE;
  const context = await browser.newContext(contextOpts);
  const page = await context.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  // Step 1 — no login gate on workspace
  await page.goto(APP + "/");
  await page.waitForLoadState("networkidle");
  if (page.url().includes("/login")) {
    throw new Error("FAIL: workspace redirected to /login — app login gate still present");
  }
  console.log("Step 1 OK: workspace loads without app login.");

  // Step 2 — ensure Google auth (may pause for manual sign-in)
  await ensureGoogleAuth(page, context);

  // Step 3 — back to workspace, select platform + channel
  await page.goto(APP + "/");
  await page.waitForLoadState("networkidle");

  // Platform dropdown → YouTube
  await page.click("#custom-platform-select");
  await page.click("#platform-option-youtube");
  console.log("Step 3a: platform = YouTube selected.");

  // Target Channel dropdown → first YouTube account
  await page.click("#custom-channel-select");
  const firstChannel = page.locator('[id^="channel-option-"]').first();
  await firstChannel.click();
  console.log("Step 3b: YouTube channel selected.");

  // Upload video through the app's real upload endpoint
  const title = `${TITLE_PREFIX} ${new Date().toISOString()}`;
  await page.setInputFiles('input[type="file"]', VIDEO);
  await page.waitForFunction(
    () => !document.querySelector('button[type="submit"]').disabled,
    null,
    { timeout: 300_000 }
  );
  console.log("Step 3c: video uploaded to app, submit enabled.");

  // Fill title/description
  await page.fill('input[placeholder*="amazing video"]', title);
  await page.fill('textarea[placeholder*="viewers"]', "Uploaded by local E2E test — final.mp4");

  // Step 4 — submit → real publish
  const responsePromise = page.waitForResponse(
    (res) => res.url().includes("/api/posts") && res.request().method() === "POST",
    { timeout: 600_000 }
  );
  await page.click('button[type="submit"]');
  const res = await responsePromise;
  const body = await res.json().catch(() => ({}));
  console.log("Step 4: POST /api/posts →", res.status());
  console.log("Post body:", JSON.stringify(body).slice(0, 500));

  if (!res.ok()) throw new Error("FAIL: post submission failed: " + JSON.stringify(body));

  // Persist result for the verification step
  fs.writeFileSync(RESULT_FILE, JSON.stringify({ title, status: res.status(), post: body }, null, 2));

  // Watch queue for completed status (YouTube resumable upload ~99.5MB takes a while)
  console.log("Waiting for upload to complete (queue polls /api/posts)…");
  let post = null;
  const deadline = Date.now() + 900_000; // 15 min
  while (Date.now() < deadline) {
    const postsRes = await page.request.get(APP + "/api/posts");
    const posts = await postsRes.json();
    post = (posts || []).find((p) => p.title === title);
    if (post && (post.status === "completed" || post.status === "failed")) break;
    await page.waitForTimeout(5000);
  }

  console.log("Queue status:", post?.status, "| publishedUrl:", post?.publishedUrl);
  if (post?.status !== "completed" || !post?.publishedUrl) {
    throw new Error("FAIL: post did not complete. Error: " + (post?.error || "timeout"));
  }

  // Persist auth state for reruns
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  await context.storageState({ path: STATE_FILE });

  // Step 5 — verify YouTube actually accepted the video
  const watchUrl = post.publishedUrl;
  const videoId = watchUrl.split("v=")[1]?.split("&")[0];

  const oembed = await fetch(
    `https://www.youtube.com/oembed?url=${encodeURIComponent(watchUrl)}&format=json`
  );
  console.log("oEmbed HTTP:", oembed.status);
  let oembedBody = null;
  if (oembed.ok) {
    oembedBody = await oembed.json();
    console.log("oEmbed title:", oembedBody.title, "| author:", oembedBody.author_name);
  }

  fs.writeFileSync(
    RESULT_FILE,
    JSON.stringify(
      {
        title,
        post,
        watchUrl,
        videoId,
        oembedStatus: oembed.status,
        oembedBody,
        consoleErrors: consoleErrors.slice(0, 10),
      },
      null,
      2
    )
  );

  if (oembed.status === 200) {
    console.log("\nSUCCESS: YouTube accepted the upload.");
    console.log("Watch URL:", watchUrl);
    console.log("Evidence written to e2e/result.json");
  } else if ([401, 403].includes(oembed.status)) {
    // private/unlisted videos → oEmbed may still 200 for unlisted; private 403.
    console.log("oEmbed not public — checking watch page instead…");
    const watch = await fetch(watchUrl);
    console.log("Watch page HTTP:", watch.status);
  } else {
    throw new Error(`FAIL: oEmbed returned ${oembed.status} — video not found on YouTube`);
  }

  await browser.close();
})().catch((err) => {
  console.error("E2E TEST FAILED:", err.message);
  process.exit(1);
});
