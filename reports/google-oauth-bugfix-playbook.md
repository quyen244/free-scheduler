# Bug Report: Google OAuth Sign-In / YouTube Upload Failures — Local Mode

Fix playbook for the SaaS-stripped local app (LOCAL_MODE=true). Every failure below was
hit and root-caused during the 2026-09-08/09 E2E effort. Use this to skip hours of
diagnosis when Google OAuth or YouTube publishing breaks again.

App: `http://localhost:3000` · DB: `youtube_db` @ 127.0.0.1:5432 · E2E: `node e2e/upload.spec.js`

---

## Symptom → Fix Table (fast path)

| # | Symptom | Root cause | Fix | Time |
|---|---------|-----------|-----|------|
| 1 | Publish fails: `YouTube authorization is missing a refresh token. Sign out and sign in again.` | `PrismaAdapter.linkAccount()` writes `refresh_token_expires_in`; `Account` model lacked that column → `LinkAccountError` → tokens never stored | Add `refresh_token_expires_in Int?` to Account model → `npx prisma db push` → `npx prisma generate` → **restart dev server** → re-consent at `/login` | 30 min incl. propagation |
| 2 | Google sign-in screen shows `This browser or app may not be secure` | Google anti-automation policy: blocks OAuth in CDP-controlled browsers (Playwright bundled Chromium) | Never sign in inside the Playwright window. Test runs headless; user authorizes at `/login` in their **normal Chrome** | 0 after setup |
| 3 | Consent accepted → browser shows `localhost refused to connect` / lands nowhere | Usually NOT a port problem — same-family cause as #1: callback ran, `linkAccount` threw, NextAuth bounced to error page (or stale dev.log misled) | Check real server output (not stale dev.log) with `NEXTAUTH_DEBUG=true`; look for `LinkAccountError` / `Unknown argument refresh_token_expires_in` | varies |
| 4 | Consent screen Vietnamese/English: `app chưa hoàn tất xác minh … only testers` 403 `access_denied` | OAuth consent screen in **Testing** mode; user's email not a test user | https://console.cloud.google.com/auth/audience → project **ytb-scheduler** → Test users → **+ Add users** → add Google email. Alternative: **Publish app** (In production) | 2 min |
| 5 | Publish fails: `YouTube Data API v3 has not been used in project … or it is disabled` (403) | YouTube Data API v3 disabled in Cloud project 187536018252 | Open the URL inside the error message → **ENABLE** → wait ~1 min propagation → retry post | 5 min |
| 6 | Old post rows show `status: failed` with auth-type errors | Stale tokens from before the schema fix (row had expired access_token, no refresh_token, scope missing `youtube.upload`) | Reconnect YouTube button in UI (see § UI) or purge: `node --env-file=.env scripts/db-inspect-accounts.js --purge` then re-consent | 10 min |
| 7 | E2E: `Timed out waiting for Google sign-in` | Nobody at machine to authorize — genuine external blocker (not a bug) | Run test when a human is present; authorize in normal Chrome; test auto-detects via `/api/social/accounts` poll | — |
| 8 | E2E dies: `Target page, context or browser has been closed` | User closed the Playwright window mid sign-in | (Historical — auth no longer happens in test browser.) If seen again: rerun `node e2e/upload.spec.js` | 1 min |

---

## Architecture (why it works this way)

Publish path does NOT need a browser session. Tokens live in the DB:

```
User authorizes at /login (normal Chrome)
  → NextAuth callback → PrismaAdapter → Account row
     (access_token, refresh_token, expires_at, scope incl. youtube.upload)
  → publish: getAppSession() → google Account row (tier 2)
  → youtube.js getAccessToken()
       valid? use access_token
       expired? refreshAccessToken() — refresh_token → Google token endpoint
               → new access_token, DB row updated (no user action)
  → resumable upload → youtube.com
```

Local-mode session resolution (src/lib/local-mode.js), in order:
1. Real NextAuth Google session (browser cookie)
2. User owning the stored google Account row ← normal publish path
3. `local@localhost` placeholder (fresh DB; no YouTube publishing until consent)

Token lifetimes:
- **access_token** ~1 hour → auto-refreshed in code, invisible.
- **refresh_token** months–years → only manual re-consent if revoked, >50 sign-ins
  issue a newer one, or ~6 months unused. Normal use: consent once, never again.

---

## Key Files

| File | Role |
|------|------|
| `src/lib/auth.js` | NextAuth options. MUST keep `access_type: "offline"`, `prompt: "consent"`, scope incl. `https://www.googleapis.com/auth/youtube.upload` — otherwise no refresh_token / no upload rights |
| `prisma/schema.prisma` | `Account` model incl. `refresh_token_expires_in Int?` — the #1 bug column |
| `src/lib/youtube.js` | Token refresh + resumable upload. Throws the two auth errors the UI detects |
| `src/lib/local-mode.js` | `getAppSession()` 3-tier resolution |
| `src/app/api/social/accounts/route.js` | Returns YouTube account when google Account row exists — E2E auth-detection poll target |
| `src/app/page.js` | Reconnect YouTube banner + queue-card button (auth-error regex) |
| `e2e/upload.spec.js` | Full acceptance test: no-login load → auth detection → upload → publish → oEmbed verify |
| `e2e/reconnect-button.spec.js` | Seeds failed auth post in DB → verifies button renders |
| `scripts/db-inspect-accounts.js` | Show/purge google Account rows (`--purge`) |
| `scripts/db-inspect-users.js` | Show User/Account/Session rows |
| `scripts/db-seed-failed-post.js` | Seed a failed post for UI tests |

---

## Diagnostic Commands

```powershell
# Account row health: refresh_token present? token expired? scope includes youtube.upload?
node --env-file=.env scripts/db-inspect-accounts.js
# → expect: token=valid, refresh_token=YES, scope contains youtube.upload

# Purge broken rows (forces fresh consent; use when refresh_token=NO repeatedly)
node --env-file=.env scripts/db-inspect-accounts.js --purge

# User/Session state
node --env-file=.env scripts/db-inspect-users.js

# Dev server with NextAuth debug (see linkAccount errors live)
$env:NEXTAUTH_DEBUG = "true"; npm run dev
# Then watch for: "LinkAccountError", "Unknown argument `refresh_token_expires_in`",
# "GET /api/auth/callback/google ... 302" (callback DID arrive)

# Probe callback route (expect 302, not connection refused)
curl.exe -s -o NUL -w "%{http_code}" http://localhost:3000/api/auth/callback/google

# Full E2E (human needed only if DB has no valid google Account row)
node e2e/upload.spec.js
```

Gotchas learned the hard way:
- **Stale dev.log**: a dev server started from an old session buffers output to a dead
  file. Verify the PID (`netstat -ano | findstr :3000`) and its parent chain before
  trusting any log. Restart with debug env when in doubt.
- `prisma db push` said "already in sync" on second run — the first run had already
  applied the migration. Column presence ≠ Prisma Client knows it. **Always
  `npx prisma generate` after schema change, and restart the dev server** — a running
  server keeps the old generated client in memory.
- Google sign-in NEVER works inside Playwright Chromium (bundles CDP + automation
  flags; "This browser or app may not be secure"). Community workarounds
  (`ignoreDefaultArgs: ['--enable-automation']`, stealth plugins, Firefox) are
  unreliable — do not chase them.
- OAuth redirect URI must be exactly `http://localhost:3000/api/auth/callback/google`
  in Cloud Console (Auth clients page). Mismatch → post-consent dead end.

---

## UI Self-Fix (Reconnect YouTube)

When publish fails with an auth-type error — regex in `src/app/page.js`
`isAuthError()` matches `refresh token | authorization | sign in | oauth | invalid_grant | expired`:

- **Submit failure** → red banner with exact error + `Reconnect YouTube` button
  → routes to `/login` → user consents → returns → resubmits.
- **Failed queue item** (platform=youtube, auth error) → inline `Reconnect YouTube`
  next to Retry/Delete.

Non-auth errors keep normal alert behavior (no noise).

---

## Google Cloud Console Checklist (project 187536018252 / ytb-scheduler)

1. **OAuth consent screen** — https://console.cloud.google.com/auth/audience
   - Testing mode: user email must be in **Test users** (else 403 access_denied)
   - Or publish app (In production) — unverified warning OK for personal use
2. **OAuth client** — https://console.cloud.google.com/auth/clients
   - Authorized redirect URI: `http://localhost:3000/api/auth/callback/google`
3. **APIs** — YouTube Data API v3 **enabled** (else 403 "has not been used in project")
4. Scopes: `openid email profile https://www.googleapis.com/auth/youtube.upload`

---

## E2E Success Baseline (2026-09-09)

- Video live: https://www.youtube.com/watch?v=vn0DUeLORCo
- oEmbed 200, title `[E2E Local Test] 2026-09-09T02:39:01.041Z`, author `Mindset Forge`
- Post `cmtthq70c0001bwo3bf2ixdh1`, mediaUrl `/uploads/bf99b76f-51dd-47cc-b596-63c16ccd54e1.mp4`
- Evidence: `e2e/result.json`
- Video source: `D:\Projects\Assignment\Free-AI-Social-Media-Scheduler\automation\data\3gi_15UH9fQ.bench-backup\processed\final.mp4` (99.5 MB)

If a future run fails, diff its output against this baseline: Step 1 no-login OK →
accounts poll OK → Step 3a/3b/3c → POST 200 → queue completed → oEmbed 200.
