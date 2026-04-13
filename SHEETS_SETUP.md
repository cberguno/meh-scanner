# Google Sheets Setup — meh-scanner

End-to-end runbook for provisioning the Google Sheets integration. **Run these
commands on your local machine** (not inside Claude Code) — several steps open a
browser for OAuth consent.

The goal is to end up with:

- A GCP project `meh-scanner` with the Sheets API enabled
- A service account `meh-scanner-sa` with a downloaded JSON key
- The **existing** Google Sheet
  (`1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg`) shared with the service
  account email as Editor, with a tab named exactly `Deals`
- `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` (base64) set in both your
  local `.env` and as GitHub Actions secrets on `cberguno/meh-scanner`

---

## 0. Prerequisites

- macOS, Linux, or WSL
- A Google account
- Push access to `cberguno/meh-scanner`
- Python 3.12 available locally

---

## 1. Install `gcloud` CLI

### macOS (Homebrew)

```bash
brew install --cask google-cloud-sdk
```

### Linux / WSL

```bash
curl -sSL https://sdk.cloud.google.com | bash
exec -l $SHELL
```

Verify:

```bash
gcloud --version
```

---

## 2. Authenticate

```bash
gcloud auth login
```

Completes in your browser. Use the same Google account you want the sheet
shared with.

---

## 3. Create (or select) the GCP project

Project IDs are globally unique — if `meh-scanner` is taken, pick a suffix
like `meh-scanner-42` and substitute it everywhere below.

```bash
PROJECT_ID=meh-scanner
gcloud projects create "$PROJECT_ID" --name="meh-scanner" \
  || echo "(project already exists — continuing)"
gcloud config set project "$PROJECT_ID"
```

---

## 4. Enable required APIs

```bash
gcloud services enable sheets.googleapis.com
```

Drive API is not needed — the sheet already exists, and writes use the Sheets
API only.

---

## 5. Create the service account

```bash
gcloud iam service-accounts create meh-scanner-sa \
  --display-name="Meh-Scanner Service Account"

SA_EMAIL=$(gcloud iam service-accounts list \
  --filter="displayName:Meh-Scanner Service Account" \
  --format="value(email)")
echo "$SA_EMAIL"
```

---

## 6. Download the JSON key

```bash
gcloud iam service-accounts keys create ./meh-scanner-sa.json \
  --iam-account="$SA_EMAIL"
```

The key lands at `./meh-scanner-sa.json`. `.gitignore` already excludes
`*.json`, so git will not stage it — confirm with `git status`.

---

## 7. Share the existing sheet with the service account

Open the existing sheet in your browser:

```
https://docs.google.com/spreadsheets/d/1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg
```

1. Click **Share** (top-right).
2. In the "Add people and groups" field, paste the service account email
   captured in step 5 (`$SA_EMAIL`, looks like
   `meh-scanner-sa@<project-id>.iam.gserviceaccount.com`).
3. Set the role to **Editor**.
4. Uncheck "Notify people" (service accounts have no inbox).
5. Click **Share**.

Then confirm the sheet has a tab named exactly `Deals` (case-sensitive). If
not, rename the existing tab or add a new one called `Deals` — `sheets.py`
writes to `Deals!A:M` and will fail if the tab is missing.

The sheet ID to use everywhere below is:

```
1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg
```

---

## 8. Base64-encode the service account key

### Linux / WSL

```bash
base64 -w0 meh-scanner-sa.json > meh-scanner-sa.b64
```

### macOS

```bash
base64 -i meh-scanner-sa.json -o meh-scanner-sa.b64
```

Single-line output is required so it fits cleanly in a `.env` value.

---

## 9. Populate local `.env`

```bash
cp -n .env.example .env 2>/dev/null || true
```

Edit `.env` and set:

```
GOOGLE_SHEET_ID=1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg
GOOGLE_SERVICE_ACCOUNT_JSON=<paste contents of meh-scanner-sa.b64>
```

Remove any leftover `your_google_sheet_id_here` placeholder line.

---

## 10. Install `gh` CLI (if not already)

### macOS

```bash
brew install gh
```

### Linux / WSL

```bash
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list
sudo apt update && sudo apt install gh
```

Auth once:

```bash
gh auth login
```

---

## 11. Add GitHub Actions secrets

From the repo clone:

```bash
gh secret set GOOGLE_SHEET_ID \
  --repo cberguno/meh-scanner \
  --body "1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg"

gh secret set GOOGLE_SERVICE_ACCOUNT_JSON \
  --repo cberguno/meh-scanner \
  < meh-scanner-sa.b64
```

Verify:

```bash
gh secret list --repo cberguno/meh-scanner
```

Expected output should include both `GOOGLE_SHEET_ID` and
`GOOGLE_SERVICE_ACCOUNT_JSON`.

---

## 12. Clean up local key files

Once the key is in `.env` and GitHub secrets, the raw files on disk are a
liability. Delete them:

```bash
rm meh-scanner-sa.json meh-scanner-sa.b64
```

If you ever need to rotate, re-run steps 6 → 8 → 9 → 11 and delete the old key:

```bash
gcloud iam service-accounts keys list --iam-account="$SA_EMAIL"
gcloud iam service-accounts keys delete <OLD_KEY_ID> --iam-account="$SA_EMAIL"
```

---

## 13. Smoke test

```bash
py main.py
```

Open the sheet at
`https://docs.google.com/spreadsheets/d/1RAdedQhNpyTgM86z3eRDsbbT_h9tNTRCN9tMvkfh7Zg`
and confirm a new row landed in the `Deals` tab with all 13 columns
populated:

```
Site | URL | Niche | Score | Price | Was | Est. ROI % | Rationale |
Market Price | Market Source | Savings % | Confidence | Scanned At
```

If `Market Price` / `Market Source` / `Savings %` / `Confidence` are empty,
check that the upstream analyzer is populating `market_price`,
`market_source`, `verified_savings_pct`, and `match_confidence` on each deal
dict.
