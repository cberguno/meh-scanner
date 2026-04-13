"""
Write a glanceable daily dashboard under public/ for opening in a browser.

GitHub Actions: Settings → Pages → Source: **Deploy from a branch**, branch **gh-pages**, **/(root)**.

**Why .nojekyll:** Disables Jekyll so static HTML is served as-is (avoids blank/404 quirks).

**Why MEH_DASHBOARD_BASE_PATH in CI:** Project sites live at https://user.github.io/repo-name/; a
path-only ``<base href="/repo-name/">`` makes ./latest.json resolve under that prefix.

**Exception:** A repo named ``username.github.io`` (user site) uses URL root; override
``MEH_DASHBOARD_BASE_PATH=/`` in that case.
"""
from __future__ import annotations

import csv
import html as html_module
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from logger import logger
from scoring import CANDIDATE_SCORING_REFERENCE

PUBLIC_DIR       = Path("public")
INDEX_HTML       = PUBLIC_DIR / "index.html"
LATEST_JSON      = PUBLIC_DIR / "latest.json"
CANDIDATES_JSON  = PUBLIC_DIR / "candidates.json"
CANDIDATES_CSV   = PUBLIC_DIR / "candidates.csv"
NOJEKYLL         = PUBLIC_DIR / ".nojekyll"

CSV_COLUMNS = [
    "site_name",
    "url",
    "snippet",
    "discovery_source",
    "source_status",
    "vibe_score",
    "deal_title",
    "deal_price",
    "original_price",
    "meh_signals",
    "meh_signal_hits",
    "guardrail_flags",
    "scrape_method",
    "quality_score",
    "niche",
    "rationale",
    "accepted",
    "rejection_reason",
]


def candidate_row_for_csv(c: dict) -> dict:
    """Normalize types for CSV (booleans and optional numeric scores)."""
    row = {**c}
    row["accepted"] = "yes" if row.get("accepted") else "no"
    if row.get("quality_score") is None:
        row["quality_score"] = ""
    return row


def write_project_root_candidate_files(all_candidates: list[dict]) -> None:
    """Write ``candidates.csv`` and ``candidates.txt`` at the repo root (scanner / CLI)."""
    import os as _os
    import shutil

    _tmp = "candidates.csv.tmp"
    with open(_tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for c in all_candidates:
            w.writerow(candidate_row_for_csv(c))
    try:
        _os.replace(_tmp, "candidates.csv")
    except Exception:
        shutil.copy2(_tmp, "candidates.csv")

    lines: list[str] = []
    if not all_candidates:
        lines.append("NO CANDIDATES FOUND")
    else:
        lines.append("\t".join(CSV_COLUMNS))
        for c in all_candidates:
            rc = candidate_row_for_csv(c)
            lines.append(
                "\t".join(str(rc.get(col) if rc.get(col) is not None else "") for col in CSV_COLUMNS)
            )
    with open("candidates.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _normalize_base_path(path: str) -> str:
    path = path.strip()
    if not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    return path


def _base_tag_html() -> str:
    base = _normalize_base_path(Config.MEH_DASHBOARD_BASE_PATH)
    if not base:
        return ""
    return f'  <base href="{html_module.escape(base)}"/>\n'


def _pages_url_hint() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo or "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}/"


def _findings_table_rows(all_candidates: list[dict]) -> str:
    """HTML table body rows for every analyzed URL (escaped)."""
    if not all_candidates:
        return (
            '<tr><td colspan="8" style="text-align:center;color:#a9b1d6">'
            "No analyzed URLs this run.</td></tr>"
        )
    lines: list[str] = []
    for c in all_candidates:
        vibe = c.get("vibe_score")
        vibe_s = "—" if vibe is None or vibe == "" else html_module.escape(str(vibe))
        qs = c.get("quality_score")
        q_s = "—" if qs is None else html_module.escape(str(qs))
        acc = c.get("accepted")
        result = "yes" if acc else "no"
        row_cls = "" if acc else "row-rejected"
        name = html_module.escape(str(c.get("site_name") or ""))
        niche = html_module.escape(str(c.get("niche") or ""))
        reason = html_module.escape(str(c.get("rejection_reason") or ""))
        rat = str(c.get("rationale") or "")
        if len(rat) > 320:
            rat = rat[:320] + "…"
        rat_e = html_module.escape(rat)
        url = c.get("url") or ""
        link = html_module.escape(url)
        lines.append(
            f"<tr class='{row_cls}'>"
            f"<td>{vibe_s}</td><td>{q_s}</td><td>{name}</td><td>{niche}</td>"
            f"<td>{result}</td><td>{reason}</td><td class='rationale'>{rat_e}</td>"
            f"<td><a href=\"{link}\" target=\"_blank\" rel=\"noopener\">link</a></td></tr>"
        )
    return "\n".join(lines)


def export_daily_dashboard(
    deals: list[dict],
    *,
    candidates_count: int = 0,
    runtime_seconds: float = 0.0,
    scan_summary: dict | None = None,
    all_candidates: list[dict] | None = None,
) -> None:
    if not Config.MEH_DASHBOARD:
        logger.info("dashboard_skipped", message="MEH_DASHBOARD disabled")
        return

    # Log base path configuration
    base_path = _normalize_base_path(Config.MEH_DASHBOARD_BASE_PATH)
    if base_path and os.getenv("GITHUB_REPOSITORY"):
        logger.info("base_path_configured", path=base_path, message="Using base path for GitHub Pages")
    elif base_path:
        logger.info("base_path_local", path=base_path, message="Base path set but not in CI")
    else:
        logger.info("base_path_none", message="No base path (local or user site)")

    now_utc = datetime.now(timezone.utc)
    generated_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    generated = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    hint = _pages_url_hint()
    payload = {
        "generated_at": generated,
        "generated_at_iso": generated_iso,
        "candidates_scanned": candidates_count,
        "runtime_seconds": round(runtime_seconds, 1),
        "deals_count": len(deals),
        "deals": deals,
        **({"pages_url_hint": hint} if hint else {}),
    }
    if all_candidates is not None:
        payload["candidates"] = all_candidates
    if scan_summary is not None:
        payload["summary"] = scan_summary

    if Config.MEH_DASHBOARD_DRY_RUN:
        logger.info(
            "dashboard_dry_run",
            message="Would write dashboard (dry run)",
            deals_count=len(deals),
            path=str(INDEX_HTML),
        )
        return

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    # Create .nojekyll to disable Jekyll processing (CRITICAL for GitHub Pages)
    # This file MUST exist at the root of the deployed site, not nested
    try:
        NOJEKYLL.write_text("", encoding="utf-8")  # Explicit write instead of touch
        # Verify it was created
        if not NOJEKYLL.exists():
            raise FileNotFoundError(f".nojekyll not created at {NOJEKYLL}")
        file_stat = NOJEKYLL.stat()
        logger.info("nojekyll_created",
                   path=str(NOJEKYLL),
                   size_bytes=file_stat.st_size,
                   message="✅ .nojekyll created (disables Jekyll)")
    except Exception as e:
        logger.error("nojekyll_critical_fail",
                    error=str(e),
                    path=str(NOJEKYLL),
                    message="🚨 CRITICAL: .nojekyll creation failed! GitHub Pages will use Jekyll and fail.")

    LATEST_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if all_candidates is not None:
        CANDIDATES_JSON.write_text(
            json.dumps(
                {
                    "generated_at": generated,
                    "scoring": CANDIDATE_SCORING_REFERENCE,
                    "candidates": all_candidates,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for c in all_candidates:
            writer.writerow(candidate_row_for_csv(c))
        CANDIDATES_CSV.write_text(buf.getvalue(), encoding="utf-8")

    rows_html = []
    for d in deals:
        url = d.get("url") or ""
        name = html_module.escape(str(d.get("site_name") or ""))
        rationale = html_module.escape(str(d.get("rationale") or ""))
        niche_raw = str(d.get("niche") or "")
        niche = html_module.escape(niche_raw)
        score = int(d.get("quality_score") or 0)
        now_p = html_module.escape(str(d.get("deal_price") or "—"))
        was_p = html_module.escape(str(d.get("original_price") or "—"))
        link = html_module.escape(url)
        base_cls = "row-hot" if score >= 8 else ("row-good" if score >= 7 else "")
        row_cls = (base_cls + (" row-worth" if d.get("worth_buying") else "")).strip()
        # Market columns — only show data when confidence is medium or high
        confidence = d.get("match_confidence")
        show_market = confidence in ("high", "medium")
        if show_market:
            mp = d.get("market_price")
            market_p = html_module.escape(f"${mp:.2f}" if mp is not None else "—")
            market_src = html_module.escape(str(d.get("market_source") or "—"))
            svp = d.get("verified_savings_pct")
            if svp is not None:
                star = "★ " if d.get("worth_buying") else ""
                savings_s = html_module.escape(f"{star}{svp:.1f}%")
            else:
                savings_s = "—"
            conf_s = html_module.escape(confidence)
        else:
            market_p = "—"
            market_src = "—"
            savings_s = "—"
            conf_s = html_module.escape(confidence or "—")
        rows_html.append(
            f"<tr class='{row_cls}' data-score='{score}'>"
            f"<td>{score}</td>"
            f"<td>{name}</td>"
            f"<td>{niche}</td>"
            f"<td>{now_p}</td>"
            f"<td>{was_p}</td>"
            f"<td>{market_p}</td>"
            f"<td>{market_src}</td>"
            f"<td>{savings_s}</td>"
            f"<td>{conf_s}</td>"
            f"<td class='rationale'>{rationale}</td>"
            f"<td><a href=\"{link}\" target=\"_blank\" rel=\"noopener\">link</a></td></tr>"
        )

    if rows_html:
        table_body = "\n".join(rows_html)
    else:
        # Friendly zero-deal message
        table_body = (
            f"<tr style='text-align:center;color:#a9b1d6;'>"
            f"<td colspan='11'>"
            f"Hey there! 👋 No deals matching your current filters right now. Want to loosen things up a bit, try a different view, or let me help you explore some options? I'm here whenever you're ready! 📦"
            f"</td></tr>"
        )

    findings_section = ""
    if all_candidates is not None:
        findings_body = _findings_table_rows(all_candidates)
        findings_section = f"""
  <h2 class="section">All analyzed URLs (accepted and rejected)</h2>
  <p class="meta" style="margin-bottom:0.5rem;">{len(all_candidates)} finding(s) · full data in <a href="./candidates.json">candidates.json</a></p>
  <div class="wrap-findings">
  <table id="findings">
    <thead>
      <tr>
        <th>Vibe</th>
        <th>Q</th>
        <th>Site</th>
        <th>Niche</th>
        <th>OK</th>
        <th>Reason</th>
        <th>Rationale</th>
        <th>URL</th>
      </tr>
    </thead>
    <tbody>
{findings_body}
    </tbody>
  </table>
  </div>
"""

    base_tag = _base_tag_html()
    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
{base_tag}  <title>Meh-Scanner — Daily deals</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.25rem; background: #1a1b26; color: #c0caf5; }}
    h1 {{ font-size: 1.35rem; margin-bottom: 0.35rem; }}
    .last-updated {{ font-size: 1.05rem; color: #a9b1d6; margin: 0 0 0.5rem 0; }}
    .last-updated time {{ font-weight: 600; color: #bb9af7; }}
    .meta {{ color: #565f89; font-size: 0.9rem; margin-bottom: 1rem; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }}
    .toolbar label {{ color: #a9b1d6; font-size: 0.85rem; }}
    .toolbar input, .toolbar select {{ background: #24283b; border: 1px solid #3b4261; color: #c0caf5; padding: 0.35rem 0.5rem; border-radius: 4px; }}
    .toolbar input {{ min-width: 12rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
    th, td {{ border: 1px solid #3b4261; padding: 0.5rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: #24283b; cursor: pointer; user-select: none; }}
    th:hover {{ background: #364a82; }}
    tbody tr:nth-child(even):not(.row-hot):not(.row-good) {{ background: #16161e; }}
    tr.row-good {{ box-shadow: inset 3px 0 0 #7aa2f7; }}
    tr.row-hot {{ box-shadow: inset 3px 0 0 #9ece6a; background: #1e2030; }}
    tr.row-worth td:nth-child(8) {{ color: #e0af68; font-weight: 600; }}
    td.rationale {{ max-width: 28rem; line-height: 1.4; }}
    a {{ color: #7aa2f7; }}
    h2.section {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem 0; color: #bb9af7; }}
    tr.row-rejected {{ opacity: 0.72; }}
    tr.row-rejected td {{ color: #a9b1d6; }}
    .wrap-findings {{ overflow-x: auto; max-height: 28rem; overflow-y: auto; margin-bottom: 1.5rem; }}
  </style>
</head>
<body>
  <h1>Meh-Scanner — daily picks</h1>
  <p class="last-updated">Last updated: <time datetime="{html_module.escape(generated_iso)}">{html_module.escape(generated)}</time></p>
  <p class="meta">{len(deals)} deal(s) · {candidates_count} candidates scanned · {round(runtime_seconds, 1)}s run · <a href="./latest.json">latest.json</a> · <a href="./candidates.json">candidates.json</a> · <a href="./candidates.csv">candidates.csv</a></p>
  <div class="toolbar">
    <label>Filter <input type="search" id="filterText" placeholder="Site, niche, rationale…" oninput="applyFilters()" autocomplete="off"/></label>
    <label>Min score <select id="minScore" onchange="applyFilters()">
      <option value="0">Any</option>
      <option value="6" selected>6+</option>
      <option value="7">7+</option>
      <option value="8">8+</option>
    </select></label>
    <span style="color:#565f89;font-size:0.8rem;">Tip: rows with score ≥7 (blue bar) and ≥8 (green) are highlighted.</span>
  </div>
  <table id="deals">
    <thead>
      <tr>
        <th data-type="num" onclick="sortCol(0)">Score</th>
        <th onclick="sortCol(1)">Site</th>
        <th onclick="sortCol(2)">Niche</th>
        <th onclick="sortCol(3)">Price</th>
        <th onclick="sortCol(4)">Was / MSRP</th>
        <th onclick="sortCol(5)">Market Price</th>
        <th onclick="sortCol(6)">Source</th>
        <th data-type="num" onclick="sortCol(7)">Savings %</th>
        <th onclick="sortCol(8)">Confidence</th>
        <th onclick="sortCol(9)">Rationale</th>
        <th>URL</th>
      </tr>
    </thead>
    <tbody>
{table_body}
    </tbody>
  </table>
{findings_section}
  <script>
    function sortCol(col) {{
      const table = document.getElementById('deals');
      if (!table || !table.tBodies[0]) return;
      const tbody = table.tBodies[0];
      const th = table.tHead.rows[0].cells[col];
      const type = th.dataset.type || 'str';
      const asc = th.asc = !th.asc;
      const rows = Array.from(tbody.rows).filter(r => r.cells.length > 1);
      rows.sort((a, b) => {{
        let va = a.cells[col].innerText.trim();
        let vb = b.cells[col].innerText.trim();
        if (type === 'num') {{ va = parseFloat(va) || 0; vb = parseFloat(vb) || 0; return asc ? va - vb : vb - va; }}
        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
      }});
      rows.forEach(r => tbody.appendChild(r));
      applyFilters();
    }}
    function applyFilters() {{
      const ft = document.getElementById('filterText');
      const ms = document.getElementById('minScore');
      const tbody = document.querySelector('#deals tbody');
      if (!tbody) return;
      const q = (ft && ft.value || '').toLowerCase().trim();
      const minScore = ms ? (parseInt(ms.value, 10) || 0) : 0;
      [...tbody.rows].forEach(r => {{
        if (r.cells.length < 11) {{ r.style.display = ''; return; }}
        const score = parseInt(r.cells[0].innerText, 10) || 0;
        const hay = r.innerText.toLowerCase();
        const ok = score >= minScore && (q === '' || hay.includes(q));
        r.style.display = ok ? '' : 'none';
      }});
    }}
    applyFilters();
  </script>
</body>
</html>
"""
    INDEX_HTML.write_text(html_out, encoding="utf-8")

    # Verify all critical files exist and are accessible
    files_ok = True
    for fpath in [INDEX_HTML, LATEST_JSON, NOJEKYLL]:
        if not fpath.exists():
            logger.error("file_missing_critical",
                        path=str(fpath),
                        message=f"🚨 CRITICAL: {fpath.name} missing!")
            files_ok = False
        else:
            size = fpath.stat().st_size
            logger.info("file_verified", f"File verified: {fpath.name} ({size} bytes)", path=str(fpath), size_bytes=size)

    # Verify base tag injection (if configured)
    base_path = _normalize_base_path(Config.MEH_DASHBOARD_BASE_PATH)
    if base_path:
        html_content = INDEX_HTML.read_text(encoding="utf-8")
        if f'<base href="{base_path}"' in html_content:
            logger.info("base_tag_verified",
                       base_path=base_path,
                       message=f"✅ Base tag correctly injected: {base_path}")
        else:
            logger.error("base_tag_missing",
                        base_path=base_path,
                        message="⚠️ Base tag NOT found in HTML! Relative links may fail.")
            files_ok = False

    log_ctx = {
        "path": str(INDEX_HTML),
        "json_path": str(LATEST_JSON),
        "nojekyll_path": str(NOJEKYLL),
        "deals_count": len(deals),
        "base_path": base_path or "(none — local or user site)",
        "all_files_ok": files_ok,
    }
    if hint:
        log_ctx["pages_url_hint"] = hint

    status = "✅ Dashboard exported successfully" if files_ok else "⚠️ Dashboard exported but with warnings"
    logger.info("dashboard_written", status, **log_ctx)
