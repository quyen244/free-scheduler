// E2E acceptance test: local app → real YouTube upload of final.mp4
//
// Flow:
// 1. Load workspace at http://localhost:3000 — NO app login required.
// 2. Verify a YouTube account exists (google Account row in DB). If missing,
//    PAUSE: user authorizes at /login in their NORMAL browser (Google blocks
//    sign-in inside automation Chromium — "This browser or app may not be
//    secure"). We NEVER touch the password. Poll /api/social/accounts.
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

async function ensureYouTubeAccount(page) {
  // The publish path resolves the google Account row from the DB (local-mode
  // tier 2) — a live session cookie is NOT required for uploads. Google also
  // BLOCKS sign-in inside automation-controlled Chromium ("This browser or
  // app may not be secure"), so authorization MUST happen in the user's
  // normal browser. This test detects it via the accounts API.
  const hasYouTube = async () => {
    try {
      const res = await page.request.get(APP + "/api/social/accounts");
      const accounts = await res.json().catch(() => []);
      return (accounts || []).some((a) => a.platform_name === "youtube");
    } catch (e) {
      return false;
    }
  };

  if (await hasYouTube()) {
    console.log("YouTube account found in DB — no sign-in needed.");
    return;
  }

  console.log("\n=====================================================");
  console.log("ACTION REQUIRED: Google authorization needed.");
  console.log("Google BLOCKS sign-in inside automated browsers, and the");
  console.log("test now runs headless (no window) — so authorize in");
  console.log("your NORMAL Chrome window:");
  console.log("");
  console.log("  1. Open  http://localhost:3000/login");
  console.log("  2. Click 'Authorize with Google' + complete sign-in");
  console.log("     (if 'unverified app' shown → Advanced → continue)");
  console.log("  3. Return here — test continues automatically");
  console.log("=====================================================\n");

  const deadline = Date.now() + 3_600_000; // 1 hour
  while (Date.now() < deadline) {
    if (await hasYouTube()) {
      console.log("Google authorization detected — continuing.");
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error(
    "Timed out waiting for Google authorization (1 hour) — complete sign-in at http://localhost:3000/login in your normal browser."
  );
}

(async () => {
  // Headless: the ONLY visible Chrome on the user's screen is their real one,
  // which is where Google authorization must happen (Google blocks automation
  // browsers — "This browser or app may not be secure"). A visible test window
  // invites the user to sign in inside the blocked browser by mistake.
  const browser = await chromium.launch({ headless: true });
  const contextOpts = { acceptDownloads: true };
  // storageState is optional now — publish auth comes from the DB Account row.
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

  // Step 2 — ensure Google auth (user authorizes in their NORMAL browser;
  // this poll detects the resulting DB row via the accounts API)
  await ensureYouTubeAccount(page);

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

  // Persist auth state for reruns (session cookie only; publish uses DB row)
  if (context) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    await context.storageState({ path: STATE_FILE });
  }

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
