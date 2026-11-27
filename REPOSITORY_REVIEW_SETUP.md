# Arcade Intelligence — per-item owner review system

## What changes

- The agent continues its existing badge discovery and programme/community monitoring jobs.
- Discovered changes are compared with `agent-data/approved-knowledge.json` and written as proposals to `agent-data/review-queue.json`.
- A digest is checked daily, but a successful digest is sent no more often than once every 48 hours. All currently pending proposals are included in that one email.
- Every update has its own Approve and Reject buttons. A button opens an item-specific confirmation page; the app endpoint must require a second explicit confirmation. A decision for one item never approves the other cards.
- Only an approved item is written to `approved-knowledge.json`. Rejected items remain recorded as rejected. The discovery feed is not the approved knowledge database.
- Existing skill-badge, game and completion-catalogue entries are copied into the approved database once as the starting baseline. Existing programme announcements and YouTube observations remain pending unless the old watcher already recorded an explicit approval. This avoids treating unreviewed programme posts as pre-approved. Future additions and material edits go through owner review.

## Upload through GitHub on Android

This setup uses three small ZIP files. The source ZIP is auto-extracted by a new repository workflow. The five workflow YAML files are uploaded directly by you, because GitHub's built-in Actions token cannot write files under `.github/workflows`.

### Step 1 — install the ZIP importer once

1. Extract `Arcade-Intelligence-Importer-Bootstrap.zip` on the phone.
2. In the repository, open `.github/workflows` → **Add file** → **Upload files**.
3. Upload the single file `import-review-system.yml` from the extracted bootstrap folder.
4. Commit message: `Add review-system ZIP importer`.

### Step 2 — upload the source, database and test ZIP

1. Return to the repository root.
2. Upload `Arcade-Intelligence-Review-System.zip` with that exact filename. Do not extract this ZIP.
3. Commit message: `Upload Arcade Intelligence review code and data`.
4. Open **Actions** → **Import Arcade Intelligence review code and data ZIP**. A green check means the ZIP was safely extracted, all tests passed, and the source/data/test changes were committed. A red cross means stop and inspect the failed step.
5. After a successful run, the ZIP is removed from the repository and its files appear in `agent/`, `agent-data/`, `tests/`, and the root setup guide.

### Step 3 — upload the five workflow YAML files

1. Extract `Arcade-Intelligence-Workflow-Updates.zip` on the phone.
2. Open the extracted `workflows` folder. Select these five files together:
   - `arcade-intelligence.yml`
   - `arcade-program-watch.yml`
   - `arcade-intelligence-ci.yml`
   - `arcade-review-digest.yml`
   - `arcade-review-apply.yml`
3. In GitHub, open `.github/workflows` → **Add file** → **Upload files**.
4. Upload the five selected files. GitHub will replace the three existing workflow files and add the two new ones.
5. Commit message: `Update Arcade Intelligence review workflows`.
6. Open **Actions** and confirm the Arcade Intelligence CI workflow passes. Do not run the 48-hour email digest yet; its signed links become active only after the app endpoint is deployed in the next phase.

Do not upload any ZIP into `agent/` or `agent-data/`. The importer ZIP belongs at the repository root. The five `.yml` files belong directly inside `.github/workflows/`.

## Required repository secrets and variable — configure during app integration

The email workflow intentionally does not send until all four required values exist:

- Repository **Settings → Secrets and variables → Actions → New repository secret**: `RESEND_API_KEY` — use the same Resend API key already used by the website. Never put its value in a tracked file or email.
- Secret: `OWNER_EMAIL` — the inbox that should receive the digest.
- Secret: `AGENT_REVIEW_SECRET` — generate a new long random secret; use the exact same value in the Netlify app function environment. Do not reuse the Resend key.
- Repository **Settings → Secrets and variables → Actions → Variables → New repository variable**: `AGENT_REVIEW_BASE_URL` — set this only after the app phase deploys the `/api/agent-review` confirmation endpoint. Use the HTTPS site origin, for example `https://your-site.netlify.app`.
- Optional secret: `MAIL_FROM` — only set this if the sender is permitted by the Resend account. If omitted, the sender defaults to `Arcade Tracker <onboarding@resend.dev>`, subject to Resend's sender restrictions.

The Netlify site's environment variables are not automatically available to GitHub Actions. The repository secrets above are separate copies. Never paste secret values into this public repository.

## App integration is the next phase, not included in this repository ZIP

The signed review links need a Netlify Function at `/api/agent-review`. The app phase must implement all of the following before setting `AGENT_REVIEW_BASE_URL` or expecting working email buttons:

1. Verify the HMAC signature using `AGENT_REVIEW_SECRET`; reject expired or malformed links.
2. On GET, show the exact proposed item and a confirmation form only. Do not approve/reject on GET because email security scanners can open links automatically.
3. On explicit POST, record the signed token as used in persistent Netlify Blobs and call GitHub's `workflow_dispatch` endpoint for `arcade-review-apply.yml` with that one `item_id` and its decision.
4. Keep the GitHub token server-side in Netlify as `GITHUB_AGENT_TOKEN`, scoped only to the `arcade-intelligence-2.0` repository with Actions read/write permission. Never expose it to browser JavaScript.
5. Return a clear result page and make repeated clicks idempotent. The workflow itself refuses to change an item that already has a final decision.
6. Make the app chatbot read approved knowledge only from `agent-data/approved-knowledge.json`; it must not treat `public-feed.json` or `review-queue.json` as approved facts.

The existing Hot Update email and its approval flow are separate. Do not replace or merge that flow when implementing `/api/agent-review`.

## Safety and data rules

- The 48-hour interval is enforced by the script, not by assuming GitHub schedule precision. GitHub may delay scheduled workflows.
- No digest is sent if an API key, owner address, review secret, or review endpoint base URL is missing.
- No point value, current eligibility, event availability, or deadline is inferred merely because a source was found.
- Removals from a discovery feed are not automatically deleted from approved knowledge. This version handles new records and material edits; it deliberately avoids deleting approved records based on a temporary crawler miss.
- The existing `public-feed.json` remains the discovery feed for compatibility. The app must switch to the approved-only database in the separate app phase.
