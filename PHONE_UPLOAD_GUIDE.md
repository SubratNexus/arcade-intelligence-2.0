# Android setup guide — Arcade Intelligence Agent v1.2

This guide is tailored to the GitHub repository used in this project:
`SubratNexus/arcade-intelligence-2.0`.

## A. Download and extract the complete package

1. Download `Arcade-Intelligence-Agent-v1.2-Full-Setup.zip` from this chat.
2. Open your phone's Files/File Manager app.
3. Open **Downloads** and extract the ZIP.
4. Open the extracted folder `Arcade-Intelligence-Agent-v1.2-Full-Setup`.
5. Keep this folder. Do not upload the ZIP itself.

The package includes every source file, initial JSON file, test, workflow text, and guide required by this setup.

## B. Open the repository — do not delete it again

The exact repository URL was checked on 26 November 2026. It is currently publicly visible, uses the `main` branch, and its root currently shows `.github/workflows` and `agent`. The existing workflow file is present, but the expected `agent/discover.py` raw file currently returns 404. That means the repository still exists, but the agent source needs to be uploaded again.

1. Open this link in Chrome: `https://github.com/SubratNexus/arcade-intelligence-2.0`.
2. Sign in to the GitHub account that owns `SubratNexus`.
3. Confirm you are viewing `SubratNexus/arcade-intelligence-2.0` and the `main` branch.
4. Reuse this repository. Do not create a second repository and do not delete this one.

If GitHub itself shows **404 / Page not found** after you sign in, then the repository may actually have been deleted or you may be signed into a different account. Only in that case create it again with owner `SubratNexus`, name `arcade-intelligence-2.0`, visibility **Public**, and leave the README, `.gitignore`, and License options unchecked. Then continue with the steps below.

Public repository warning: anyone can view the code, workflow runs/logs, catalogue, public source links, and repository owner name. Never upload passwords, API keys, private profile URLs, or personal information.

## C. Upload the three files into the repository root

The root means the first page of the repository — not inside `agent` or another folder.

1. Open the repository main page.
2. Tap **Add file → Upload files**. On an empty repository, use the **uploading an existing file** link if GitHub shows its Quick setup screen.
3. Tap **Choose your files**.
4. In Android Files, open **Downloads → Arcade-Intelligence-Agent-v1.2-Full-Setup**. Stay in this main extracted folder.
5. Select exactly these three files:
   - `README.md`
   - `PHONE_UPLOAD_GUIDE.md`
   - `WORKFLOW_FOR_MANUAL_CREATE.txt`
6. Return to GitHub. Commit message: `Add project documentation`. Tap **Commit changes**.

If Android does not allow selecting multiple files, upload each file separately. Start each upload from the repository root.

## D. Upload the three actual program files into `agent`

The `agent` folder is already visible in the repository. Open it. If it is missing in your GitHub view, create it first by choosing **Add file → Create new file**, entering `agent/.gitkeep` in the filename field, typing `Folder marker` as content, and committing.

1. Open the `agent` folder.
2. Tap **Add file → Upload files**. Or open this direct page while signed in:
   `https://github.com/SubratNexus/arcade-intelligence-2.0/upload/main/agent`
3. In Android Files, open **Downloads → Arcade-Intelligence-Agent-v1.2-Full-Setup → agent**.
4. Select exactly:
   - `discover.py`
   - `requirements.txt`
   - `README.md`
5. Return to GitHub. Commit message: `Upload Arcade Intelligence agent`. Commit to `main`.

These three files must appear inside the `agent` folder. Do not put them at the repository root.

## E. Create `agent-data` and upload its three real JSON files

If `agent-data` already exists, open it and skip the folder-creation steps.

1. Return to the repository root.
2. Choose **Add file → Create new file**.
3. In the filename/path box, enter exactly: `agent-data/.gitkeep`
4. In the file content box, enter: `Folder marker`
5. Commit message: `Create agent-data folder`. Commit to `main`.
6. Open `agent-data` → **Add file → Upload files**. Or open:
   `https://github.com/SubratNexus/arcade-intelligence-2.0/upload/main/agent-data`
7. In Android Files, open **Downloads → Arcade-Intelligence-Agent-v1.2-Full-Setup → agent-data**.
8. Select exactly:
   - `catalogue.json`
   - `state.json`
   - `last-run.json`
9. Commit message: `Upload initial catalogue data`. Commit to `main`.

These are the supplied JSON files. Do not replace their contents with `[]` or hand-typed sample text.

## F. Create `tests` and upload the test file

If `tests` already exists, open it and skip folder creation.

1. Return to repository root → **Add file → Create new file**.
2. Filename/path: `tests/.gitkeep`
3. File content: `Folder marker`
4. Commit message: `Create tests folder`. Commit to `main`.
5. Open `tests` → **Add file → Upload files**. Or open:
   `https://github.com/SubratNexus/arcade-intelligence-2.0/upload/main/tests`
6. In Android Files, open **Downloads → Arcade-Intelligence-Agent-v1.2-Full-Setup → tests**.
7. Select `test_classifier.py`.
8. Commit message: `Upload classifier tests`. Commit to `main`.

## G. Replace the existing workflow with the complete v1.2 workflow

The existing repository already has `.github/workflows/arcade-intelligence.yml`. Edit that file; do not create a second workflow with a different name.

1. Return to repository root.
2. Open `.github` → `workflows` → `arcade-intelligence.yml`.
3. Tap the pencil **Edit** button.
4. On your phone, open `WORKFLOW_FOR_MANUAL_CREATE.txt` from the extracted package in a text editor.
5. Select and copy **all** text in that file, starting at `name:` and ending after the final `fi`.
6. Return to GitHub's editor. Select the old workflow text, delete it, and paste the complete copied text.
7. Scroll down. Commit message: `Update Arcade Intelligence workflow v1.2`.
8. Tap **Commit changes** and commit to `main`.

If you cannot find the existing workflow file, create a new file with this exact path:
`.github/workflows/arcade-intelligence.yml`
Then paste the same complete content from `WORKFLOW_FOR_MANUAL_CREATE.txt` and commit it.

The leading dot in `.github` is required. The extension must be `.yml`, not `.txt`.

## H. Check the final repository file tree

It should contain at least these exact files:

```text
.github/
  workflows/
    arcade-intelligence.yml
README.md
PHONE_UPLOAD_GUIDE.md
WORKFLOW_FOR_MANUAL_CREATE.txt
agent/
  discover.py
  requirements.txt
  README.md
agent-data/
  catalogue.json
  state.json
  last-run.json
tests/
  test_classifier.py
```

You may also see `.gitkeep` marker files inside folders you created. They are harmless; you can leave them.

## I. Run the workflow once

1. Open the repository's **Actions** tab.
2. If GitHub asks to enable Actions, enable them for this repository.
3. Select **Arcade Intelligence — public badge discovery** in the left list.
4. Tap **Run workflow**, keep branch `main`, then tap the green **Run workflow** button.
5. Open the new run and inspect its result.
6. A green check means the job completed. A red cross means it failed; open the failed step and read the error before changing files.

The workflow first installs dependencies and runs five classifier tests, then installs Tesseract OCR and runs the limited public-page discovery. A successful run may still find zero badges; this does not prove that all Google Skills pages were searched.

## J. Check the public JSON feed

After a successful run, open:

`https://raw.githubusercontent.com/SubratNexus/arcade-intelligence-2.0/main/agent-data/catalogue.json`

It should display JSON. Before a successful run, its status will say `not_run_yet`.

Only connect this URL to Arcade Tracker after confirming that the deployed website version includes its public catalogue-feed connection feature. This GitHub setup does not deploy or change the website.

## Important limits and costs

- This is OCR plus explicit-label classification, not generative AI or Gemini. No AI API key is required.
- It does not sign in, crawl user profiles, infer special +2 point status, or award points.
- It checks a limited set of publicly accessible allowlisted pages/images and may miss dynamic, restricted, or unreadable content.
- GitHub may delay scheduled runs. The schedule is 07:37 UTC (13:07 IST) daily; manual **Run workflow** is also available.
- Do not add secrets or personal data to this public repository.
