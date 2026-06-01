#!/usr/bin/env python3
"""
fetch_hubspot_contacts.py — Extract contacts from a HubSpot list/segment
==========================================================================
Reads contacts from a HubSpot contact list and writes them to a CSV file
with the columns needed by batch_audit_runner.py.

Usage:
    python3 fetch_hubspot_contacts.py --list-id 154293172
    python3 fetch_hubspot_contacts.py --list-id 154293172 --output my_contacts.csv
    python3 fetch_hubspot_contacts.py --list-id 154293172 --portal-id 144358290

Environment:
    Requires HUBSPOT_ACCESS_TOKEN in a .env file or environment variable.

Output CSV columns:
    first_name, job_title, email, company_name, email_domain, company_url, auditurl
"""

import argparse
import csv
import os
import sys
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("[setup] Installing requests …")
    import subprocess as _sp
    _sp.check_call([sys.executable, "-m", "pip", "install", "-q", "requests"])
    import requests


# ── Config ────────────────────────────────────────────────────────────────

OUTPUT_FILE = "contacts.csv"
BATCH_SIZE = 100  # HubSpot API page size (max 100)


# ── Helpers ───────────────────────────────────────────────────────────────

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


def domain_to_url(domain: str) -> str:
    """Convert an email domain or raw domain into a full URL."""
    domain = domain.strip().lower()
    if not domain:
        return ""
    # Remove any protocol if accidentally included
    if "://" in domain:
        domain = urlparse(domain).netloc or domain
    # Strip www.
    if domain.startswith("www."):
        domain = domain[4:]
    return f"https://www.{domain}/"


def extract_email_domain(email: str) -> str:
    """Extract domain part from an email address."""
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


# ── HubSpot API ──────────────────────────────────────────────────────────

def fetch_list_contacts(token: str, list_id: str) -> list[dict]:
    """
    Fetch all contacts from a HubSpot list using the v3 Lists API.
    Returns a list of contact dicts with the fields we need.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Step 1: Get contact IDs from the list (memberships)
    print(f"[hubspot] Fetching contact memberships from list {list_id} …")
    all_contact_ids = []
    after = None

    while True:
        url = f"https://api.hubapi.com/crm/v3/lists/{list_id}/memberships"
        params = {"limit": 100}
        if after:
            params["after"] = after

        resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 404:
            print(f"[error] List {list_id} not found. Check the list ID.")
            sys.exit(1)
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        for item in results:
            if isinstance(item, dict):
                all_contact_ids.append(item.get("recordId") or item.get("id"))
            else:
                all_contact_ids.append(item)

        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")
        if not after:
            break

    print(f"[hubspot] Found {len(all_contact_ids)} contact IDs in list")

    if not all_contact_ids:
        return []

    # Step 2: Fetch contact properties in batches
    print("[hubspot] Fetching contact details …")
    properties = ["firstname", "jobtitle", "email", "company", "hs_email_domain",
                   "domain", "website"]
    contacts = []

    # Process in batches of 100
    for i in range(0, len(all_contact_ids), BATCH_SIZE):
        batch_ids = all_contact_ids[i:i + BATCH_SIZE]
        batch_inputs = [{"id": str(cid)} for cid in batch_ids]

        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/contacts/batch/read",
            headers=headers,
            json={
                "properties": properties,
                "inputs": batch_inputs,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])

        for contact in results:
            props = contact.get("properties", {})
            email = (props.get("email") or "").strip()
            email_domain = extract_email_domain(email)

            # Try multiple sources for domain/URL
            company_domain = (
                props.get("domain") or
                props.get("hs_email_domain") or
                props.get("website") or
                email_domain
            )

            contacts.append({
                "first_name": (props.get("firstname") or "").strip(),
                "job_title": (props.get("jobtitle") or "").strip(),
                "email": email,
                "company_name": (props.get("company") or "").strip(),
                "email_domain": email_domain,
                "company_url": domain_to_url(company_domain) if company_domain else "",
            })

        print(f"  Fetched {min(i + BATCH_SIZE, len(all_contact_ids))}/{len(all_contact_ids)} contacts")

    return contacts


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch contacts from a HubSpot list and write to CSV.",
    )
    parser.add_argument("--list-id", required=True,
                        help="HubSpot list ID (from the URL: /views/<LIST_ID>/list)")
    parser.add_argument("--portal-id", default=None,
                        help="HubSpot portal ID (informational only)")
    parser.add_argument("--output", default=OUTPUT_FILE,
                        help=f"Output CSV file path (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    # Load credentials
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env = load_env(os.path.join(base_dir, ".env"))
    token = env.get("HUBSPOT_ACCESS_TOKEN") or os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        print("[error] HUBSPOT_ACCESS_TOKEN not found in .env or environment.")
        print("[error] Create a .env file with: HUBSPOT_ACCESS_TOKEN=your-token")
        sys.exit(1)

    print()
    print("━" * 60)
    print(f"  Fetch HubSpot Contacts")
    print(f"  List ID  : {args.list_id}")
    if args.portal_id:
        print(f"  Portal   : {args.portal_id}")
    print(f"  Output   : {args.output}")
    print("━" * 60)
    print()

    # Fetch contacts
    contacts = fetch_list_contacts(token, args.list_id)

    if not contacts:
        print("[warn] No contacts found in this list.")
        sys.exit(0)

    # Write CSV
    fieldnames = ["first_name", "job_title", "email", "company_name",
                  "email_domain", "company_url", "auditurl"]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for contact in contacts:
            contact["auditurl"] = ""  # blank — to be filled by batch runner
            writer.writerow(contact)

    # Summary
    has_url = sum(1 for c in contacts if c["company_url"])
    no_url = len(contacts) - has_url

    print()
    print("━" * 60)
    print(f"  Done! Wrote {len(contacts)} contacts to {args.output}")
    print(f"  With company URL: {has_url}")
    print(f"  Missing URL:      {no_url} (will be skipped by batch runner)")
    print("━" * 60)
    print()


if __name__ == "__main__":
    main()
