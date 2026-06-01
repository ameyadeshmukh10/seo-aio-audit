#!/usr/bin/env python3
"""
STEP 4 — Generate HTML Reports
================================
Reads:
  - audits/{slug}/{slug}_audit_results.json   (from step 3)
  - audits/{slug}/{slug}_pages.json           (from step 1, optional)

Writes two self-contained HTML files:
  - audits/{slug}/{slug}_dashboard.html       (interactive 4-tab audit dashboard)
  - audits/{slug}/{slug}_key_takeaways.html   (9-slide presentation deck)

Then opens both in the default browser.

Usage:
    python 04_generate_reports.py --url https://memgraph.com/
"""

import argparse, json, math, os, re, subprocess, sys
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse


# ── Helpers ────────────────────────────────────────────────────────────────

def url_to_slug(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return re.sub(r"[.\-]+", "_", domain)


def url_to_company_name(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    name   = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


def pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{n / total * 100:.1f}%"


def pct_f(n: int, total: int) -> float:
    return 0.0 if total == 0 else round(n / total * 100, 1)


# ── Stats computation ──────────────────────────────────────────────────────

def compute_stats(posts: list[dict]) -> dict:
    """Compute all aggregate statistics from the audit results."""
    valid  = [p for p in posts if not p.get("error")]
    total  = len(valid)
    errors = len(posts) - total

    if total == 0:
        return {"total": 0, "errors": errors}

    wcs        = [p["word_count"] for p in valid if isinstance(p.get("word_count"), int)]
    avg_wc     = round(sum(wcs) / len(wcs)) if wcs else 0
    max_wc     = max(wcs) if wcs else 0
    min_wc     = min(wcs) if wcs else 0

    faq_schema  = sum(1 for p in valid if p.get("has_faq_schema"))
    faq_content = sum(1 for p in valid if p.get("has_faq_content"))
    faq_gap     = sum(1 for p in valid if p.get("has_faq_content") and not p.get("has_faq_schema"))
    has_art     = sum(1 for p in valid if p.get("has_article_schema"))
    has_bread   = sum(1 for p in valid if p.get("has_breadcrumb_schema"))
    has_howto   = sum(1 for p in valid if p.get("has_howto_schema"))
    has_snip    = sum(1 for p in valid if p.get("has_snippets"))
    has_kw      = sum(1 for p in valid if p.get("target_keywords"))
    no_kw       = total - has_kw
    no_int      = sum(1 for p in valid if not (p.get("internal_blog_links") or []))
    has_direct  = sum(1 for p in valid if p.get("has_direct_answer"))
    q_headings  = sum(1 for p in valid if (p.get("question_heading_count") or 0) >= 2)
    data_cits   = sum(1 for p in valid if (p.get("data_citation_count") or 0) >= 3)

    thin   = sum(1 for p in valid if p.get("wc_tier") == "Thin")
    ok_wc  = sum(1 for p in valid if p.get("wc_tier") == "OK")
    strong = sum(1 for p in valid if p.get("wc_tier") == "Strong")

    ready   = sum(1 for p in valid if p.get("aio_tier") == "AIO Ready")
    partial = sum(1 for p in valid if p.get("aio_tier") == "AIO Partial")
    weak    = sum(1 for p in valid if p.get("aio_tier") == "AIO Weak")

    # Word count histogram buckets
    wc_buckets = {
        "0–499": 0, "500–799": 0, "800–999": 0,
        "1000–1499": 0, "1500–1999": 0, "2000+": 0,
    }
    for wc in wcs:
        if   wc < 500:  wc_buckets["0–499"]    += 1
        elif wc < 800:  wc_buckets["500–799"]  += 1
        elif wc < 1000: wc_buckets["800–999"]  += 1
        elif wc < 1500: wc_buckets["1000–1499"]+= 1
        elif wc < 2000: wc_buckets["1500–1999"]+= 1
        else:           wc_buckets["2000+"]    += 1

    # Top / Bottom posts
    sorted_posts = sorted(valid,
                          key=lambda p: ((p.get("aio_score") or 0),
                                         (p.get("word_count") or 0)),
                          reverse=True)
    top10    = sorted_posts[:10]
    bottom10 = sorted_posts[-10:][::-1]

    return {
        "total": total, "errors": errors,
        "avg_wc": avg_wc, "max_wc": max_wc, "min_wc": min_wc,
        "faq_schema": faq_schema, "faq_content": faq_content, "faq_gap": faq_gap,
        "has_article_schema": has_art, "has_breadcrumb_schema": has_bread,
        "has_howto_schema": has_howto,
        "has_snippets": has_snip, "no_snippets": total - has_snip,
        "has_keywords": has_kw, "no_keywords": no_kw,
        "no_internal_links": no_int, "has_internal_links": total - no_int,
        "has_direct_answer": has_direct,
        "q_headings_2plus": q_headings,
        "data_citations_3plus": data_cits,
        "thin": thin, "ok_wc": ok_wc, "strong": strong,
        "aio_ready": ready, "aio_partial": partial, "aio_weak": weak,
        "wc_buckets": wc_buckets,
        "top10": top10, "bottom10": bottom10,
        "all_valid": valid,
    }


# ── Enrich posts for dashboard JS ─────────────────────────────────────────

def enrich_posts(valid: list[dict]) -> list[dict]:
    """Strip verbose fields and add tier labels for the dashboard table."""
    out = []
    for p in valid:
        out.append({
            "url":                   p.get("url", ""),
            "title":                 p.get("title") or p.get("url", ""),
            "meta_description":      p.get("meta_description") or "",
            "target_keywords":       p.get("target_keywords") or [],
            "word_count":            p.get("word_count") or 0,
            "wc_tier":               p.get("wc_tier") or "Thin",
            "has_snippets":          bool(p.get("has_snippets")),
            "has_faq_content":       bool(p.get("has_faq_content")),
            "has_faq_schema":        bool(p.get("has_faq_schema")),
            "has_article_schema":    bool(p.get("has_article_schema")),
            "has_breadcrumb_schema": bool(p.get("has_breadcrumb_schema")),
            "has_direct_answer":     bool(p.get("has_direct_answer")),
            "question_heading_count":p.get("question_heading_count") or 0,
            "data_citation_count":   p.get("data_citation_count") or 0,
            "internal_blog_links_count": p.get("internal_blog_links_count") or 0,
            "solution_links_count":  len(p.get("solution_page_links") or []),
            "aio_score":             p.get("aio_score") or 0,
            "aio_tier":              p.get("aio_tier") or "AIO Weak",
            "issues":                p.get("issues") or [],
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ══════════════════════════════════════════════════════════════════════════════

def generate_dashboard(company: str, slug: str, s: dict,
                       posts_json: str, run_date: str) -> str:
    """
    Returns a fully self-contained HTML dashboard string.
    All chart data and post records are embedded as JS variables.
    """

    wc_labels  = list(s["wc_buckets"].keys())
    wc_data    = list(s["wc_buckets"].values())
    wc_colours = ["#ff0d40","#ff0d40","#f5a623","#f5a623","#1a8a4a","#1a8a4a"]

    signal_labels  = ["Article Schema","Breadcrumb","FAQ Content","Structured Snippets",
                      "Direct Answer Opening","Q&A Headings (2+)","Target Keywords","FAQ Schema","Internal Blog Links"]
    signal_data    = [
        pct_f(s["has_article_schema"],   s["total"]),
        pct_f(s["has_breadcrumb_schema"],s["total"]),
        pct_f(s["faq_content"],          s["total"]),
        pct_f(s["has_snippets"],         s["total"]),
        pct_f(s["has_direct_answer"],    s["total"]),
        pct_f(s["q_headings_2plus"],     s["total"]),
        pct_f(s["has_keywords"],         s["total"]),
        pct_f(s["faq_schema"],           s["total"]),
        pct_f(s["has_internal_links"],   s["total"]),
    ]
    signal_colours = [
        "#1a8a4a" if v >= 80 else "#f5a623" if v >= 40 else "#ff0d40"
        for v in signal_data
    ]

    aio_pcts = [
        pct_f(s["aio_ready"],   s["total"]),
        pct_f(s["aio_partial"], s["total"]),
        pct_f(s["aio_weak"],    s["total"]),
    ]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{company} — Blog SEO &amp; AIO Audit Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#ffffff;--surface:#f5f5f7;--surface2:#eaebee;--border:#d0d2d6;
    --accent:#272b31;--accent2:#48ffbd;--warn:#f5a623;--danger:#ff0d40;
    --good:#1a8a4a;--text:#111111;--muted:#787878;--r:12px;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
  .top-nav{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 32px;display:flex;align-items:center;position:sticky;top:0;z-index:100}}
  .nav-brand{{font-size:1rem;font-weight:700;color:var(--accent);padding:18px 24px 18px 0;border-right:1px solid var(--border);margin-right:24px;white-space:nowrap}}
  .nav-tab{{padding:20px 18px;font-size:.875rem;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:3px solid transparent;transition:all .2s;white-space:nowrap}}
  .nav-tab:hover{{color:var(--text)}}
  .nav-tab.active{{color:var(--accent);border-bottom-color:var(--accent)}}
  .nav-meta{{margin-left:auto;font-size:.75rem;color:var(--muted);white-space:nowrap}}
  .page{{display:none;padding:32px;max-width:1400px;margin:0 auto}}
  .page.active{{display:block}}
  h1{{font-size:1.6rem;font-weight:700;margin-bottom:4px}}
  .subtitle{{color:var(--muted);font-size:.9rem;margin-bottom:28px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:28px}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px 22px;position:relative;overflow:hidden}}
  .card::before{{content:'';position:absolute;top:0;left:0;width:4px;height:100%;background:var(--accent);border-radius:var(--r) 0 0 var(--r)}}
  .card.warn::before{{background:var(--warn)}}.card.danger::before{{background:var(--danger)}}.card.good::before{{background:var(--good)}}
  .card-label{{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}}
  .card-value{{font-size:2rem;font-weight:800;line-height:1}}
  .card-sub{{font-size:.75rem;color:var(--muted);margin-top:4px}}
  .charts-row{{display:grid;grid-template-columns:260px 1fr 1fr;gap:20px;margin-bottom:28px}}
  .chart-box{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:22px}}
  .chart-box h3{{font-size:.9rem;font-weight:600;margin-bottom:16px}}
  .chart-wrap{{position:relative}}
  .signal-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:28px}}
  .signal-item{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px;display:flex;align-items:center;gap:14px}}
  .signal-icon{{font-size:1.4rem;flex-shrink:0}}
  .signal-label{{font-size:.78rem;color:var(--muted);margin-bottom:2px}}
  .signal-val{{font-size:1.1rem;font-weight:700}}
  .sig-good{{color:var(--good)}}.sig-warn{{color:var(--warn)}}.sig-danger{{color:var(--danger)}}
  .section-head{{font-size:1rem;font-weight:700;margin:28px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}}
  .badge{{font-size:.7rem;padding:2px 8px;border-radius:20px;font-weight:600}}
  .badge-good{{background:#e6fff3;color:var(--good)}}.badge-warn{{background:#fff5e0;color:var(--warn)}}.badge-danger{{background:#ffe6eb;color:var(--danger)}}
  .tbl-wrap{{overflow-x:auto;border-radius:var(--r);border:1px solid var(--border);margin-bottom:28px}}
  table{{width:100%;border-collapse:collapse;font-size:.82rem}}
  th{{background:var(--surface2);color:var(--muted);text-transform:uppercase;font-size:.7rem;letter-spacing:.05em;padding:10px 14px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}}
  th:hover{{color:var(--text)}}
  td{{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:top}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:var(--surface2)}}
  .post-title{{font-weight:500;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .post-title a{{color:inherit;text-decoration:none}}
  .post-title a:hover{{color:var(--accent)}}
  .tier{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:700;white-space:nowrap}}
  .tier-ready{{background:#e6fff3;color:#1a8a4a}}.tier-partial{{background:#fff5e0;color:#c07b00}}.tier-weak{{background:#ffe6eb;color:#ff0d40}}
  .tier-thin{{background:#ffe6eb;color:#ff0d40}}.tier-ok{{background:#fff5e0;color:#c07b00}}.tier-strong{{background:#e6fff3;color:#1a8a4a}}
  .issue-chip{{display:inline-block;background:var(--surface2);border:1px solid var(--border);color:var(--warn);font-size:.68rem;padding:2px 7px;border-radius:4px;margin:1px;white-space:nowrap}}
  .score-bar{{display:flex;gap:3px;align-items:center}}
  .score-pip{{width:10px;height:10px;border-radius:2px;background:var(--border)}}
  .score-pip.on{{background:#272b31}}
  .filters{{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap;align-items:center}}
  .search-box{{flex:1;min-width:240px;padding:9px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.85rem;outline:none}}
  .search-box:focus{{border-color:var(--accent)}}
  .filter-select{{padding:9px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.85rem;outline:none;cursor:pointer}}
  .result-count{{font-size:.8rem;color:var(--muted);white-space:nowrap}}
  .pagination{{display:flex;gap:6px;justify-content:center;padding:16px 0 0;flex-wrap:wrap}}
  .page-btn{{padding:6px 12px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--muted);cursor:pointer;font-size:.8rem;transition:all .15s}}
  .page-btn:hover{{border-color:var(--accent);color:var(--text)}}
  .page-btn.active{{background:#272b31;border-color:#272b31;color:white}}
  .callout{{background:linear-gradient(135deg,#f8f8fa 0%,#eef0f3 100%);border:1px solid var(--border);border-left:4px solid var(--danger);border-radius:var(--r);padding:20px 24px;margin-bottom:20px}}
  .callout.warn{{border-left-color:var(--warn)}}.callout.good{{border-left-color:var(--good)}}
  .callout-title{{font-size:.9rem;font-weight:700;margin-bottom:6px}}
  .callout-body{{font-size:.82rem;color:var(--muted);line-height:1.6}}
  ::-webkit-scrollbar{{width:6px;height:6px}}
  ::-webkit-scrollbar-track{{background:#f0f0f0}}
  ::-webkit-scrollbar-thumb{{background:#c0c0c0;border-radius:3px}}
</style>
</head>
<body>

<nav class="top-nav">
  <div class="nav-brand">📊 {company} Blog Audit</div>
  <div class="nav-tab active" onclick="showPage('overview',this)">Overview</div>
  <div class="nav-tab" onclick="showPage('explorer',this)">Post Explorer</div>
  <div class="nav-tab" onclick="showPage('top10',this)">Top Posts</div>
  <div class="nav-tab" onclick="showPage('issues',this)">Issues</div>
  <div class="nav-meta">{s["total"]} posts audited · {run_date}</div>
</nav>

<!-- OVERVIEW -->
<div id="page-overview" class="page active">
  <h1>SEO &amp; AIO Audit — {company} Blog</h1>
  <p class="subtitle">Full crawl · {s["total"]} posts successfully audited · {run_date}</p>

  <div class="cards">
    <div class="card good">
      <div class="card-label">Posts Audited</div>
      <div class="card-value">{s["total"]:,}</div>
      <div class="card-sub">{s["errors"]} errors skipped</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Word Count</div>
      <div class="card-value">{s["avg_wc"]:,}</div>
      <div class="card-sub">Target ≥ 1,500</div>
    </div>
    <div class="card good">
      <div class="card-label">AIO Ready</div>
      <div class="card-value">{pct(s["aio_ready"],s["total"])}</div>
      <div class="card-sub">{s["aio_ready"]} posts score ≥ 4/5</div>
    </div>
    <div class="card danger">
      <div class="card-label">FAQ Schema Gap</div>
      <div class="card-value">{s["faq_gap"]:,}</div>
      <div class="card-sub">{pct(s["faq_gap"],s["total"])} have no FAQ schema</div>
    </div>
    <div class="card warn">
      <div class="card-label">Missing Keywords</div>
      <div class="card-value">{s["no_keywords"]:,}</div>
      <div class="card-sub">{pct(s["no_keywords"],s["total"])} have no target KW</div>
    </div>
    <div class="card danger">
      <div class="card-label">No Internal Links</div>
      <div class="card-value">{s["no_internal_links"]:,}</div>
      <div class="card-sub">{pct(s["no_internal_links"],s["total"])} have zero blog links</div>
    </div>
  </div>

  <div class="charts-row">
    <div class="chart-box">
      <h3>AIO Readiness</h3>
      <div class="chart-wrap" style="height:180px"><canvas id="aioDonut"></canvas></div>
      <div style="margin-top:14px;font-size:.78rem">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#1a8a4a">● AIO Ready</span><span>{s["aio_ready"]} ({pct(s["aio_ready"],s["total"])})</span></div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#f5a623">● AIO Partial</span><span>{s["aio_partial"]} ({pct(s["aio_partial"],s["total"])})</span></div>
        <div style="display:flex;justify-content:space-between"><span style="color:#ff0d40">● AIO Weak</span><span>{s["aio_weak"]} ({pct(s["aio_weak"],s["total"])})</span></div>
      </div>
    </div>
    <div class="chart-box">
      <h3>Word Count Distribution</h3>
      <div class="chart-wrap" style="height:220px"><canvas id="wcHist"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>AIO Signal Coverage</h3>
      <div class="chart-wrap" style="height:220px"><canvas id="signalBar"></canvas></div>
    </div>
  </div>

  <div class="signal-grid">
    <div class="signal-item"><span class="signal-icon">📰</span><div><div class="signal-label">Article Schema</div><div class="signal-val {'sig-good' if pct_f(s['has_article_schema'],s['total'])>90 else 'sig-warn'}">{s["has_article_schema"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">🗂️</span><div><div class="signal-label">Breadcrumb Schema</div><div class="signal-val {'sig-good' if pct_f(s['has_breadcrumb_schema'],s['total'])>90 else 'sig-warn'}">{s["has_breadcrumb_schema"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">❓</span><div><div class="signal-label">FAQ Content</div><div class="signal-val {'sig-good' if pct_f(s['faq_content'],s['total'])>80 else 'sig-warn'}">{s["faq_content"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">📋</span><div><div class="signal-label">FAQ Schema</div><div class="signal-val {'sig-good' if pct_f(s['faq_schema'],s['total'])>50 else 'sig-danger'}">{s["faq_schema"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">📦</span><div><div class="signal-label">Structured Snippets</div><div class="signal-val {'sig-good' if pct_f(s['has_snippets'],s['total'])>60 else 'sig-warn'}">{s["has_snippets"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">🎯</span><div><div class="signal-label">Direct Answer Opening</div><div class="signal-val {'sig-good' if pct_f(s['has_direct_answer'],s['total'])>60 else 'sig-warn'}">{s["has_direct_answer"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">🔑</span><div><div class="signal-label">Target Keywords</div><div class="signal-val {'sig-good' if pct_f(s['has_keywords'],s['total'])>70 else 'sig-warn'}">{s["has_keywords"]} / {s["total"]}</div></div></div>
    <div class="signal-item"><span class="signal-icon">🔗</span><div><div class="signal-label">Internal Blog Links</div><div class="signal-val {'sig-good' if pct_f(s['has_internal_links'],s['total'])>50 else 'sig-danger'}">{s["has_internal_links"]} / {s["total"]}</div></div></div>
  </div>

  <div class="callout">
    <div class="callout-title">🚨 Biggest AIO Gap: FAQ Schema</div>
    <div class="callout-body">{s["faq_gap"]} posts have FAQ-style content but no FAQPage schema — {pct(s["faq_gap"],s["total"])} of the blog. This is the single highest-ROI fix: structured Q&amp;A schema is what AI Overviews pull cited answers from.</div>
  </div>
  <div class="callout warn">
    <div class="callout-title">⚠️ Internal Linking Gap</div>
    <div class="callout-body">{s["no_internal_links"]} posts ({pct(s["no_internal_links"],s["total"])}) have zero internal links to other blog posts. Without topical cluster signals, posts rank in isolation and AI systems can't assess subject-matter authority.</div>
  </div>
</div>

<!-- POST EXPLORER -->
<div id="page-explorer" class="page">
  <h1>Post Explorer</h1>
  <p class="subtitle">Search, filter, and sort all {s["total"]} audited posts. Click column headers to sort.</p>
  <div class="filters">
    <input class="search-box" id="searchInput" placeholder="🔍 Search title or URL…" oninput="applyFilters()"/>
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
      <thead><tr>
        <th onclick="sortTable('title')">Title ↕</th>
        <th onclick="sortTable('aio_score')">AIO ↕</th>
        <th onclick="sortTable('aio_tier')">Tier ↕</th>
        <th onclick="sortTable('word_count')">Words ↕</th>
        <th onclick="sortTable('wc_tier')">Content ↕</th>
        <th>FAQ Schema</th><th>Snippets</th>
        <th onclick="sortTable('internal_blog_links_count')">Int. Links ↕</th>
        <th>Issues</th>
      </tr></thead>
      <tbody id="explorerBody"></tbody>
    </table>
  </div>
  <div class="pagination" id="pagination"></div>
</div>

<!-- TOP POSTS -->
<div id="page-top10" class="page">
  <h1>Top &amp; Bottom Posts by AIO Score</h1>
  <p class="subtitle">Use the top posts as your content template. The bottom posts need the most attention.</p>
  <div class="section-head">🏆 Top 10 — Highest AIO Score <span class="badge badge-good">AIO Ready</span></div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>#</th><th>Title</th><th>AIO Score</th><th>Words</th><th>FAQ Schema</th><th>Snippets</th><th>Keywords</th><th>Int. Links</th></tr></thead>
    <tbody id="top10Body"></tbody>
  </table></div>
  <div class="section-head">⚠️ Bottom 10 — Most Issues <span class="badge badge-danger">Needs Work</span></div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>#</th><th>Title</th><th>AIO Score</th><th>Words</th><th>Issues</th></tr></thead>
    <tbody id="bottom10Body"></tbody>
  </table></div>
</div>

<!-- ISSUES -->
<div id="page-issues" class="page">
  <h1>Issues Breakdown</h1>
  <p class="subtitle">Posts grouped by their most critical gap. Fix the top sections first.</p>

  <div class="section-head">📋 Missing FAQ Schema <span class="badge badge-danger" id="faqGapBadge"></span></div>
  <div class="callout"><div class="callout-title">Fix</div><div class="callout-body">Add FAQPage + Question/Answer JSON-LD schema. Many CMSs (WordPress + Rank Math, Webflow, etc.) support this natively. One template update rolls out across all posts.</div></div>
  <div class="tbl-wrap"><table><thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Tier</th><th>Snippets</th></tr></thead><tbody id="faqGapBody"></tbody></table></div>

  <div class="section-head">🔗 No Internal Blog Links <span class="badge badge-danger" id="noLinksBadge"></span></div>
  <div class="callout warn"><div class="callout-title">Fix</div><div class="callout-body">Build topical clusters. Each post should link to 2–3 related posts. Start with your highest-AIO-score posts and work down — adding links is the fastest way to move AIO Partial → AIO Ready.</div></div>
  <div class="tbl-wrap"><table><thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Score</th><th>Tier</th></tr></thead><tbody id="noLinksBody"></tbody></table></div>

  <div class="section-head">📉 Thin Content <span class="badge badge-warn" id="thinBadge"></span></div>
  <div class="callout warn"><div class="callout-title">Fix</div><div class="callout-body">Expand posts under 800 words. Add FAQ sections, data citations, step-by-step lists, and a direct-answer opening paragraph. Target 1,500–2,500 words per post.</div></div>
  <div class="tbl-wrap"><table><thead><tr><th>#</th><th>Title</th><th>Words</th><th>AIO Score</th><th>Keywords</th></tr></thead><tbody id="thinBody"></tbody></table></div>
</div>

<script>
const POSTS = {posts_json};
const COMPANY = "{company}";
const TOTAL = {s["total"]};

// ── Charts ─────────────────────────────────────────────────────────────────
(function buildCharts(){{
  new Chart(document.getElementById('aioDonut'),{{
    type:'doughnut',
    data:{{labels:['AIO Ready','AIO Partial','AIO Weak'],
           datasets:[{{data:[{s["aio_ready"]},{s["aio_partial"]},{s["aio_weak"]}],
                       backgroundColor:['#1a8a4a','#f5a623','#ff0d40'],borderWidth:0,hoverOffset:4}}]}},
    options:{{responsive:true,maintainAspectRatio:false,cutout:'68%',
              plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` ${{ctx.label}}: ${{ctx.raw}} (${{(ctx.raw/TOTAL*100).toFixed(1)}}%)`}}}}}}}}
  }});

  new Chart(document.getElementById('wcHist'),{{
    type:'bar',
    data:{{labels:{json.dumps(wc_labels)},
           datasets:[{{label:'Posts',data:{json.dumps(wc_data)},
                       backgroundColor:{json.dumps(wc_colours)},borderRadius:4,borderSkipped:false}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
              scales:{{x:{{grid:{{color:'#e0e0e0'}},ticks:{{color:'#787878',font:{{size:11}}}}}},
                       y:{{grid:{{color:'#e0e0e0'}},ticks:{{color:'#787878',font:{{size:11}}}}}}}}}}
  }});

  new Chart(document.getElementById('signalBar'),{{
    type:'bar',
    data:{{labels:{json.dumps(signal_labels)},
           datasets:[{{label:'%',data:{json.dumps(signal_data)},
                       backgroundColor:{json.dumps(signal_colours)},borderRadius:4,borderSkipped:false}}]}},
    options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
              plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` ${{ctx.raw}}%`}}}}}},
              scales:{{x:{{max:100,grid:{{color:'#e0e0e0'}},ticks:{{color:'#787878',font:{{size:10}},callback:v=>v+'%'}}}},
                       y:{{grid:{{color:'transparent'}},ticks:{{color:'#787878',font:{{size:10}}}}}}}}}}
  }});
}})();

// ── Nav ────────────────────────────────────────────────────────────────────
let explorerBuilt=false,top10Built=false,issuesBuilt=false;
function showPage(id,tab){{
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  tab.classList.add('active');
  if(id==='explorer'&&!explorerBuilt){{buildExplorer();explorerBuilt=true;}}
  if(id==='top10'&&!top10Built){{buildTop10();top10Built=true;}}
  if(id==='issues'&&!issuesBuilt){{buildIssues();issuesBuilt=true;}}
}}

// ── Utilities ──────────────────────────────────────────────────────────────
function tierBadge(t){{
  if(t==='AIO Ready')  return '<span class="tier tier-ready">Ready</span>';
  if(t==='AIO Partial')return '<span class="tier tier-partial">Partial</span>';
  return '<span class="tier tier-weak">Weak</span>';
}}
function wcBadge(t){{
  return t==='Thin'?'<span class="tier tier-thin">Thin</span>':
         t==='Strong'?'<span class="tier tier-strong">Strong</span>':
         '<span class="tier tier-ok">OK</span>';
}}
function boolIcon(v){{return v?'<span style="color:#1a8a4a">✓</span>':'<span style="color:#ff0d40">✗</span>';}}
function scorePips(s){{
  return '<div class="score-bar">'+[0,1,2,3,4].map(i=>`<div class="score-pip ${{i<s?'on':''}}"></div>`).join('')+`&nbsp;<span style="font-size:.78rem;color:var(--muted)">${{s}}/5</span></div>`;
}}

// ── Explorer ───────────────────────────────────────────────────────────────
let filtered=[...POSTS],currentPage=1,sortKey='aio_score',sortDir=-1;
const PAGE_SIZE=30;

function buildExplorer(){{applyFilters();}}
function applyFilters(){{
  const q=document.getElementById('searchInput').value.toLowerCase();
  const aio=document.getElementById('aioFilter').value;
  const wc=document.getElementById('wcFilter').value;
  const faq=document.getElementById('faqFilter').value;
  const kw=document.getElementById('kwFilter').value;
  filtered=POSTS.filter(p=>{{
    if(q&&!p.title.toLowerCase().includes(q)&&!p.url.toLowerCase().includes(q))return false;
    if(aio&&p.aio_tier!==aio)return false;
    if(wc&&p.wc_tier!==wc)return false;
    if(faq==='yes'&&!p.has_faq_schema)return false;
    if(faq==='no'&&p.has_faq_schema)return false;
    if(kw==='yes'&&(!p.target_keywords||!p.target_keywords.length))return false;
    if(kw==='no'&&p.target_keywords&&p.target_keywords.length)return false;
    return true;
  }});
  sortFiltered();currentPage=1;renderPage();
}}
function sortFiltered(){{
  filtered.sort((a,b)=>{{
    let va=a[sortKey],vb=b[sortKey];
    if(typeof va==='string'){{va=va.toLowerCase();vb=(vb||'').toLowerCase();}}
    if(typeof va==='boolean'){{va=va?1:0;vb=vb?1:0;}}
    return sortDir*(va>vb?1:va<vb?-1:0);
  }});
}}
function sortTable(k){{if(sortKey===k)sortDir*=-1;else{{sortKey=k;sortDir=-1;}}sortFiltered();currentPage=1;renderPage();}}
function renderPage(){{
  const total2=filtered.length,pages=Math.ceil(total2/PAGE_SIZE);
  const slice=filtered.slice((currentPage-1)*PAGE_SIZE,currentPage*PAGE_SIZE);
  document.getElementById('resultCount').textContent=`${{total2}} post${{total2!==1?'s':''}} shown`;
  document.getElementById('explorerBody').innerHTML=slice.map(p=>`
    <tr>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td>
      <td>${{tierBadge(p.aio_tier)}}</td>
      <td>${{p.word_count.toLocaleString()}}</td>
      <td>${{wcBadge(p.wc_tier)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_faq_schema)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td>
      <td style="text-align:center">${{p.internal_blog_links_count}}</td>
      <td>${{(p.issues||[]).map(i=>`<span class="issue-chip">${{i}}</span>`).join(' ')}}</td>
    </tr>`).join('');
  let html='';
  for(let i=1;i<=pages;i++){{
    if(i===1||i===pages||Math.abs(i-currentPage)<=2)
      html+=`<button class="page-btn ${{i===currentPage?'active':''}}" onclick="goPage(${{i}})">${{i}}</button>`;
    else if(Math.abs(i-currentPage)===3)
      html+=`<span style="color:var(--muted);padding:6px 4px">…</span>`;
  }}
  document.getElementById('pagination').innerHTML=html;
}}
function goPage(n){{currentPage=n;renderPage();window.scrollTo(0,120);}}

// ── Top / Bottom ───────────────────────────────────────────────────────────
function buildTop10(){{
  const sorted=[...POSTS].sort((a,b)=>(b.aio_score-a.aio_score)||(b.word_count-a.word_count));
  document.getElementById('top10Body').innerHTML=sorted.slice(0,10).map((p,i)=>`
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td><td>${{p.word_count.toLocaleString()}}</td>
      <td style="text-align:center">${{boolIcon(p.has_faq_schema)}}</td>
      <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td>
      <td style="font-size:.78rem;color:var(--muted)">${{(p.target_keywords||[]).slice(0,2).join(', ')||'—'}}</td>
      <td style="text-align:center">${{p.internal_blog_links_count}}</td>
    </tr>`).join('');
  document.getElementById('bottom10Body').innerHTML=sorted.slice(-10).reverse().map((p,i)=>`
    <tr>
      <td style="color:var(--muted)">${{i+1}}</td>
      <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
      <td>${{scorePips(p.aio_score)}}</td><td>${{p.word_count.toLocaleString()}}</td>
      <td>${{(p.issues||[]).map(i=>`<span class="issue-chip">${{i}}</span>`).join(' ')}}</td>
    </tr>`).join('');
}}

// ── Issues ─────────────────────────────────────────────────────────────────
function buildIssues(){{
  const faqGap=POSTS.filter(p=>p.has_faq_content&&!p.has_faq_schema).sort((a,b)=>b.word_count-a.word_count);
  document.getElementById('faqGapBadge').textContent=faqGap.length+' posts';
  document.getElementById('faqGapBody').innerHTML=faqGap.map((p,i)=>`
    <tr><td style="color:var(--muted)">${{i+1}}</td>
    <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
    <td>${{p.word_count.toLocaleString()}}</td><td>${{tierBadge(p.aio_tier)}}</td>
    <td style="text-align:center">${{boolIcon(p.has_snippets)}}</td></tr>`).join('');

  const noLinks=POSTS.filter(p=>p.internal_blog_links_count===0).sort((a,b)=>b.aio_score-a.aio_score||b.word_count-a.word_count);
  document.getElementById('noLinksBadge').textContent=noLinks.length+' posts';
  document.getElementById('noLinksBody').innerHTML=noLinks.map((p,i)=>`
    <tr><td style="color:var(--muted)">${{i+1}}</td>
    <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
    <td>${{p.word_count.toLocaleString()}}</td><td>${{scorePips(p.aio_score)}}</td>
    <td>${{tierBadge(p.aio_tier)}}</td></tr>`).join('');

  const thin=POSTS.filter(p=>p.wc_tier==='Thin').sort((a,b)=>a.word_count-b.word_count);
  document.getElementById('thinBadge').textContent=thin.length+' posts';
  document.getElementById('thinBody').innerHTML=thin.map((p,i)=>`
    <tr><td style="color:var(--muted)">${{i+1}}</td>
    <td class="post-title"><a href="${{p.url}}" target="_blank">${{p.title||p.url}}</a></td>
    <td style="color:var(--danger)">${{p.word_count.toLocaleString()}}</td>
    <td>${{scorePips(p.aio_score)}}</td>
    <td style="font-size:.78rem;color:var(--muted)">${{(p.target_keywords||[]).slice(0,2).join(', ')||'—'}}</td></tr>`).join('');
}}
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# KEY TAKEAWAYS HTML (Presentation slide deck)
# ══════════════════════════════════════════════════════════════════════════════

def generate_takeaways(company: str, s: dict, run_date: str,
                        top3: list[dict]) -> str:
    """
    Returns a 9-slide, keyboard-navigable presentation HTML string.
    All content is derived from the audit statistics.
    """

    # Build top-3 post rows for slide 7
    def post_row(p: dict, score_label: str) -> str:
        title = (p.get("title") or p.get("url") or "")[:80]
        wc    = p.get("word_count") or 0
        kws   = ", ".join((p.get("target_keywords") or [])[:3]) or "—"
        notes = []
        if p.get("has_faq_schema"):    notes.append("FAQ schema")
        if p.get("has_snippets"):      notes.append("structured snippets")
        if p.get("has_direct_answer"): notes.append("direct answer opening")
        return f"""
        <div style="background:rgba(0,0,0,.03);border:1px solid rgba(26,138,74,.25);border-radius:12px;
                    padding:20px 24px;margin-bottom:14px;display:flex;align-items:flex-start;gap:20px">
          <div style="font-size:2rem;font-weight:900;color:#1a8a4a;min-width:50px;text-align:center;line-height:1;padding-top:2px">{score_label}</div>
          <div>
            <div style="font-size:1.05rem;font-weight:700;margin-bottom:5px">{title}</div>
            <div style="font-size:.84rem;color:var(--muted);line-height:1.6">{wc:,} words · Keywords: {kws} · {", ".join(notes) or "needs improvement"}</div>
          </div>
        </div>"""

    top3_html = "".join(post_row(p, f"{p.get('aio_score',0)}/5") for p in top3[:3])

    # Dynamic callouts based on worst signal
    worst_signal_pct = pct_f(s["faq_gap"], s["total"])
    int_link_pct     = pct_f(s["no_internal_links"], s["total"])
    thin_pct         = pct_f(s["thin"], s["total"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{company} — Blog Audit Key Takeaways</title>
<style>
  :root{{--bg:#ffffff;--surface:#f5f5f7;--border:#d0d2d6;--accent:#272b31;--accent2:#48ffbd;--warn:#f5a623;--danger:#ff0d40;--good:#1a8a4a;--text:#111111;--muted:#787878}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}}
  .slide{{display:none;min-height:100vh;flex-direction:column;justify-content:center;align-items:center;padding:60px 80px;position:relative}}
  .slide.active{{display:flex}}
  .slide-cover{{background:radial-gradient(ellipse at 30% 50%,#eef0f5 0%,var(--bg) 65%)}}
  .slide-dark{{background:var(--surface)}}
  .slide-default{{background:var(--bg)}}
  .slide-cta{{background:radial-gradient(ellipse at 70% 50%,#e6fff3 0%,var(--bg) 65%)}}
  .slide-nav{{position:fixed;bottom:32px;left:50%;transform:translateX(-50%);display:flex;gap:12px;align-items:center;background:rgba(255,255,255,.92);backdrop-filter:blur(12px);border:1px solid var(--border);border-radius:40px;padding:10px 20px;z-index:999}}
  .nav-btn{{background:none;border:1px solid var(--border);color:var(--muted);padding:7px 18px;border-radius:20px;cursor:pointer;font-size:.85rem;transition:all .2s}}
  .nav-btn:hover{{border-color:var(--accent);color:var(--text)}}
  .slide-counter{{font-size:.8rem;color:var(--muted);min-width:50px;text-align:center}}
  .dot-nav{{display:flex;gap:6px;align-items:center}}
  .dot{{width:7px;height:7px;border-radius:50%;background:var(--border);cursor:pointer;transition:all .2s}}
  .dot.active{{background:var(--accent);transform:scale(1.3)}}
  .slide-eyebrow{{font-size:.8rem;text-transform:uppercase;letter-spacing:.15em;color:var(--accent);margin-bottom:14px;font-weight:600}}
  .slide-title{{font-size:clamp(2rem,4vw,3.2rem);font-weight:800;line-height:1.1;margin-bottom:16px;text-align:center}}
  .slide-sub{{font-size:1.05rem;color:var(--muted);text-align:center;line-height:1.6;max-width:640px}}
  .stat-hero{{font-size:clamp(4rem,10vw,7rem);font-weight:900;line-height:1}}
  .stat-hero.danger{{color:var(--danger)}}.stat-hero.warn{{color:var(--warn)}}.stat-hero.good{{color:var(--good)}}.stat-hero.accent{{color:var(--accent)}}
  .divider{{width:60px;height:3px;background:var(--accent);border-radius:2px;margin:18px auto}}
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:28px;width:100%;max-width:1000px}}
  .three-col{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;width:100%;max-width:1100px}}
  .card{{background:rgba(0,0,0,.03);border:1px solid var(--border);border-radius:16px;padding:28px 30px;text-align:center}}
  .card.danger{{border-color:rgba(255,13,64,.3);background:rgba(255,13,64,.06)}}
  .card.warn{{border-color:rgba(245,166,35,.3);background:rgba(245,166,35,.06)}}
  .card.good{{border-color:rgba(46,204,113,.4);background:rgba(26,138,74,.06)}}
  .card-num{{font-size:2.8rem;font-weight:900;line-height:1;margin-bottom:6px}}
  .card-lbl{{font-size:.82rem;color:var(--muted);line-height:1.4}}
  .prog-row{{width:100%;max-width:700px;margin-bottom:12px}}
  .prog-label{{display:flex;justify-content:space-between;margin-bottom:5px;font-size:.85rem}}
  .prog-bar{{background:var(--border);border-radius:6px;height:12px;overflow:hidden}}
  .prog-fill{{height:100%;border-radius:6px}}
  .rec-list{{display:flex;flex-direction:column;gap:12px;width:100%;max-width:860px}}
  .rec-item{{display:flex;align-items:flex-start;gap:14px;background:rgba(39,43,49,.05);border:1px solid rgba(39,43,49,.15);border-radius:10px;padding:14px 18px}}
  .rec-priority{{font-size:.7rem;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;margin-top:2px}}
  .pri-critical{{background:rgba(224,82,96,.2);color:var(--danger)}}
  .pri-high{{background:rgba(245,166,35,.1);color:var(--warn)}}
  .pri-medium{{background:rgba(39,43,49,.1);color:#555555}}
  .rec-text{{font-size:.88rem;line-height:1.55}}
  .rec-title{{font-weight:700;margin-bottom:2px;font-size:.92rem}}
  .logo-area{{font-size:1.3rem;font-weight:800;color:var(--accent);letter-spacing:-.01em;margin-bottom:6px}}
  .logo-sub{{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}}
  .tier-block{{border-radius:12px;padding:22px 24px;text-align:center}}
</style>
</head>
<body>

<!-- SLIDE 1 — COVER -->
<div class="slide slide-cover active" id="slide-1">
  <div class="logo-area">{company}</div>
  <div class="logo-sub">Blog Audit · {run_date}</div>
  <div class="divider"></div>
  <div class="slide-title">SEO &amp; AI Overview<br/>Optimization Audit</div>
  <p class="slide-sub">Full technical and content audit of {s["total"]} live blog posts — measuring SEO health, AIO readiness, and content effectiveness.</p>
  <div style="margin-top:40px;display:flex;gap:40px;text-align:center">
    <div><div style="font-size:2rem;font-weight:900;color:var(--accent)">{s["total"]:,}</div><div style="font-size:.78rem;color:var(--muted)">Posts Audited</div></div>
    <div><div style="font-size:2rem;font-weight:900;color:var(--accent2)">5</div><div style="font-size:.78rem;color:var(--muted)">AIO Signals Scored</div></div>
    <div><div style="font-size:2rem;font-weight:900;color:var(--warn)">3</div><div style="font-size:.78rem;color:var(--muted)">Priority Actions</div></div>
  </div>
</div>

<!-- SLIDE 2 — AIO READINESS -->
<div class="slide slide-default" id="slide-2">
  <div class="slide-eyebrow">AIO Readiness Overview</div>
  <div class="slide-title">Only {pct(s["aio_ready"],s["total"])} of posts<br/>are AI-Overview Ready</div>
  <div class="divider"></div>
  <div class="three-col" style="margin-top:10px">
    <div class="tier-block" style="background:rgba(26,138,74,.08);border:1px solid rgba(26,138,74,.3)">
      <div style="font-size:2.8rem;font-weight:900;color:var(--good)">{pct(s["aio_ready"],s["total"])}</div>
      <h3 style="font-size:1.4rem;font-weight:800">AIO Ready</h3>
      <p style="font-size:.8rem;color:var(--muted);margin-top:4px">{s["aio_ready"]} posts · score 4–5/5<br/>FAQ schema + snippets + keywords + 1,200w+</p>
    </div>
    <div class="tier-block" style="background:rgba(245,166,35,.08);border:1px solid rgba(245,166,35,.3)">
      <div style="font-size:2.8rem;font-weight:900;color:var(--warn)">{pct(s["aio_partial"],s["total"])}</div>
      <h3 style="font-size:1.4rem;font-weight:800">AIO Partial</h3>
      <p style="font-size:.8rem;color:var(--muted);margin-top:4px">{s["aio_partial"]} posts · score 2–3/5<br/>Missing 1–2 signals — closest to promotion</p>
    </div>
    <div class="tier-block" style="background:rgba(255,13,64,.08);border:1px solid rgba(224,82,96,.3)">
      <div style="font-size:2.8rem;font-weight:900;color:var(--danger)">{pct(s["aio_weak"],s["total"])}</div>
      <h3 style="font-size:1.4rem;font-weight:800">AIO Weak</h3>
      <p style="font-size:.8rem;color:var(--muted);margin-top:4px">{s["aio_weak"]} posts · score 0–1/5<br/>Thin content, no structure, no schema</p>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:28px">Scored on: FAQ Schema · FAQ Content · Structured Snippets · Target Keywords · Word Count ≥ 1,200</p>
</div>

<!-- SLIDE 3 — FAQ SCHEMA GAP -->
<div class="slide slide-dark" id="slide-3">
  <div class="slide-eyebrow">Finding #1 — Critical</div>
  <div style="text-align:center">
    <div style="font-size:.8rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:8px">Posts with FAQ content but no FAQ schema</div>
    <div class="stat-hero danger">{s["faq_gap"]:,}</div>
    <div style="font-size:1.3rem;color:var(--muted);margin-top:8px">of {s["total"]} total posts ({pct(s["faq_gap"],s["total"])})</div>
  </div>
  <div class="divider"></div>
  <div class="two-col" style="max-width:760px;margin-top:10px">
    <div class="card danger" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--danger)">What's happening</div>
      <div style="font-size:.84rem;color:var(--muted);line-height:1.6">{s["faq_content"]} posts contain FAQ-style Q&amp;A content — but <strong style="color:var(--text)">{pct(s["faq_gap"],s["total"])}</strong> have no FAQPage schema. Google and AI systems can't extract structured answers from untagged content.</div>
    </div>
    <div class="card good" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--good)">The opportunity</div>
      <div style="font-size:.84rem;color:var(--muted);line-height:1.6">Adding FAQ schema is a <strong style="color:var(--text)">templated, scalable fix</strong>. One CMS update deploys across all posts. Done on the top 50 posts by traffic, this can unlock rich result snippets and AI citations within weeks.</div>
    </div>
  </div>
</div>

<!-- SLIDE 4 — INTERNAL LINKING -->
<div class="slide slide-default" id="slide-4">
  <div class="slide-eyebrow">Finding #2 — Critical</div>
  <div style="text-align:center">
    <div style="font-size:.8rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:8px">Posts with zero internal blog links</div>
    <div class="stat-hero danger">{pct(s["no_internal_links"],s["total"])}</div>
    <div style="font-size:1.3rem;color:var(--muted);margin-top:8px">{s["no_internal_links"]} of {s["total"]} posts are isolated dead ends</div>
  </div>
  <div class="divider"></div>
  <div class="two-col" style="max-width:760px;margin-top:10px">
    <div class="card danger" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--danger)">Why it matters</div>
      <div style="font-size:.84rem;color:var(--muted);line-height:1.6">No internal links = no topical cluster signals. Google and AI systems measure subject-matter authority by how related content is interconnected. Isolated posts rank lower and are rarely cited.</div>
    </div>
    <div class="card warn" style="text-align:left">
      <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--warn)">The fix</div>
      <div style="font-size:.84rem;color:var(--muted);line-height:1.6">Build topic clusters. Each post should link to <strong style="color:var(--text)">2–3 related posts</strong>. Start with AIO Partial posts ({s["aio_partial"]} posts) — adding links is the fastest way to move them to AIO Ready.</div>
    </div>
  </div>
</div>

<!-- SLIDE 5 — CONTENT DEPTH -->
<div class="slide slide-dark" id="slide-5">
  <div class="slide-eyebrow">Finding #3 — High Priority</div>
  <div class="slide-title">Content depth is below<br/>the AIO threshold</div>
  <div class="divider"></div>
  <div style="width:100%;max-width:680px;margin-top:8px">
    <div class="prog-row">
      <div class="prog-label"><span>Thin (&lt;800 words)</span><span style="color:var(--danger)">{s["thin"]} posts · {pct(s["thin"],s["total"])}</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:{pct_f(s["thin"],s["total"])}%;background:var(--danger)"></div></div>
    </div>
    <div class="prog-row">
      <div class="prog-label"><span>OK (800–1,499 words)</span><span style="color:var(--warn)">{s["ok_wc"]} posts · {pct(s["ok_wc"],s["total"])}</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:{pct_f(s["ok_wc"],s["total"])}%;background:var(--warn)"></div></div>
    </div>
    <div class="prog-row">
      <div class="prog-label"><span>Strong (1,500+ words)</span><span style="color:var(--good)">{s["strong"]} posts · {pct(s["strong"],s["total"])}</span></div>
      <div class="prog-bar"><div class="prog-fill" style="width:{pct_f(s["strong"],s["total"])}%;background:var(--good)"></div></div>
    </div>
  </div>
  <div class="two-col" style="max-width:760px;margin-top:24px">
    <div class="card" style="text-align:left">
      <div style="font-size:1.5rem;font-weight:900;color:var(--warn);margin-bottom:4px">{s["avg_wc"]:,}</div>
      <div class="card-lbl">Average word count. AI systems prefer comprehensive content — target is 1,500–2,500 words per post.</div>
    </div>
    <div class="card" style="text-align:left">
      <div style="font-size:1.5rem;font-weight:900;color:var(--warn);margin-bottom:4px">{pct(s["no_snippets"],s["total"])}</div>
      <div class="card-lbl">Posts have no structured snippets — the exact content format AI systems prefer to quote and cite.</div>
    </div>
  </div>
</div>

<!-- SLIDE 6 — WHAT'S WORKING -->
<div class="slide slide-default" id="slide-6">
  <div class="slide-eyebrow">What's Working</div>
  <div class="slide-title">The technical foundation<br/>is {'solid' if s['has_article_schema'] > s['total'] * 0.8 else 'partially in place'}</div>
  <div class="divider"></div>
  <div class="three-col" style="margin-top:10px">
    <div class="card {'good' if s['has_article_schema'] > s['total']*0.8 else 'warn'}">
      <div class="card-num" style="color:{'var(--good)' if s['has_article_schema'] > s['total']*0.8 else 'var(--warn)'}">{pct(s["has_article_schema"],s["total"])}</div>
      <div class="card-lbl">Article / BlogPosting schema present</div>
    </div>
    <div class="card {'good' if s['has_breadcrumb_schema'] > s['total']*0.8 else 'warn'}">
      <div class="card-num" style="color:{'var(--good)' if s['has_breadcrumb_schema'] > s['total']*0.8 else 'var(--warn)'}">{pct(s["has_breadcrumb_schema"],s["total"])}</div>
      <div class="card-lbl">Breadcrumb schema in place</div>
    </div>
    <div class="card {'good' if s['faq_content'] > s['total']*0.7 else 'warn'}">
      <div class="card-num" style="color:{'var(--good)' if s['faq_content'] > s['total']*0.7 else 'var(--warn)'}">{pct(s["faq_content"],s["total"])}</div>
      <div class="card-lbl">Posts already contain FAQ-style content</div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:28px">The scaffolding is in place. {company} doesn't need to rebuild — it needs to close three specific gaps: <strong>FAQ schema, internal linking, and content depth</strong>. The content already exists; the optimisation is in how it's structured and connected.</p>
</div>

<!-- SLIDE 7 — TOP PERFORMERS -->
<div class="slide slide-dark" id="slide-7">
  <div class="slide-eyebrow">Benchmark Posts</div>
  <div class="slide-title">The AIO Ready posts<br/>show the blueprint</div>
  <div class="divider"></div>
  <div style="width:100%;max-width:860px;margin-top:8px">
    {top3_html}
  </div>
  <p class="slide-sub" style="margin-top:20px">These posts share a pattern: 1,500+ words, FAQ schema, structured snippets, clear keyword focus. Replicate this formula across the {s["aio_partial"]} AIO Partial posts.</p>
</div>

<!-- SLIDE 8 — RECOMMENDATIONS -->
<div class="slide slide-default" id="slide-8">
  <div class="slide-eyebrow">Prioritised Recommendations</div>
  <div class="slide-title">The 90-Day Action Plan</div>
  <div class="divider"></div>
  <div class="rec-list" style="margin-top:10px">
    <div class="rec-item">
      <span class="rec-priority pri-critical">CRITICAL</span>
      <div class="rec-text">
        <div class="rec-title">1. Add FAQ Schema to {s["faq_gap"]} posts — prioritise top 50 by estimated traffic</div>
        Use your CMS FAQ block or inject FAQPage JSON-LD programmatically. All {s["faq_content"]} posts already have the Q&amp;A content — this is a tagging exercise, not a writing exercise.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-critical">CRITICAL</span>
      <div class="rec-text">
        <div class="rec-title">2. Build internal linking clusters — fix {s["no_internal_links"]} isolated posts</div>
        Map posts into topical clusters. Add 2–3 contextual internal links per post. Start with AIO Partial posts — internal links alone can push them to AIO Ready.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-high">HIGH</span>
      <div class="rec-text">
        <div class="rec-title">3. Expand {s["thin"]} thin-content posts (&lt;800 words) to 1,500+ words</div>
        Prioritise by keyword value. Add FAQ sections, data citations, and step-by-step structure. Don't pad — add genuine depth: definitions, comparisons, how-to steps.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-high">HIGH</span>
      <div class="rec-text">
        <div class="rec-title">4. Set target keywords on {s["no_keywords"]} posts currently missing them</div>
        Without a defined focus keyword, your CMS can't optimise title patterns, meta tags, or schema. This is a 2-minute fix per post.
      </div>
    </div>
    <div class="rec-item">
      <span class="rec-priority pri-medium">MEDIUM</span>
      <div class="rec-text">
        <div class="rec-title">5. Add structured snippets to {s["no_snippets"]} posts lacking them</div>
        Comparison tables, numbered step-lists, and definition boxes are the content formats AI systems cite most frequently. One structured element per post, added to existing content.
      </div>
    </div>
  </div>
</div>

<!-- SLIDE 9 — CLOSING -->
<div class="slide slide-cta" id="slide-9">
  <div class="slide-eyebrow">Summary</div>
  <div class="slide-title">The path from {pct(s["aio_ready"],s["total"])}<br/>to 80% AIO Ready is clear</div>
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
      <div class="card-lbl">Internal link clusters across<br/>AIO Partial posts<br/><em style="color:var(--accent2)">Weeks 3–5</em></div>
    </div>
    <div class="card" style="text-align:center">
      <div style="font-size:2rem;margin-bottom:8px">📝</div>
      <div style="font-weight:700;margin-bottom:6px">Sprint 3</div>
      <div class="card-lbl">Expand thin content +<br/>add keywords + snippets<br/><em style="color:var(--accent2)">Weeks 6–12</em></div>
    </div>
  </div>
  <p class="slide-sub" style="margin-top:32px">
    The content, schema infrastructure, and site structure are already in place.<br/>
    These are <strong>execution gaps</strong> — not structural ones. Fast wins are available immediately.
  </p>
</div>

<div class="slide-nav">
  <button class="nav-btn" onclick="prevSlide()">← Prev</button>
  <div class="dot-nav" id="dotNav"></div>
  <span class="slide-counter" id="slideCounter">1 / 9</span>
  <button class="nav-btn" onclick="nextSlide()">Next →</button>
</div>

<script>
const TOTAL_SLIDES=9;let cur=1;
function buildDots(){{
  const dn=document.getElementById('dotNav');dn.innerHTML='';
  for(let i=1;i<=TOTAL_SLIDES;i++){{
    const d=document.createElement('div');
    d.className='dot'+(i===cur?' active':'');
    d.onclick=()=>goSlide(i);dn.appendChild(d);
  }}
}}
function goSlide(n){{
  document.getElementById('slide-'+cur).classList.remove('active');
  cur=Math.max(1,Math.min(TOTAL_SLIDES,n));
  document.getElementById('slide-'+cur).classList.add('active');
  document.getElementById('slideCounter').textContent=cur+' / '+TOTAL_SLIDES;
  buildDots();window.scrollTo(0,0);
}}
function nextSlide(){{goSlide(cur+1);}}
function prevSlide(){{goSlide(cur-1);}}
document.addEventListener('keydown',e=>{{
  if(e.key==='ArrowRight'||e.key==='ArrowDown')nextSlide();
  if(e.key==='ArrowLeft'||e.key==='ArrowUp')prevSlide();
}});
buildDots();
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML dashboard + key takeaways deck from audit results."
    )
    parser.add_argument("--url", required=True,
                        help="Base URL (e.g. https://memgraph.com/)")
    args = parser.parse_args()

    base_url     = args.url.rstrip("/") + "/"
    slug         = url_to_slug(base_url)
    company_name = url_to_company_name(base_url)
    output_dir   = os.path.join("audits", slug)
    run_date     = datetime.now().strftime("%B %Y")

    # Load audit results
    results_path = os.path.join(output_dir, f"{slug}_audit_results.json")
    if not os.path.exists(results_path):
        print(f"[error] Audit results not found: {results_path}")
        print(f"[error] Run step 3 first: python 03_audit_posts.py --url {args.url}")
        sys.exit(1)

    with open(results_path, encoding="utf-8") as f:
        raw_posts = json.load(f)

    # Compute statistics
    print(f"[4/reports] Computing stats for {len(raw_posts)} posts …")
    stats = compute_stats(raw_posts)
    if stats["total"] == 0:
        print("[error] No valid posts found in audit results.")
        sys.exit(1)

    print(f"[4/reports] Valid posts: {stats['total']} | Errors: {stats['errors']}")
    print(f"[4/reports] AIO Ready: {stats['aio_ready']} | Partial: {stats['aio_partial']} | Weak: {stats['aio_weak']}")

    # Prepare posts JSON for dashboard
    enriched  = enrich_posts(stats["all_valid"])
    posts_json = json.dumps(enriched, ensure_ascii=False)

    # Generate dashboard
    print("[4/reports] Building dashboard …")
    dashboard_html = generate_dashboard(company_name, slug, stats,
                                         posts_json, run_date)

    # Generate takeaways
    print("[4/reports] Building key takeaways deck …")
    takeaways_html = generate_takeaways(company_name, stats, run_date,
                                         stats["top10"][:3])

    # Write files
    dash_path = os.path.join(output_dir, f"{slug}_dashboard.html")
    take_path = os.path.join(output_dir, f"{slug}_key_takeaways.html")

    with open(dash_path, "w", encoding="utf-8") as f:
        f.write(dashboard_html)
    with open(take_path, "w", encoding="utf-8") as f:
        f.write(takeaways_html)

    print(f"\n[4/reports] ✓ Dashboard    → {dash_path}")
    print(f"[4/reports] ✓ Takeaways   → {take_path}")

    # Open in browser
    for path in [dash_path, take_path]:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", path])
            elif sys.platform == "win32":
                os.startfile(path)
        except Exception:
            pass

    print(f"\n[4/reports] All done! Open in browser:")
    print(f"  Dashboard  → file://{os.path.abspath(dash_path)}")
    print(f"  Takeaways  → file://{os.path.abspath(take_path)}")


if __name__ == "__main__":
    main()
