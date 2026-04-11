# GitHub Pages Deployment Checklist

## Step 1: Pre-Deployment Testing (Local)
```bash
bash test_github_pages.sh
```
✅ All tests should pass
✅ Files created: `public/index.html`, `public/latest.json`, `public/.nojekyll`
✅ Base tag injected when `MEH_DASHBOARD_BASE_PATH` is set

## Step 2: Push to GitHub
```bash
git add .
git commit -m "Add GitHub Pages deployment with hardened .nojekyll handling"
git push
```

## Step 3: Monitor Workflow
1. Go to: **GitHub Repo** → **Actions** tab
2. Click **Daily Meh-Scanner** (most recent run)
3. Wait for completion (usually 2-5 minutes)
4. Check **Workflow Summary** (below the job log):
   - **Dashboard Files** table:
     - index.html: ✅
     - latest.json: ✅  
     - .nojekyll: ✅
   - **Deployment**: success ✅

## Step 4: Configure GitHub Pages (One-Time Only)
1. Go to: **Repo Settings** → **Pages**
2. Under **"Build and deployment"**:
   - Source: `Deploy from a branch`
   - Branch: `gh-pages`
   - Folder: `/ (root)`
3. Click **Save**
4. You should see: "Your site is live at: https://..."

## Step 5: Verify Live Site
```
URL: https://<username>.github.io/<repo>/
```

### What You Should See:
- **Dark-themed table** with column headers
- **Filter search box** (top left)
- **Min score dropdown** (top right)
- **"Last updated" timestamp** (in gray text)
- **Deal rows** with data (if any found)
- **Color highlights**:
  - Blue bar on left = score ≥7
  - Green bar on left = score ≥8

### Test Interactivity:
1. Type in the search box → table filters instantly
2. Change "Min score" dropdown → table updates
3. Click column headers → rows sort
4. Click deal URLs → opens in new tab

### If Page Doesn't Load:
1. **Hard-refresh**: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac)
2. **Wait 1-2 minutes** (first deploy is slower)
3. **Open DevTools** (F12):
   - Network tab: Check for 404 errors
   - Console tab: Check for JS errors
4. **Check Settings again**: Make sure Pages shows "Your site is live at..."

## Step 6: Troubleshooting (If Needed)

### See "404 Not Found"?
→ Check **Troubleshooting** section in [GITHUB_PAGES_SETUP.md](GITHUB_PAGES_SETUP.md)

### See blank page or broken styling?
→ Hard-refresh (Ctrl+Shift+R) + wait 1-2 minutes

### Check DevTools for asset 404s?
→ See **"Base Path / Asset 404s"** in [GITHUB_PAGES_SETUP.md](GITHUB_PAGES_SETUP.md)

### Page loads but no data in table?
→ Check that deals qualified for quality_score ≥ 6 (see logs)

## Diagnostic Commands

**Check that gh-pages branch has the files:**
```bash
# View on GitHub: 
# Code → select 'gh-pages' branch → should see:
# - index.html
# - latest.json  
# - .nojekyll (empty file at root)
```

**Check workflow logs for issues:**
```
Actions → Daily Meh-Scanner → [Most recent run]
→ Look for step "Check dashboard output"
→ Should show ✅ for all three files
```

**Verify locally before pushing:**
```bash
# Test without base path
python main.py
ls -la public/

# Test with base path (simulates GitHub Pages)
export MEH_DASHBOARD_BASE_PATH="/meh-scanner/"
python main.py
grep "base href" public/index.html  # Should show: <base href="/meh-scanner/">
```

## Key Changes Made (This Deployment)

1. **Stronger .nojekyll handling**:
   - Uses explicit file write instead of touch
   - Verifies file was created
   - Logs critical errors if creation fails

2. **Hardened workflow checks**:
   - Explicit verification all files exist before deploy
   - Pre-deploy artifact inspection
   - Post-deploy verification step
   - Better error messages

3. **Improved base tag injection**:
   - Logs which base path is used
   - Verifies base tag is in HTML
   - Falls back gracefully if not configured

4. **Better documentation**:
   - Detailed troubleshooting guide
   - DevTools diagnostics
   - Verification checklist

## Expected Timeline

| Stage | Time |
|-------|------|
| Local tests | 1-2 min |
| Push to GitHub | < 1 min |
| Workflow runs | 2-5 min |
| Pages initializes | 1-2 min |
| **Total** | **5-10 min** |

## Questions?

See **[GITHUB_PAGES_SETUP.md](GITHUB_PAGES_SETUP.md)** for detailed troubleshooting and explanations.
