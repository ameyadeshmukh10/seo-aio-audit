#!/usr/bin/env python3
"""
STEP 6 — Update HubSpot Contact Property
==========================================
Sets the `seo_aio_geo_audit` property on one or more HubSpot contacts
(looked up by email) to the published audit URL from step 5.

Usage:
    python3 06_update_hubspot_contact.py --email user@company.com --audit-url https://...
    python3 06_update_hubspot_contact.py --email a@b.com --email c@d.com --audit-url https://...
"""

import argparse
import os
import sys
from urllib.parse import quote

try:
    import requests
except ImportError:
    import subprocess as _sp
    _sp.check_call([sys.executable, "-m", "pip", "install", "-q", "requests"])
    import requests


PROPERTY_NAME = "seo_aio_geo_audit"
CONTACTS_API  = "https://api.hubapi.com/crm/v3/objects/contacts"


def load_env(path: str) -> dict:
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


def update_contact_property(token: str, email: str, audit_url: str) -> tuple[bool, str]:
    """
    PATCH the contact identified by `email`, setting `seo_aio_geo_audit` to `audit_url`.
    Returns (ok, message).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    encoded = quote(email, safe="")
    url     = f"{CONTACTS_API}/{encoded}?idProperty=email"
    body    = {"properties": {PROPERTY_NAME: audit_url}}

    try:
        resp = requests.patch(url, headers=headers, json=body, timeout=20)
    except Exception as e:
        return False, f"request failed: {e}"

    if resp.status_code == 404:
        return False, "contact not found"
    if not resp.ok:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return True, "updated"


def main():
    parser = argparse.ArgumentParser(
        description="Update the seo_aio_geo_audit contact property in HubSpot.",
    )
    parser.add_argument("--email", action="append", required=True,
                        help="Contact email (repeat for multiple contacts)")
    parser.add_argument("--audit-url", required=True,
                        help="Published audit URL to write into the property")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    env      = load_env(os.path.join(base_dir, ".env"))
    token    = env.get("HUBSPOT_ACCESS_TOKEN") or os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        print("[error] HUBSPOT_ACCESS_TOKEN not found in .env or environment.")
        sys.exit(1)

    emails = [e.strip() for e in args.email if e and e.strip()]
    if not emails:
        print("[error] No valid emails provided.")
        sys.exit(1)

    print()
    print("─" * 60)
    print(f"  STEP 6 — Update HubSpot Contact Property")
    print(f"  Property : {PROPERTY_NAME}")
    print(f"  Value    : {args.audit_url}")
    print(f"  Contacts : {len(emails)}")
    print("─" * 60)

    ok_count = 0
    for email in emails:
        ok, msg = update_contact_property(token, email, args.audit_url)
        marker = "✓" if ok else "✗"
        print(f"  {marker}  {email:<40} — {msg}")
        if ok:
            ok_count += 1

    print()
    print(f"  Updated {ok_count}/{len(emails)} contacts.")
    if ok_count == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
