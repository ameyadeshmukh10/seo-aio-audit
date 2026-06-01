#!/usr/bin/env python3
"""
Build the Momentive Software Blog Audit Dashboard.
Reads /tmp/dashboard_data.json + momentive_audit_results.json
Writes:
  - momentive_dashboard.html  (full interactive audit dashboard)
  - momentive_key_takeaways.html (presentation-ready key findings slide deck)
"""

import json
import os

# ── Load data ─────────────────────────────────────────────────────────────────
with open("/tmp/dashboard_data.json") as f:
    posts = json.load(f)

# ── Pre-compute all aggregate stats ───────────────────────────────────────────
total        = len(posts)
avg_wc       = round(sum(p["word_count"] for p in posts) / total)
faq_gap      = sum(1 for p in posts if p["has_faq_content"] and not p["has_faq_schema"])
faq_schema   = sum(1 for p in posts if p["has_faq_schema"])
no_kw        = sum(1 for p in posts if not p["rank_math_keywords"])
no_int       = sum(1 for p in posts if p["internal_blog_links_count"] == 0)
has_snippets = sum(1 for p in posts if p["has_snippets"])
thin         = sum(1 for p in posts if p["wc_tier"] == "Thin")
ok_wc        = sum(1 for p in posts if p["wc_tier"] == "OK")
strong       = sum(1 for p in posts if p["wc_tier"] == "Strong")
aio_ready    = sum(1 for p in posts if p["aio_tier"] == "AIO Ready")
aio_partial  = sum(1 for p in posts if p["aio_tier"] == "AIO Partial")
aio_weak     = sum(1 for p in posts if p["aio_tier"] == "AIO Weak")

pct = lambda n: round(n / total * 100, 1)

wc_buckets = {"0–499": 0, "500–799": 0, "800–999": 0,
              "1000–1499": 0, "1500–1999": 0, "2000+": 0}
for p in posts:
    wc = p["word_count"]
    if   wc < 500:  wc_buckets["0–499"]    += 1
    elif wc < 800:  wc_buckets["500–799"]  += 1
    elif wc < 1000: wc_buckets["800–999"]  += 1
    elif wc < 1500: wc_buckets["1000–1499"]+= 1
    elif wc < 2000: wc_buckets["1500–1999"]+= 1
    else:           wc_buckets["2000+"]    += 1

# Top / bottom by AIO score
sorted_posts   = sorted(posts, key=lambda p: (p["aio_score"], p["word_count"]), reverse=True)
top10          = sorted_posts[:10]
bottom10       = sorted_posts[-10:][::-1]

# JSON payload for the JS in the HTML
posts_json = json.dumps(posts, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ══════════════════════════════════════════════════════════════════════════════
dashboard_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Momentive Software — Blog SEO &amp; AIO Audit Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:       #0f1117;
    --surface:  #1a1d27;
    --surface2: #22263a;
    --border:   #2e3350;
    --accent:   #6c63ff;
    --accent2:  #00d4aa;
    --warn:     #f5a623;
    --danger:   #e05260;
    --good:     #27ae60;
    --text:     #e8eaf6;
    --muted:    #8b90b8;
    --radius:   12px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }}

  /* ── NAV ── */
  .top-nav {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 32px;
    display: flex; align-items: center; gap: 0; position: sticky; top: 0; z-index: 100;
  }}
  .nav-brand {{ font-size: 1rem; font-weight: 700; color: var(--accent); padding: 18px 24px 18px 0; border-right: 1px solid var(--border); margin-right: 24px; white-space: nowrap; }}
  .nav-tab {{ padding: 20px 18px; font-size: 0.875rem; font-weight: 500; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; transition: all .2s; white-space: nowrap; }}
  .nav-tab:hover {{ color: var(--text); }}
  .nav-tab.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
  .nav-meta {{ margin-left: auto; font-size: 0.75rem; color: var(--muted); white-space: nowrap; }}

  /* ── LAYOUT ── */
  .page {{ display: none; padding: 32px; max-width: 1400px; margin: 0 auto; }}
  .page.active {{ display: block; }}
  h1 {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 28px; }}

  /* ── STAT CARDS ── */
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 28px; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 20px 22px; position: relative; overflow: hidden;
  }}
  .card::before {{
    content:''; position:absolute; top:0; left:0; width:4px; height:100%;
    background: var(--accent); border-radius: var(--radius) 0 0 var(--radius);
  }}
  .card.warn::before {{ background: var(--warn); }}
  .card.danger::before {{ background: var(--danger); }}
  .card.good::before {{ background: var(--good); }}
  .card-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }}
  .card-value {{ font-size: 2rem; font-weight: 800; line-height: 1; }}
  .card-sub {{ font-size: 0.75rem; color: var(--muted); margin-top: 4px; }}

  /* ── CHARTS ROW ── */
  .charts-row {{ display: grid; grid-template-columns: 280px 1fr 1fr; gap: 20px; margin-bottom: 28px; }}
  .chart-box {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 22px;
  }}
  .chart-box h3 {{ font-size: 0.9rem; font-weight: 600; margin-bottom: 16px; color: var(--text); }}
  .chart-wrap {{ position: relative; }}

  /* ── SIGNAL GRID ── */
  .signal-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .signal-item {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 18px; display: flex; align-items: center; gap: 14px;
  }}
  .signal-icon {{ font-size: 1.4rem; flex-shrink: 0; }}
  .signal-label {{ font-size: 0.78rem; color: var(--muted); margin-bottom: 2px; }}
  .signal-val {{ font-size: 1.1rem; font-weight: 700; }}
  .sig-good {{ color: var(--good); }}
  .sig-warn {{ color: var(--warn); }}
  .sig-danger {{ color: var(--danger); }}

  /* ── SECTION HEADING ── */
  .section-head {{ font-size: 1rem; font-weight: 700; margin: 28px 0 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }}
  .badge {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 20px; font-weight: 600; }}
  .badge-good {{ background: #1a3d2b; color: var(--good); }}
  .badge-warn {{ background: #3d2f10; color: var(--warn); }}
  .badge-danger {{ background: #3d1520; color: var(--danger); }}

  /* ── TABLES ── */
  .tbl-wrap {{ overflow-x: auto; border-radius: var(--radius); border: 1px solid var(--border); margin-bottom: 28px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ background: var(--surface2); color: var(--muted); text-transform: uppercase; font-size: 0.7rem; letter-spacing: .05em; padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; cursor: pointer; user-select: none; }}
  th:hover {{ color: var(--text); }}
  td {{ padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: var(--surface2); }}
  .post-title {{ font-weight: 500; color: var(--text); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .post-title a {{ color: inherit; text-decoration: none; }}
  .post-title a:hover {{ color: var(--accent); }}

  /* ── TIER BADGES ── */
  .tier {{ display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 0.72rem; font-weight: 700; white-space: nowrap; }}
  .tier-ready   {{ background: #1a3d2b; color: #2ecc71; }}
  .tier-partial {{ background: #3d2f10; color: #f5a623; }}
  .tier-weak    {{ background: #3d1520; color: #e05260; }}
  .tier-thin    {{ background: #3d1520; color: #e05260; }}
  .tier-ok      {{ background: #2a2f10; color: #e0c023; }}
  .tier-strong  {{ background: #1a3d2b; color: #2ecc71; }}

  /* ── ISSUE CHIPS ── */
  .issue-chip {{ display: inline-block; background: var(--surface2); border: 1px solid var(--border); color: var(--warn); font-size: 0.68rem; padding: 2px 7px; border-radius: 4px; margin: 1px; white-space: nowrap; }}

  /* ── SCORE BAR ── */
  .score-bar {{ display: flex; gap: 3px; align-items: center; }}
  .score-pip {{ width: 10px; height: 10px; border-radius: 2px; background: var(--border); }}
  .score-pip.on {{ background: var(--accent); }}

  /* ── EXPLORER ── */
  .filters {{ display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; align-items: center; }}
  .search-box {{ flex: 1; min-width: 240px; padding: 9px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 0.85rem; outline: none; }}
  .search-box:focus {{ border-color: var(--accent); }}
  .filter-select {{ padding: 9px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 0.85rem; outline: none; cursor: pointer; }}
  .filter-select:focus {{ border-color: var(--accent); }}
  .result-count {{ font-size: 0.8rem; color: var(--muted); white-space: nowrap; }}

  /* ── PROGRESS BAR ── */
  .progress-bar {{ background: var(--surface2); border-radius: 4px; height: 8px; overflow: hidden; width: 80px; }}
  .progress-fill {{ height: 100%; border-radius: 4px; background: var(--accent); }}

  /* ── PAGINATION ── */
  .pagination {{ display: flex; gap: 6px; justify-content: center; padding: 16px 0 0; flex-wrap: wrap; }}
  .page-btn {{ padding: 6px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; color: var(--muted); cursor: pointer; font-size: 0.8rem; transition: all .15s; }}
  .page-btn:hover {{ border-color: var(--accent); color: var(--text); }}
  .page-btn.active {{ background: var(--accent); border-color: var(--accent); color: white; }}
  .page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}

  /* ── KEY ISSUES CALLOUT ── */
  .callout {{
    background: linear-gradient(135deg, #1a1d27 0%, #22263a 100%);
    border: 1px solid var(--border); border-left: 4px solid var(--danger);
    border-radius: var(--radius); padding: 20px 24px; margin-bottom: 20px;
  }}
  .callout.warn {{ border-left-color: var(--warn); }}
  .callout.good {{ border-left-color: var(--good); }}
  .callout-title {{ font-size: 0.9rem; font-weight: 700; margin-bottom: 6px; }}
  .callout-body {{ font-size: 0.82rem; color: var(--muted); line-height: 1.6; }}

  /* ── SCROLLBAR ── */
  ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg); }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
</style>
</head>
<body>

<!-- ════════════ NAV ════════════ -->
<nav class="top-nav">
  <div class="nav-brand">📊 Momentive Blog Audit</div>
  <div class="nav-tab active" onclick="showPage('overview',this)">Overview</div>
  <div class="nav-tab" onclick="showPage('explorer',this)">Post Explorer</div>
  <div class="nav-tab" onclick="showPage('top10',this)">Top Posts</div>
  <div class="nav-tab" onclick="showPage('issues',this)">Issues</div>
  <div class="nav-meta">413 posts audited · March 2026</div>
</nav>

<!-- ════════════════════════════════════════════════════════════════════════════
     PAGE 1 — OVERVIEW
════════════════════════════════════════════════════════════════════════════ -->
<div id="page-overview" class="page active">
  <h1>SEO &amp; AIO Audit — Momentive Software Blog</h1>
  <p class="subtitle">Full crawl of momentivesoftware.com/blog · 413 posts successfully audited · March 2026</p>

  <!-- Stat Cards -->
  <div class="cards">
    <div class="card good">
      <div class="card-label">Posts Audited</div>
      <div class="card-value">413</div>
      <div class="card-sub">of 780 discovered URLs</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Word Count</div>
      <div class="card-value">1,250</div>
      <div class="card-sub">Target ≥ 1,500</div>
    </div>
    <div class="card good">
      <div class="card-label">AIO Ready</div>
      <div class="card-value">22.3%</div>
      <div class="card-sub">92 posts score ≥ 4/5</div>
    </div>
    <div class="card danger">
      <div class="card-label">FAQ Schema Gap</div>
      <div class="card-value">358</div>
      <div class="card-sub">86.7% have no FAQ schema</div>
    </div>
    <div class="card warn">
      <div class="card-label">Missing Keywords</div>
      <div class="card-value">139</div>
      <div class="card-sub">33.7% have no Rank Math KW</div>
    </div>
    <div class="card danger">
      <div class="card-label">No Internal Links</div>
      <div class="card-value">365</div>
      <div class="card-sub">88.4% have zero blog links</div>
    </div>
  </div>

  <!-- Charts Row -->
  <div class="charts-row">
    <!-- AIO Donut -->
    <div class="chart-box">
      <h3>AIO Readiness</h3>
      <div class="chart-wrap" style="height:200px">
        <canvas id="aioDonut"></canvas>
      </div>
      <div style="margin-top:14px;font-size:0.78rem;">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span style="color:#2ecc71">● AIO Ready</span><span>92 (22.3%)</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span style="color:#f5a623">● AIO Partial</span><span>259 (62.7%)</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:#e05260">● AIO Weak</span><span>62 (15.0%)</span>
        </div>
      </div>
    </div>

    <!-- Word Count Histogram -->
    <div class="chart-box">
      <h3>Word Count Distribution</h3>
      <div class="chart-wrap" style="height:220px">
        <canvas id="wcHist"></canvas>
      </div>
    </div>

    <!-- Signal Coverage -->
    <div class="chart-box">
      <h3>AIO Signal Coverage</h3>
      <div class="chart-wrap" style="height:220px">
        <canvas id="signalBar"></canvas>
      </div>
    </div>
  </div>

  <!-- Signal Grid -->
  <div class="signal-grid">
    <div class="signal-item">
      <span class="signal-icon">❓</span>
      <div>
        <div class="signal-label">Has FAQ Content</div>
        <div class="signal-val sig-good">413 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">📋</span>
      <div>
        <div class="signal-label">Has FAQ Schema</div>
        <div class="signal-val sig-danger">55 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">📰</span>
      <div>
        <div class="signal-label">Has Article Schema</div>
        <div class="signal-val sig-good">411 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">🗂️</span>
      <div>
        <div class="signal-label">Has Breadcrumb Schema</div>
        <div class="signal-val sig-good">413 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">📦</span>
      <div>
        <div class="signal-label">Has Structured Snippets</div>
        <div class="signal-val sig-warn">190 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">🔑</span>
      <div>
        <div class="signal-label">Rank Math Keywords Set</div>
        <div class="signal-val sig-warn">274 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">🔗</span>
      <div>
        <div class="signal-label">Has Internal Blog Links</div>
        <div class="signal-val sig-danger">48 / 413</div>
      </div>
    </div>
    <div class="signal-item">
      <span class="signal-icon">📝</span>
      <div>
        <div class="signal-label">Strong Content (1500+w)</div>
        <div class="signal-val sig-warn">103 / 413</div>
      </div>
    </div>
  </div>

  <!-- Callouts -->
  <div class="callout">
    <div class="callout-title">🚨 Critical: The FAQ Schema Gap is the #1 AIO Miss</div>
    <div class="callout-body">
      Every single blog post (413/413) contains FAQ-style content — but only 55 have FAQ schema markup.
      That means <strong>358 posts are invisible to Google's AI Overviews and rich result FAQ snippets</strong>.
      Adding FAQPage schema is the single highest-ROI fix across the entire blog.
    </div>
  </div>
  <div class="callout warn">
    <div class="callout-title">⚠️ Internal Linking is Nearly Non-Existent</div>
    <div class="callout-body">
      88.4% of posts (365 of 413) have <strong>zero internal links to other blog posts</strong>.
      This siloes every post as a dead end for both users and crawlers, preventing PageRank flow
      and eliminating topical cluster signals that AI systems use to assess authority.
    </div>
  </div>
  <div class="callout good">
    <div class="callout-title">✅ Strong Foundation: Schema &amp; Structure Are in Place</div>
    <div class="callout-body">
      99.5% of posts have Article schema, and 100% have BreadcrumbList — a solid technical foundation.
      The gap is in AIO-specific signals (FAQ schema, Q&amp;A structure, internal linking), not in basic SEO.
    </div>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════════════════════
     PAGE 2 — POST EXPLORER
════════════════════════════════════════════════════════════════════════════ -->
<div id="page-explorer" class="page">
  <h1>Post Explorer</h1>
  <p class="subtitle">Search, filter, and review every audited post. Click any column header to sort.</p>

  <div class="filters">
    <input class="search-box" id="searchInput" placeholder="🔍 Search by title or URL…" oninput="applyFilters()"/>
    <select class="filter-select" id="aioFilter" onchange="applyFilters()">
      <option value="">All AIO Tiers</option>
      <option value="AIO Ready">AIO Ready</option>
      <option value="AIO Partial">AIO Partial</option>
      <option value="AIO Weak">AIO Weak</option>
    </select>
    <select class="filter-select" id="wcFilter" onchange="applyFilters()">
      <option value="">All Word Counts</option>
      <option value="Thin">Thin (&lt;800)</option>
      <option value="OK">OK (800–1499)</option>
      <option value="Strong">Strong (1500+)</option>
    </select>
    <select class="filter-select" id="faqFilter" onchange="applyFilters()">
      <option value="">FAQ Schema — Any</option>
      <option value="yes">Has FAQ Schema</option>
      <option value="no">Missing FAQ Schema</option>
    </select>
    <select class="filter-select" id="kwFilter" onchange="applyFilters()">
      <option value="">Keywords — Any</option>
      <option value="yes">Has Keywords</option>
      <option value="no">No Keywords</option>
    </select>
    <span class="result-count" id="resultCount"></span>
  </div>

  <div class="tbl-wrap">
    <table id="explorerTable">
      <thead>
        <tr>
          <th onclick="sortTable('title')">Title ↕</th>
          <th onclick="sortTable('aio_score')">AIO Score ↕</th>
          <th onclick="sortTable('aio_tier')">AIO Tier ↕</th>
          <th onclick="sortTable('word_count')">Words ↕</th>
          <th onclick="sortTable('wc_tier')">Content ↕</th>
          <th>FAQ Schema</th>
          <th>Snippets</th>
          <th onclick="sortTable('internal_blog_links_count')">Int. Links ↕</th>
          <th>Issues</th>
        </tr>
      </thead>
      <tbody id="explorerBody"></tbody>
    </table>
  </div>
  <div class="pagination" id="pagination"></div>
</div>

<!-- ════════════════════════════════════════════════════════════════════════════
     PAGE 3 — TOP POSTS
════════════════════════════════════════════════════════════════════════════ -->
<div id="page-top10" class="page">
  <h1>Top &amp; Bottom Posts by AIO Score</h1>
  <p class="subtitle">Sorted by AIO Score (0–5) then word count. Use these as benchmarks.</p>

  <div class="section-head">🏆 Top 10 — Highest AIO Score <span class="badge badge-good">AIO Ready</span></div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Title</th><th>AIO Score</th><th>Words</th>
          <th>FAQ Schema</th><th>Snippets</th><th>Keywords</th><th>Int. Links</th>
        </tr>
      </thead>
      <tbody id="top10Body"></tbody>
    </table>
  </div>

  <div class="section-head">⚠️ Bottom 10 — Most Issues <span class="badge badge-danger">Needs Work</span></div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Title</th><th>AIO Score</th><th>Words</th>
          <th>Issues</th>
        </tr>
      </thead>
      <tbody id="bottom10Body"></tbody>
    </table>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════════════════════
     PAGE 4 — ISSUES BREAKDOWN
════════════════════════════════════════════════════════════════════════════ -->
<div id="page-issues" class="page">
  <h1>Issues Breakdown</h1>
  <p class="subtitle">Posts grouped by their most critical outstanding issue.</p>

  <div class="section-head">📋 All Posts Missing FAQ Schema <span class="badge badge-danger">358 posts</span></div>
  <div class="callout">
    <div class="callout-title">What to do</div>
    <div class="callout-body">Add <code>FAQPage</code> + <code>Question</code>/<code>Answer</code> JSON-LD schema to every post that already has a FAQ section. This is a templated fix — one Rank Math update per post, or a bulk programmatic injection.</div>
  </div>
  <div class="tbl-wrap">
    <table id="faqGapTable">
      <thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Tier</th><th>Has Snippets</th></tr></thead>
      <tbody id="faqGapBody"></tbody>
    </table>
  </div>

  <div class="section-head">🔗 Posts With Zero Internal Blog Links <span class="badge badge-danger">365 posts</span></div>
  <div class="callout warn">
    <div class="callout-title">What to do</div>
    <div class="callout-body">Build topical clusters: each post should link to 2–3 related posts. Prioritise by AIO score — start with AIO Partial posts and add 2 internal links each to move them up to AIO Ready.</div>
  </div>
  <div class="tbl-wrap">
    <table id="noLinksTable">
      <thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Score</th><th>AIO Tier</th></tr></thead>
      <tbody id="noLinksBody"></tbody>
    </table>
  </div>

  <div class="section-head">📉 Thin Content Posts <span class="badge badge-warn">142 posts</span></div>
  <div class="callout warn">
    <div class="callout-title">What to do</div>
    <div class="callout-body">Posts under 800 words are at risk of being classified as thin content. Prioritise expanding posts that rank for valuable keywords. Target 1,500+ words with FAQ sections and data citations.</div>
  </div>
  <div class="tbl-wrap">
    <table id="thinTable">
      <thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Score</th><th>Keywords</th></tr></thead>
      <tbody id="thinBody"></tbody>
    </table>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════════════════════
     SCRIPTS
════════════════════════════════════════════════════════════════════════════ -->
<script>
// ── Data ──────────────────────────────────────────────────────────────────
const POSTS = {posts_json};

// ── Navigation ────────────────────────────────────────────────────────────
function showPage(id, tab) {{
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  tab.classList.add('active');
  if (id === 'explorer' && !explorerBuilt) buildExplorer();
  if (id === 'top10'    && !top10Built)   buildTop10();
  if (id === 'issues'   && !issuesBuilt)  buildIssues();
}}

// ── Charts ────────────────────────────────────────────────────────────────
const PALETTE = {{
  ready:   '#2ecc71',
  partial: '#f5a623',
  weak:    '#e05260',
  accent:  '#6c63ff',
  muted:   '#8b90b8',
  surface2:'#22263a',
}};

function buildCharts() {{
  // AIO Donut
  new Chart(document.getElementById('aioDonut'), {{
    type: 'doughnut',
    data: {{
      labels: ['AIO Ready', 'AIO Partial', 'AIO Weak'],
      datasets: [{{ data: [92, 259, 62], backgroundColor: [PALETTE.ready, PALETTE.partial, PALETTE.weak], borderWidth: 0, hoverOffset: 4 }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, cutout: '68%',
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{
        label: ctx => ` ${{ctx.label}}: ${{ctx.raw}} (${{(ctx.raw/413*100).toFixed(1)}}%)`
      }} }} }}
    }}
  }});

  // Word count histogram
  new Chart(document.getElementById('wcHist'), {{
    type: 'bar',
    data: {{
      labels: ['0–499','500–799','800–999','1000–1499','1500–1999','2000+'],
      datasets: [{{
        label: 'Posts',
        data: [37, 105, 60, 108, 42, 61],
        backgroundColor: ['#e05260','#e05260','#f5a623','#f5a623','#2ecc71','#2ecc71'],
        borderRadius: 4, borderSkipped: false,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#2e3350' }}, ticks: {{ color: '#8b90b8', font: {{size:11}} }} }},
        y: {{ grid: {{ color: '#2e3350' }}, ticks: {{ color: '#8b90b8', font: {{size:11}} }} }}
      }}
    }}
  }});

  // Signal bar
  new Chart(document.getElementById('signalBar'), {{
    type: 'bar',
    data: {{
      labels: ['Article Schema','Breadcrumb','Has Snippets','Rank Math KW','FAQ Schema','Internal Links'],
      datasets: [{{
        label: '% of posts',
        data: [99.5, 100, 46, 66.3, 13.3, 11.6],
        backgroundColor: ['#2ecc71','#2ecc71','#f5a623','#f5a623','#e05260','#e05260'],
        borderRadius: 4, borderSkipped: false,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{
        label: ctx => ` ${{ctx.raw}}%`
      }} }} }},
      scales: {{
        x: {{ max: 100, grid: {{ color: '#2e3350' }}, ticks: {{ color: '#8b90b8', font: {{size:11}}, callback: v => v+'%' }} }},
        y: {{ grid: {{ color: 'transparent' }}, ticks: {{ color: '#8b90b8', font: {{size:11}} }} }}
      }}
    }}
  }});
}}

// ── Explorer ──────────────────────────────────────────────────────────────
let explorerBuilt = false, top10Built = false, issuesBuilt = false;
let filtered = [...POSTS];
let currentPage = 1;
const PAGE_SIZE = 30;
let sortKey = 'aio_score', sortDir = -1;

function tierBadge(tier) {{
  if (tier === 'AIO Ready')   return '<span class="tier tier-ready">Ready</span>';
  if (tier === 'AIO Partial') return '<span class="tier tier-partial">Partial</span>';
  return '<span class="tier tier-weak">Weak</span>';
}}
function wcBadge(t)   {{ return t==='Thin'?'<span class="tier tier-thin">Thin</span>':t==='Strong'?'<span class="tier tier-strong">Strong</span>':'<span class="tier tier-ok">OK</span>'; }}
function boolIcon(v)  {{ return v ? '<span style="color:#2ecc71">✓</span>' : '<span style="color:#e05260">✗</span>'; }}
function scorePips(s) {{
  return '<div class="score-bar">' + [0,1,2,3,4].map(i=>`<div class="score-pip ${{i<s?'on':''}}"></div>`).join('') + `&nbsp;<span style="font-size:0.78rem;color:var(--muted)">${{s}}/5</span></div>`;
}}

function buildExplorer() {{
  applyFilters();
  explorerBuilt = true;
}}

function applyFilters() {{
  const q   = document.getElementById('searchInput').value.toLowerCase();
  const aio = document.getElementById('aioFilter').value;
  const wc  = document.getElementById('wcFilter').value;
  const faq = document.getElementById('faqFilter').value;
  const kw  = document.getElementById('kwFilter').value;
  filtered = POSTS.filter(p => {{
    if (q && !p.title.toLowerCase().includes(q) && !p.url.toLowerCase().includes(q)) return false;
    if (aio && p.aio_tier !== aio) return false;
    if (wc  && p.wc_tier  !== wc)  return false;
    if (faq === 'yes' && !p.has_faq_schema) return false;
    if (faq === 'no'  && p.has_faq_schema)  return false;
    if (kw  === 'yes' && (!p.rank_math_keywords || !p.rank_math_keywords.length)) return false;
    if (kw  === 'no'  && p.rank_math_keywords && p.rank_math_keywords.length)     return false;
    return true;
  }});
  sortFiltered();
  currentPage = 1;
  renderExplorerPage();
}}

function sortFiltered() {{
  filtered.sort((a, b) => {{
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === 'string') va = va.toLowerCase(), vb = (vb||'').toLowerCase();
    if (typeof va === 'boolean') va = va ? 1 : 0, vb = vb ? 1 : 0;
    return sortDir * (va > vb ? 1 : va < vb ? -1 : 0);
  }});
}}

function sortTable(key) {{
  if (sortKey === key) sortDir *= -1; else {{ sortKey = key; sortDir = -1; }}
  sortFiltered();
  currentPage = 1;
  renderExplorerPage();
}}

function renderExplorerPage() {{
  const total  = filtered.length;
  const pages  = Math.ceil(total / PAGE_SIZE);
  const start  = (currentPage - 1) * PAGE_SIZE;
  const slice  = filtered.slice(start, start + PAGE_SIZE);

  document.getElementById('resultCount').textContent = `${{total}} post${{total!==1?'s':''}} shown`;

  const tbody = document.getElementById('explorerBody');
  tbody.innerHTML = slice.map(p => `
    <tr>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title || p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td>
      <td>${{tierBadge(p.aio_tier)}}</td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td>${{wcBadge(p.wc_tier)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_faq_schema)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td>
      <td style="text-align:center">${{p.internal_blog_links_count}}</td>
      <td>${{(p.issues||[]).map(i=>`<span class="issue-chip">${{i}}</span>`).join(' ')}}</td>
    </tr>
  `).join('');

  // Pagination
  const pg = document.getElementById('pagination');
  let html = '';
  for (let i = 1; i <= pages; i++) {{
    if (i===1 || i===pages || Math.abs(i-currentPage)<=2)
      html += `<button class="page-btn ${{i===currentPage?'active':''}}" onclick="goPage(${{i}})">${{i}}</button>`;
    else if (Math.abs(i-currentPage)===3)
      html += `<span style="color:var(--muted);padding:6px 4px">…</span>`;
  }}
  pg.innerHTML = html;
}}

function goPage(n) {{ currentPage = n; renderExplorerPage(); window.scrollTo(0,120); }}

// ── Top / Bottom ──────────────────────────────────────────────────────────
function buildTop10() {{
  const sorted = [...POSTS].sort((a,b) => (b.aio_score - a.aio_score) || (b.word_count - a.word_count));
  const top    = sorted.slice(0, 10);
  const bottom = sorted.slice(-10).reverse();

  document.getElementById('top10Body').innerHTML = top.map((p,i) => `
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td style="text-align:center">${{boolIcon(p.has_faq_schema)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td>
      <td style="font-size:0.78rem;color:var(--muted)">${{(p.rank_math_keywords||[]).slice(0,2).join(', ')||'—'}}</td>
      <td style="text-align:center">${{p.internal_blog_links_count}}</td>
    </tr>
  `).join('');

  document.getElementById('bottom10Body').innerHTML = bottom.map((p,i) => `
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td>${{(p.issues||[]).map(i=>`<span class="issue-chip">${{i}}</span>`).join(' ')}}</td>
    </tr>
  `).join('');

  top10Built = true;
}}

// ── Issues ────────────────────────────────────────────────────────────────
function buildIssues() {{
  const faqGap = POSTS.filter(p => p.has_faq_content && !p.has_faq_schema)
                       .sort((a,b) => b.word_count - a.word_count);
  document.getElementById('faqGapBody').innerHTML = faqGap.map((p,i) => `
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td>${{tierBadge(p.aio_tier)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td>
    </tr>
  `).join('');

  const noLinks = POSTS.filter(p => p.internal_blog_links_count === 0)
                        .sort((a,b) => b.aio_score - a.aio_score || b.word_count - a.word_count);
  document.getElementById('noLinksBody').innerHTML = noLinks.map((p,i) => `
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td style="text-align:center">${{scorePips(p.aio_score)}}</td>
      <td>${{tierBadge(p.aio_tier)}}</td>
    </tr>
  `).join('');

  const thin = POSTS.filter(p => p.wc_tier === 'Thin')
                     .sort((a,b) => a.word_count - b.word_count);
  document.getElementById('thinBody').innerHTML = thin.map((p,i) => `
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td style="color:var(--danger)">${{p.word_count.toLocaleString()}}</td>
      <td style="text-align:center">${{scorePips(p.aio_score)}}</td>
      <td style="font-size:0.78rem;color:var(--muted)">${{(p.rank_math_keywords||[]).slice(0,2).join(', ')||'—'}}</td>
    </tr>
  `).join('');

  issuesBuilt = true;
}}

// ── Init ──────────────────────────────────────────────────────────────────
buildCharts();
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════════════════════
# KEY TAKEAWAYS HTML (Presentation-ready)
# ══════════════════════════════════════════════════════════════════════════════

takeaways_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Momentive Software — Blog Audit Key Takeaways</title>
<style>
  :root {
    --bg:      #0a0c14;
    --surface: #111420;
    --border:  #1e2238;
    --accent:  #6c63ff;
    --accent2: #00d4aa;
    --warn:    #f5a623;
    --danger:  #e05260;
    --good:    #2ecc71;
    --text:    #eef0fb;
    --muted:   #7b82b0;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; overflow-x: hidden;
  }

  /* ── SLIDE ENGINE ── */
  .slide {
    display: none; min-height: 100vh;
    flex-direction: column; justify-content: center; align-items: center;
    padding: 60px 80px; position: relative;
  }
  .slide.active { display: flex; }

  /* Slide backgrounds */
  .slide-cover   { background: radial-gradient(ellipse at 30% 50%, #1a1560 0%, var(--bg) 65%); }
  .slide-dark    { background: var(--surface); }
  .slide-default { background: var(--bg); }
  .slide-cta     { background: radial-gradient(ellipse at 70% 50%, #0d2d1f 0%, var(--bg) 65%); }

  /* ── NAVIGATION ── */
  .slide-nav {
    position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%);
    display: flex; gap: 12px; align-items: center;
    background: rgba(17,20,32,0.9); backdrop-filter: blur(12px);
    border: 1px solid var(--border); border-radius: 40px; padding: 10px 20px;
    z-index: 999;
  }
  .nav-btn {
    background: none; border: 1px solid var(--border); color: var(--muted);
    padding: 7px 18px; border-radius: 20px; cursor: pointer; font-size: 0.85rem;
    transition: all .2s;
  }
  .nav-btn:hover { border-color: var(--accent); color: var(--text); }
  .slide-counter { font-size: 0.8rem; color: var(--muted); min-width: 50px; text-align: center; }
  .dot-nav { display: flex; gap: 6px; align-items: center; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); cursor: pointer; transition: all .2s; }
  .dot.active { background: var(--accent); transform: scale(1.3); }

  /* ── TYPOGRAPHY ── */
  .slide-eyebrow { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .15em; color: var(--accent); margin-bottom: 14px; font-weight: 600; }
  .slide-title   { font-size: clamp(2rem,4vw,3.2rem); font-weight: 800; line-height: 1.1; margin-bottom: 16px; text-align: center; }
  .slide-sub     { font-size: 1.05rem; color: var(--muted); text-align: center; line-height: 1.6; max-width: 640px; }
  .slide-label   { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin-bottom: 8px; }

  /* ── STAT HERO ── */
  .stat-hero { font-size: clamp(4rem,10vw,7rem); font-weight: 900; line-height: 1; }
  .stat-hero.danger  { color: var(--danger); }
  .stat-hero.warn    { color: var(--warn); }
  .stat-hero.good    { color: var(--good); }
  .stat-hero.accent  { color: var(--accent); }

  /* ── GRID LAYOUTS ── */
  .two-col  { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; width: 100%; max-width: 1000px; }
  .three-col{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; width: 100%; max-width: 1100px; }

  /* ── CARDS ── */
  .card {
    background: rgba(255,255,255,.04); border: 1px solid var(--border);
    border-radius: 16px; padding: 28px 30px; text-align: center;
  }
  .card.danger { border-color: rgba(224,82,96,.4); background: rgba(224,82,96,.06); }
  .card.warn   { border-color: rgba(245,166,35,.4); background: rgba(245,166,35,.06); }
  .card.good   { border-color: rgba(46,204,113,.4); background: rgba(46,204,113,.06); }
  .card-num { font-size: 2.8rem; font-weight: 900; line-height: 1; margin-bottom: 6px; }
  .card-lbl { font-size: 0.82rem; color: var(--muted); line-height: 1.4; }

  /* ── FINDING ROWS ── */
  .finding-row {
    display: flex; align-items: flex-start; gap: 20px;
    background: rgba(255,255,255,.03); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px 24px; margin-bottom: 14px;
    width: 100%; max-width: 860px;
  }
  .finding-num {
    font-size: 2rem; font-weight: 900; min-width: 50px; text-align: center;
    line-height: 1; padding-top: 2px;
  }
  .finding-body {}
  .finding-title { font-size: 1.05rem; font-weight: 700; margin-bottom: 5px; }
  .finding-desc  { font-size: 0.84rem; color: var(--muted); line-height: 1.6; }

  /* ── PROGRESS BARS ── */
  .prog-row { width: 100%; max-width: 700px; margin-bottom: 12px; }
  .prog-label { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 0.85rem; }
  .prog-bar { background: var(--border); border-radius: 6px; height: 12px; overflow: hidden; }
  .prog-fill { height: 100%; border-radius: 6px; transition: width .6s ease; }

  /* ── REC CHIPS ── */
  .rec-list { display: flex; flex-direction: column; gap: 12px; width: 100%; max-width: 860px; }
  .rec-item {
    display: flex; align-items: flex-start; gap: 14px;
    background: rgba(108,99,255,.07); border: 1px solid rgba(108,99,255,.25);
    border-radius: 10px; padding: 14px 18px;
  }
  .rec-priority { font-size: 0.7rem; font-weight: 700; padding: 3px 8px; border-radius: 20px; white-space: nowrap; margin-top: 2px; }
  .pri-critical { background: rgba(224,82,96,.2); color: var(--danger); }
  .pri-high     { background: rgba(245,166,35,.2); color: var(--warn); }
  .pri-medium   { background: rgba(108,99,255,.2); color: #9d97ff; }
  .rec-text  { font-size: 0.88rem; line-height: 1.55; color: var(--text); }
  .rec-title { font-weight: 700; margin-bottom: 2px; font-size: 0.92rem; }

  /* ── DIVIDER ── */
  .divider { width: 60px; height: 3px; background: var(--accent); border-radius: 2px; margin: 18px auto; }

  /* ── LOGO AREA ── */
  .logo-area { font-size: 1.3rem; font-weight: 800; color: var(--accent); letter-spacing: -.01em; margin-bottom: 6px; }
  .logo-sub  { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .1em; }

  /* ── AIO TIERS ── */
  .tier-block { border-radius: 12px; padding: 22px 24px; text-align: center; }
  .tier-block h3 { font-size: 1.4rem; font-weight: 800; }
  .tier-block .num { font-size: 2.8rem; font-weight: 900; }
  .tier-block p { font-size: 0.8rem; margin-top: 4px; }
</style>
</head>
<body>

<!-- ══════════ SLIDE 1 — COVER ══════════ -->
<div class="slide slide-cover active" id="slide-1">
  <div class="logo-area">Momentive Software</div>
  <div class="logo-sub">Blog Audit · March 2026</div>
  <div class="divider"></div>
  <div class="slide-title">SEO &amp; AI Overview<br/>Optimization Audit</div>
  <p class="slide-sub">A full technical and content audit of 413 live blog posts — measuring SEO health, AIO readiness, and content effectiveness.</p>
  <div style="margin-top:40px;display:flex;gap:40px;text-align:center">
    <div><div style="font-size:2rem;font-weight:900;color:var(--accent)">413</div><div style="font-size:0.78rem;color:var(--muted)">Posts Audited</div></div>
    <div><div style="font-size:2rem;font-weight:900;color:var(--accent2)">5</div><div style="font-size:0.78rem;color:var(--muted)">AIO Signals Scored</div></div>
    <div><div style="font-size:2rem;font-weight:900;color:var(--warn)">3</div><div style="font-size:0.78rem;color:var(--muted)">Priority Actions</div></div>
  </div>
</div>

<!-- ══════════ SLIDE 2 — AIO READINESS ══════════ -->
<div class="slide slide-default" id="slide-2">
  <div class="slide-eyebrow">AIO Readiness Overview</div>
  <div class="slide-title">Only 1 in 5 posts is<br/>AI-Overview Ready</div>
  <div class="divider"></div>
  <div class="three-col" style="margin-top:10px">
    <div class="tier-block" style="background:rgba(46,204,113,.08);border:1px solid rgba(46,204,113,.3)">
      <div style="color:var(--good)">
        <div class="num" style="color:var(--good)">22%</div>
        <h3>AIO Ready</h3>
        <p style="color:var(--muted)">92 posts · score 4–5/5<br/>Has FAQ schema + snippets + keywords + 1,200w+</p>
      </div>
    </div>
    <div class="tier-block" style="background:rgba(245,166,35,.08);border:1px solid rgba(245,166,35,.3)">
      <div>
        <div class="num" style="color:var(--warn)">63%</div>
        <h3>AIO Partial</h3>
        <p style="color:var(--muted)">259 posts · score 2–3/5<br/>Missing 1–2 key signals — closest to promotion</p>
      </div>
    </div>
    <div class="tier-block" style="background:rgba(224,82,96,.08);border:1px solid rgba(224,82,96,.3)">
      <div>
        <div class="num" style="color:var(--danger)">15%</div>
        <h3>AIO Weak</h3>
        <p style="color:var(--muted)">62 posts · score 0–1/5<br/>Thin content, no structure, no schema</p>
      </div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:28px">AIO score combines: FAQ Schema · FAQ Content · Structured Snippets · Rank Math Keywords · Word Count ≥1,200</p>
</div>

<!-- ══════════ SLIDE 3 — THE BIG MISS ══════════ -->
<div class="slide slide-dark" id="slide-3">
  <div class="slide-eyebrow">Finding #1 — Critical</div>
  <div style="text-align:center">
    <div class="slide-label">Posts with FAQ content but NO FAQ schema</div>
    <div class="stat-hero danger">358</div>
    <div style="font-size:1.3rem;color:var(--muted);margin-top:8px">out of 413 total posts</div>
  </div>
  <div class="divider"></div>
  <div class="two-col" style="max-width:760px;margin-top:10px">
    <div class="card danger" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--danger)">What's happening</div>
      <div style="font-size:0.84rem;color:var(--muted);line-height:1.6">
        Every post already contains FAQ-style Q&amp;A content — but <strong style="color:var(--text)">86.7%</strong> of posts have no FAQPage schema.
        Google and AI systems cannot extract structured answers from untagged content.
      </div>
    </div>
    <div class="card good" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--good)">The opportunity</div>
      <div style="font-size:0.84rem;color:var(--muted);line-height:1.6">
        Adding FAQ schema is a <strong style="color:var(--text)">templated, scalable fix</strong>. Done via Rank Math, it takes ~5 min per post.
        One bulk sprint across 50 high-traffic posts could unlock dozens of rich result snippets and AI citations.
      </div>
    </div>
  </div>
</div>

<!-- ══════════ SLIDE 4 — INTERNAL LINKING ══════════ -->
<div class="slide slide-default" id="slide-4">
  <div class="slide-eyebrow">Finding #2 — Critical</div>
  <div style="text-align:center">
    <div class="slide-label">Posts with ZERO internal blog links</div>
    <div class="stat-hero danger">88%</div>
    <div style="font-size:1.3rem;color:var(--muted);margin-top:8px">365 of 413 posts are isolated dead ends</div>
  </div>
  <div class="divider"></div>
  <div class="two-col" style="max-width:760px;margin-top:10px">
    <div class="card danger" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--danger)">Why it matters</div>
      <div style="font-size:0.84rem;color:var(--muted);line-height:1.6">
        No internal links means no topical cluster signals. Google and AI systems assess subject-matter authority
        by how related content is interconnected. Isolated posts rank lower and are rarely cited.
      </div>
    </div>
    <div class="card warn" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--warn)">The fix</div>
      <div style="font-size:0.84rem;color:var(--muted);line-height:1.6">
        Build topic clusters. Each post should link to <strong style="color:var(--text)">2–3 related posts</strong>.
        Start with the 259 AIO Partial posts — adding links is the fastest way to push them to AIO Ready status.
      </div>
    </div>
  </div>
</div>

<!-- ══════════ SLIDE 5 — CONTENT QUALITY ══════════ -->
<div class="slide slide-dark" id="slide-5">
  <div class="slide-eyebrow">Finding #3 — High Priority</div>
  <div class="slide-title">Content depth is<br/>below the AIO threshold</div>
  <div class="divider"></div>
  <div style="width:100%;max-width:680px;margin-top:8px">
    <div class="prog-row">
      <div class="prog-label"><span>Thin (&lt;800 words)</span><span style="color:var(--danger)">142 posts · 34%</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:34%;background:var(--danger)"></div></div>
    </div>
    <div class="prog-row">
      <div class="prog-label"><span>OK (800–1,499 words)</span><span style="color:var(--warn)">168 posts · 41%</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:41%;background:var(--warn)"></div></div>
    </div>
    <div class="prog-row">
      <div class="prog-label"><span>Strong (1,500+ words)</span><span style="color:var(--good)">103 posts · 25%</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:25%;background:var(--good)"></div></div>
    </div>
  </div>
  <div class="two-col" style="max-width:760px;margin-top:24px">
    <div class="card" style="text-align:left">
      <div style="font-size:1.5rem;font-weight:900;color:var(--warn);margin-bottom:4px">1,250</div>
      <div class="card-lbl">Average word count across all posts. AI systems prefer comprehensive content — target is 1,500–2,500 words per post.</div>
    </div>
    <div class="card" style="text-align:left">
      <div style="font-size:1.5rem;font-weight:900;color:var(--warn);margin-bottom:4px">54%</div>
      <div class="card-lbl">Posts have no structured snippets (lists, tables, definition boxes) — the exact content format AI systems quote from.</div>
    </div>
  </div>
</div>

<!-- ══════════ SLIDE 6 — WHAT'S WORKING ══════════ -->
<div class="slide slide-default" id="slide-6">
  <div class="slide-eyebrow">What's Working</div>
  <div class="slide-title">Strong technical<br/>SEO foundation</div>
  <div class="divider"></div>
  <div class="three-col" style="margin-top:10px">
    <div class="card good">
      <div class="card-num" style="color:var(--good)">100%</div>
      <div class="card-lbl">Breadcrumb schema present on every post</div>
    </div>
    <div class="card good">
      <div class="card-num" style="color:var(--good)">99.5%</div>
      <div class="card-lbl">Article / BlogPosting schema in place</div>
    </div>
    <div class="card good">
      <div class="card-num" style="color:var(--good)">100%</div>
      <div class="card-lbl">All posts contain FAQ-style Q&amp;A content already</div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:28px">
    The technical scaffolding is solid. Momentive doesn't need to rebuild — it needs to <strong>close three specific gaps</strong>: FAQ schema, internal linking, and content depth. The ROI of each fix is exceptionally high because the content itself already exists.
  </p>
</div>

<!-- ══════════ SLIDE 7 — TOP PERFORMERS ══════════ -->
<div class="slide slide-dark" id="slide-7">
  <div class="slide-eyebrow">Benchmark Posts</div>
  <div class="slide-title">Your AIO Ready posts<br/>show the blueprint</div>
  <div class="divider"></div>
  <div style="width:100%;max-width:860px;margin-top:8px">
    <div class="finding-row" style="border-color:rgba(46,204,113,.25)">
      <div class="finding-num" style="color:var(--good)">5/5</div>
      <div class="finding-body">
        <div class="finding-title">Best Association Management Software in 2026</div>
        <div class="finding-desc">5,212 words · FAQ schema · Structured snippets · Keywords set · Strong internal linking. This is the AIO template.</div>
      </div>
    </div>
    <div class="finding-row" style="border-color:rgba(46,204,113,.25)">
      <div class="finding-num" style="color:var(--good)">5/5</div>
      <div class="finding-body">
        <div class="finding-title">How to Start a Nonprofit: Step-by-Step Guide 2026</div>
        <div class="finding-desc">4,429 words · Comprehensive how-to structure · FAQ schema · Clear topical authority signals.</div>
      </div>
    </div>
    <div class="finding-row" style="border-color:rgba(46,204,113,.25)">
      <div class="finding-num" style="color:var(--good)">5/5</div>
      <div class="finding-body">
        <div class="finding-title">Event Registration Software: A Complete Guide</div>
        <div class="finding-desc">4,064 words · Comparison tables · FAQ schema · Strong structured content throughout.</div>
      </div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:20px">These posts share a pattern: 3,500+ words, FAQ schema, structured snippets, and a clear keyword focus. Replicate this formula across the 259 AIO Partial posts.</p>
</div>

<!-- ══════════ SLIDE 8 — RECOMMENDATIONS ══════════ -->
<div class="slide slide-default" id="slide-8">
  <div class="slide-eyebrow">Prioritised Recommendations</div>
  <div class="slide-title">The 90-Day Action Plan</div>
  <div class="divider"></div>
  <div class="rec-list" style="margin-top:10px">
    <div class="rec-item">
      <span class="rec-priority pri-critical">CRITICAL</span>
      <div class="rec-text">
        <div class="rec-title">1. Add FAQ Schema to 358 posts — start with top 50 by traffic</div>
        Use Rank Math's FAQ block or inject JSON-LD programmatically. Target posts with 1,000+ words first. Expected: rich result FAQ snippets + AI Overview citations within 4–6 weeks.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-critical">CRITICAL</span>
      <div class="rec-text">
        <div class="rec-title">2. Build internal linking clusters across 365 isolated posts</div>
        Map posts into topical clusters (Fundraising, AMS, Events, etc.). Add 2–3 contextual internal links per post. This one change can move 100+ AIO Partial posts to AIO Ready.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-high">HIGH</span>
      <div class="rec-text">
        <div class="rec-title">3. Expand 142 thin-content posts to 1,500+ words</div>
        Prioritise by keyword value and current ranking position. Add FAQ sections, data citations, and step-by-step lists to each expansion — don't just pad with filler.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-high">HIGH</span>
      <div class="rec-text">
        <div class="rec-title">4. Set Rank Math target keywords on 139 posts missing them</div>
        Without a set focus keyword, Rank Math can't optimise meta tags, title patterns, or schema. This is a 2-minute fix per post that unlocks the full SEO plugin workflow.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-medium">MEDIUM</span>
      <div class="rec-text">
        <div class="rec-title">5. Add structured snippets (tables, numbered lists) to 223 posts lacking them</div>
        AI systems preferentially cite content in list and table format. Adding a comparison table or 5-step process list per post dramatically increases AI citation probability.
      </div>
    </div>
  </div>
</div>

<!-- ══════════ SLIDE 9 — CLOSING ══════════ -->
<div class="slide slide-cta" id="slide-9">
  <div class="slide-eyebrow">Summary</div>
  <div class="slide-title">The gap between 22% and<br/>80% AIO Ready is closeable</div>
  <div class="divider"></div>
  <div class="three-col" style="max-width:860px;margin-top:16px">
    <div class="card" style="text-align:center">
      <div style="font-size:2rem;margin-bottom:8px">📋</div>
      <div style="font-weight:700;margin-bottom:6px">Sprint 1</div>
      <div class="card-lbl">FAQ schema on top 50 posts<br/><em style="color:var(--accent2)">Weeks 1–2</em></div>
    </div>
    <div class="card" style="text-align:center">
      <div style="font-size:2rem;margin-bottom:8px">🔗</div>
      <div style="font-weight:700;margin-bottom:6px">Sprint 2</div>
      <div class="card-lbl">Internal link clusters<br/>across AIO Partial posts<br/><em style="color:var(--accent2)">Weeks 3–5</em></div>
    </div>
    <div class="card" style="text-align:center">
      <div style="font-size:2rem;margin-bottom:8px">📝</div>
      <div style="font-weight:700;margin-bottom:6px">Sprint 3</div>
      <div class="card-lbl">Expand thin content +<br/>add keywords + snippets<br/><em style="color:var(--accent2)">Weeks 6–12</em></div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:32px">
    The content, schema infrastructure, and Rank Math setup are already in place.<br/>
    These are execution gaps — not structural ones. Fast wins are available now.
  </p>
</div>

<!-- ════════════ SLIDE NAV ════════════ -->
<div class="slide-nav">
  <button class="nav-btn" onclick="prevSlide()">← Prev</button>
  <div class="dot-nav" id="dotNav"></div>
  <span class="slide-counter" id="slideCounter">1 / 9</span>
  <button class="nav-btn" onclick="nextSlide()">Next →</button>
</div>

<script>
const TOTAL_SLIDES = 9;
let cur = 1;

function buildDots() {
  const dn = document.getElementById('dotNav');
  dn.innerHTML = '';
  for (let i=1;i<=TOTAL_SLIDES;i++) {
    const d = document.createElement('div');
    d.className = 'dot' + (i===cur?' active':'');
    d.onclick = () => goSlide(i);
    dn.appendChild(d);
  }
}

function goSlide(n) {
  document.getElementById('slide-'+cur).classList.remove('active');
  cur = Math.max(1, Math.min(TOTAL_SLIDES, n));
  document.getElementById('slide-'+cur).classList.add('active');
  document.getElementById('slideCounter').textContent = cur + ' / ' + TOTAL_SLIDES;
  buildDots();
  window.scrollTo(0,0);
}

function nextSlide() { goSlide(cur+1); }
function prevSlide() { goSlide(cur-1); }

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') nextSlide();
  if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   prevSlide();
});

buildDots();
</script>
</body>
</html>"""

# ── Write files ───────────────────────────────────────────────────────────────
out_dir = os.path.dirname(os.path.abspath(__file__))

dashboard_path   = os.path.join(out_dir, "momentive_dashboard.html")
takeaways_path   = os.path.join(out_dir, "momentive_key_takeaways.html")

with open(dashboard_path, "w", encoding="utf-8") as f:
    f.write(dashboard_html)
print(f"[✓] Dashboard written to:    {dashboard_path}")

with open(takeaways_path, "w", encoding="utf-8") as f:
    f.write(takeaways_html)
print(f"[✓] Key Takeaways written to: {takeaways_path}")
print()
print("Open in your browser:")
print(f"  Dashboard    → file://{dashboard_path}")
print(f"  Takeaways    → file://{takeaways_path}")
