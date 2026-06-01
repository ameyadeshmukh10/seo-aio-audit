#!/usr/bin/env python3
"""
STEP 5 — Publish Dashboard to HubSpot
=======================================
Reads:
  - audits/{slug}/{slug}_dashboard.html   (from step 4)

Creates:
  - A HubSpot Design Manager template with the dashboard HTML
  - A HubSpot landing page using that template
  - Optionally publishes the page immediately

Usage:
    python 05_publish_hubspot.py --url https://memgraph.com/
    python 05_publish_hubspot.py --url https://memgraph.com/ --publish
    python 05_publish_hubspot.py --url https://memgraph.com/ --publish --slug-override my-custom-slug
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

# ── Helpers ────────────────────────────────────────────────────────────────

def url_to_slug(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return re.sub(r"[.\-]+", "_", domain)


def url_to_company_name(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    name = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


def load_env(path: str) -> dict:
    """Load key=value pairs from a .env file."""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def extract_html_parts(html: str) -> Tuple[str, str]:
    """
    Extract <head> inner content and <body> inner content from a full HTML doc.
    Returns (head_content, body_content).
    """
    # Extract head content
    head_match = re.search(r"<head[^>]*>(.*?)</head>", html, re.DOTALL | re.IGNORECASE)
    head_content = head_match.group(1).strip() if head_match else ""

    # Extract body content
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body_content = body_match.group(1).strip() if body_match else html

    return head_content, body_content


def build_hubl_template(head_content: str, body_content: str, company_name: str) -> str:
    """
    Wrap dashboard HTML in a HubL-compatible template.
    HubSpot requires {{ standard_header_includes }} and {{ standard_footer_includes }}.
    """
    template = f"""<!--
  templateType: page
  isAvailableForNewContent: true
  label: SEO Audit Dashboard - {company_name}
-->
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
{{{{ standard_header_includes }}}}
{head_content}
</head>
<body>
{body_content}
{{{{ standard_footer_includes }}}}
</body>
</html>"""
    return template


# ── HubSpot API ────────────────────────────────────────────────────────────

TEMPLATE_API = "https://api.hubapi.com/content/api/v2/templates"
LANDING_PAGE_API = "https://api.hubapi.com/cms/v3/pages/landing-pages"


def create_or_update_template(token: str, template_path: str, source: str) -> dict:
    """
    Create a Design Manager template. If it already exists at the given path,
    update it instead.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Check if template already exists at this path
    resp = requests.get(
        TEMPLATE_API,
        headers=headers,
        params={"path": template_path, "limit": 1},
    )
    resp.raise_for_status()
    existing = resp.json().get("objects", [])

    if existing and existing[0].get("path") == template_path:
        # Update existing template
        template_id = existing[0]["id"]
        resp = requests.put(
            f"{TEMPLATE_API}/{template_id}",
            headers=headers,
            json={"source": source},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"  [hubspot] Updated existing template (id={template_id})")
        return data

    # Create new template
    resp = requests.post(
        TEMPLATE_API,
        headers=headers,
        json={
            "category_id": 2,       # Landing page category
            "template_type": 4,     # Page template
            "path": template_path,
            "is_available_for_new_content": True,
            "source": source,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"  [hubspot] Created new template (id={data['id']})")
    return data


def find_existing_page(token: str, page_slug: str) -> Optional[dict]:
    """Check if a landing page with this slug already exists."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        LANDING_PAGE_API,
        headers=headers,
        params={"slug": page_slug, "limit": 10},
    )
    resp.raise_for_status()
    for page in resp.json().get("results", []):
        if page.get("slug") == page_slug:
            return page
    return None


def create_or_update_page(
    token: str,
    template_path: str,
    page_name: str,
    page_slug: str,
    html_title: str,
    meta_description: str,
    publish: bool,
) -> dict:
    """Create a landing page or update it if one with the same slug exists."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    existing = find_existing_page(token, page_slug)

    body = {
        "name": page_name,
        "htmlTitle": html_title,
        "slug": page_slug,
        "metaDescription": meta_description,
        "templatePath": template_path,
    }

    if existing:
        page_id = existing["id"]
        resp = requests.patch(
            f"{LANDING_PAGE_API}/{page_id}",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"  [hubspot] Updated existing page (id={page_id})")
    else:
        resp = requests.post(
            LANDING_PAGE_API,
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        page_id = data["id"]
        print(f"  [hubspot] Created new page (id={page_id})")

    # Publish if requested — push the draft live immediately (no scheduling)
    if publish:
        resp = requests.post(
            f"{LANDING_PAGE_API}/{page_id}/draft/push-live",
            headers=headers,
        )
        if resp.ok or resp.status_code == 204:
            print(f"  [hubspot] Page published live")
            data["state"] = "PUBLISHED"
        else:
            print(f"  [hubspot] Warning: publish returned {resp.status_code}: {resp.text}")

    return data


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Publish SEO audit dashboard to HubSpot as a landing page.",
    )
    parser.add_argument("--url", required=True,
                        help="Company website URL (must match the URL used in earlier steps)")
    parser.add_argument("--publish", action="store_true",
                        help="Publish the page immediately (default: create as draft)")
    parser.add_argument("--slug-override", default=None,
                        help="Custom slug for the HubSpot page URL (default: seo-audit-{slug})")
    args = parser.parse_args()

    url = args.url.rstrip("/") + "/"
    slug = url_to_slug(url)
    company_name = url_to_company_name(url)

    # Load HubSpot credentials
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env = load_env(os.path.join(base_dir, ".env"))
    token = env.get("HUBSPOT_ACCESS_TOKEN") or os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        print("[error] HUBSPOT_ACCESS_TOKEN not found in .env or environment.")
        print("[error] Create a .env file with: HUBSPOT_ACCESS_TOKEN=your-token")
        sys.exit(1)

    # Locate dashboard HTML
    output_dir = os.path.join(base_dir, "audits", slug)
    dashboard_path = os.path.join(output_dir, f"{slug}_dashboard.html")
    if not os.path.exists(dashboard_path):
        print(f"[error] Dashboard not found: {dashboard_path}")
        print(f"[error] Run steps 1-4 first: python run_audit.py --url {url}")
        sys.exit(1)

    print()
    print("─" * 60)
    print(f"  STEP 5 — Publish to HubSpot")
    print(f"  Company : {company_name}")
    print(f"  Source  : {dashboard_path}")
    print(f"  Mode    : {'PUBLISH' if args.publish else 'DRAFT'}")
    print("─" * 60)

    # Read and parse dashboard HTML
    with open(dashboard_path, encoding="utf-8") as f:
        raw_html = f.read()

    print(f"  [local] Read dashboard ({len(raw_html):,} bytes)")

    # Remove the <title> tag from head (HubSpot sets it via htmlTitle)
    head_content, body_content = extract_html_parts(raw_html)
    head_content = re.sub(r"<title[^>]*>.*?</title>", "", head_content, flags=re.DOTALL | re.IGNORECASE)
    # Remove meta charset and viewport (HubSpot adds these)
    head_content = re.sub(r'<meta\s+charset[^>]*/?\s*>', "", head_content, flags=re.IGNORECASE)
    head_content = re.sub(r'<meta\s+name="viewport"[^>]*/?\s*>', "", head_content, flags=re.IGNORECASE)

    # Build HubL template
    template_source = build_hubl_template(head_content, body_content, company_name)
    template_path = f"custom/seo-audit-{slug}.html"

    print(f"  [local] Built HubL template ({len(template_source):,} bytes)")

    # Step 1: Upload template to Design Manager
    print()
    print("  ── Creating/Updating Template ──")
    template_data = create_or_update_template(token, template_path, template_source)

    # Step 2: Create landing page
    print()
    print("  ── Creating/Updating Landing Page ──")
    page_slug = args.slug_override or f"seo-audit-{slug.replace('_', '-')}"
    page_name = f"SEO & AIO Audit Dashboard — {company_name}"
    html_title = f"{company_name} — Blog SEO & AIO Audit Dashboard"
    meta_desc = f"Interactive SEO and AIO readiness audit dashboard for {company_name}."

    page_data = create_or_update_page(
        token=token,
        template_path=template_path,
        page_name=page_name,
        page_slug=page_slug,
        html_title=html_title,
        meta_description=meta_desc,
        publish=args.publish,
    )

    page_url = page_data.get("url", "N/A")
    page_state = page_data.get("state", "UNKNOWN")

    print()
    print("─" * 60)
    print(f"  ✓ HubSpot landing page ready!")
    print(f"  State    : {page_state}")
    print(f"  URL      : {page_url}")
    print(f"  Page ID  : {page_data.get('id')}")
    print("─" * 60)
    print()

    # Save metadata for reference
    meta_path = os.path.join(output_dir, f"{slug}_hubspot.json")
    meta = {
        "company": company_name,
        "template_id": template_data.get("id"),
        "template_path": template_path,
        "page_id": page_data.get("id"),
        "page_slug": page_slug,
        "page_url": page_url,
        "state": page_state,
        "published_at": datetime.now(timezone.utc).isoformat() if args.publish else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  [local] Saved HubSpot metadata → {meta_path}")


if __name__ == "__main__":
    main()
