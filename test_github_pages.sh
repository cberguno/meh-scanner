#!/bin/bash
# Test script: Simulate GitHub Pages deployment locally
# This helps verify dashboard works with base path before pushing to GitHub

set -e

FAIL_COUNT=0

test_result() {
    if [ $? -eq 0 ]; then
        echo "  ✅ $1"
    else
        echo "  ❌ $1"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

echo "=========================================="
echo "🧪 GitHub Pages Deployment Test Suite"
echo "=========================================="
echo ""

# Test 1: Local (no base path)
echo "TEST 1: Local deployment (no base path)"
echo "  Config: MEH_DASHBOARD_BASE_PATH=''"
export MEH_DASHBOARD_BASE_PATH=""
python main.py > /dev/null 2>&1
test_result "Scanner ran successfully"

[ -f public/index.html ]
test_result "index.html created"

[ -f public/latest.json ]
test_result "latest.json created"

[ -f public/.nojekyll ]
test_result ".nojekyll created"

if [ -f public/index.html ]; then
    head -n 1 public/index.html | grep -q "<!DOCTYPE" 2>/dev/null
    test_result "index.html is valid HTML"

    grep -q '<table' public/index.html 2>/dev/null
    test_result "HTML contains table element"
fi

echo ""

# Test 2: GitHub Project Pages (with base path)
echo "TEST 2: GitHub Project Pages (simulated /meh-scanner/ path)"
echo "  Config: MEH_DASHBOARD_BASE_PATH='/meh-scanner/'"
export MEH_DASHBOARD_BASE_PATH="/meh-scanner/"
python main.py > /dev/null 2>&1
test_result "Scanner ran with base path"

[ -f public/index.html ]
test_result "index.html created with base path"

if [ -f public/index.html ]; then
    grep -q '<base href="/meh-scanner/"' public/index.html 2>/dev/null
    test_result "<base> tag injected correctly"

    # Verify latest.json link is relative
    grep -q './latest.json' public/index.html 2>/dev/null
    test_result "latest.json link is relative (./latest.json)"
fi

[ -f public/.nojekyll ]
test_result ".nojekyll present with base path"

nojekyll_size=$(wc -c < public/.nojekyll 2>/dev/null || echo "0")
if [ "$nojekyll_size" -eq 0 ]; then
    echo "  ✅ .nojekyll is empty (correct)"
else
    echo "  ⚠️ .nojekyll is ${nojekyll_size} bytes (should be empty, but ok)"
fi

echo ""

# Test 3: File integrity
echo "TEST 3: File integrity checks"

html_size=$(wc -c < public/index.html)
if [ "$html_size" -gt 1000 ]; then
    echo "  ✅ index.html is $html_size bytes (reasonable size)"
else
    echo "  ⚠️ index.html is only $html_size bytes (might be too small)"
fi

json_size=$(wc -c < public/latest.json)
if [ "$json_size" -gt 100 ]; then
    echo "  ✅ latest.json is $json_size bytes (valid)"
else
    echo "  ⚠️ latest.json is only $json_size bytes (might be empty or malformed)"
fi

echo ""

# Test 4: Local server test (optional)
echo "TEST 4: Verify files can be served locally"
if command -v python3 &> /dev/null; then
    echo "  Starting local web server on http://localhost:8000/public/"
    echo "  (Ctrl+C to stop after verification)"
    echo ""
    echo "  To test:"
    echo "    1. Open http://localhost:8000/public/index.html"
    echo "    2. Check if table loads and filters work"
    echo "    3. Check browser DevTools Network tab for any 404s"
    echo ""
    echo "  Ready to start? Press Enter..."
    read -r
    cd public && python3 -m http.server 8000 || true
    cd ..
else
    echo "  ⚠️ python3 not found, skipping local server test"
fi

echo ""
echo "=========================================="
if [ $FAIL_COUNT -eq 0 ]; then
    echo "✅ All tests passed!"
    echo ""
    echo "Your deployment is ready. Next steps:"
    echo "  1. git add ."
    echo "  2. git commit -m 'Add/update dashboard'"
    echo "  3. git push"
    echo ""
    echo "  Then:"
    echo "  4. Go to Actions tab and wait for workflow to complete"
    echo "  5. Check workflow summary for deployment status"
    echo "  6. Configure GitHub Pages settings (one-time):"
    echo "     Settings → Pages → Deploy from branch: gh-pages / root"
    echo "  7. Visit: https://<username>.github.io/<repo>/"
else
    echo "⚠️ $FAIL_COUNT test(s) failed"
    echo ""
    echo "Review the failures above. Common issues:"
    echo "  - .nojekyll not created → check dashboard_export.py"
    echo "  - base tag not injected → check MEH_DASHBOARD_BASE_PATH"
    echo "  - files too small → scanner may not have found deals"
    exit 1
fi
echo "=========================================="
