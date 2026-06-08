"""
CalAIM Dashboard Updater
------------------------
Fetches RSS feeds and news pages, asks Claude to identify relevant CalAIM
updates, and writes the results to data.json for the dashboard to display.

Runs in two modes:
  daily  — scans for items from the last 24 hours, adds to dailyUpdates
  weekly — scans the last 7 days, archives current version, creates new current

Required env vars:
  ANTHROPIC_API_KEY  — your Anthropic API key (set as a GitHub secret)
  SCAN_TYPE          — "daily" or "weekly" (set by the workflow)
"""

import anthropic
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

DATA_FILE = "data.json"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

SCAN_TYPE = os.environ.get("SCAN_TYPE", "daily")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set")
    sys.exit(1)

client = anthropic.Anthropic(api_key=API_KEY)

now = datetime.now(timezone.utc)
date_str = now.strftime("%B %-d, %Y")   # e.g. "June 5, 2026"
date_id  = now.strftime("%Y-%m-%d")

lookback_days = 1 if SCAN_TYPE == "daily" else 7
cutoff = now - timedelta(days=lookback_days)

print(f"Running {SCAN_TYPE} scan for {date_str} (lookback: {lookback_days} day(s))")

# ── Fetch content from sources ────────────────────────────────────────────────

def fetch_rss(url, max_items=20):
    """Parse an RSS feed, return list of (title, link, published, summary) tuples."""
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            pub_dt = datetime(*published[:6], tzinfo=timezone.utc) if published else now
            if pub_dt >= cutoff:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": pub_dt.strftime("%B %-d, %Y"),
                    "summary": BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:400]
                })
        return items
    except Exception as e:
        print(f"  RSS fetch failed for {url}: {e}")
        return []

def fetch_page_headlines(url, selector, base_url="", max_items=15):
    """Scrape headlines from a page using a CSS selector."""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "CalAIM-Scanner/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for el in soup.select(selector)[:max_items]:
            link_tag = el.find("a") or el
            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")
            if href and not href.startswith("http"):
                href = base_url + href
            if title:
                items.append({"title": title, "link": href, "published": date_str, "summary": ""})
        return items
    except Exception as e:
        print(f"  Page fetch failed for {url}: {e}")
        return []

print("Fetching sources...")

raw_items = []

# CalMatters Health RSS
print("  CalMatters Health RSS...")
raw_items += fetch_rss("https://calmatters.org/health/feed/")

# LAO publications RSS
print("  LAO publications RSS...")
raw_items += fetch_rss("https://lao.ca.gov/rss/publications.xml")

# DHCS newsroom
print("  DHCS newsroom...")
raw_items += fetch_page_headlines(
    "https://www.dhcs.ca.gov/newsroom-office-of-communications/",
    "h2 a, h3 a, .news-item a, article a",
    base_url="https://www.dhcs.ca.gov"
)

# DHCS All-Plan Letters (check for new APLs)
print("  DHCS All-Plan Letters...")
raw_items += fetch_page_headlines(
    "https://www.dhcs.ca.gov/formsandpubs/Pages/AllPlanLetters.aspx",
    "table td a, .ms-rtestate-field a",
    base_url="https://www.dhcs.ca.gov"
)

# KFF Health News search
print("  KFF Health News RSS...")
raw_items += fetch_rss("https://kffhealthnews.org/feed/")

# Filter out items with no title
raw_items = [i for i in raw_items if i.get("title") and len(i["title"]) > 10]

print(f"  Fetched {len(raw_items)} raw items total")

if not raw_items:
    print("No items fetched — check network access from GitHub Actions runner")
    sys.exit(0)

# ── Ask Claude to filter and format ──────────────────────────────────────────

items_text = "\n".join([
    f"- [{i.get('published','')}] {i['title']} | {i['link']} | {i.get('summary','')[:200]}"
    for i in raw_items
])

if SCAN_TYPE == "daily":
    prompt = f"""You are a CalAIM policy analyst. Below is a list of headlines and summaries fetched from health policy news sources today ({date_str}).

Your job:
1. Identify items that are DIRECTLY relevant to CalAIM in California — specifically:
   - Enhanced Care Management (ECM): eligibility, populations of focus, presumptive auth, rates
   - Community Supports (CS): service definitions, MCP elections, new services
   - Medi-Cal Managed Care: APLs, contract changes, network adequacy
   - CalAIM 1115 or 1915(b) waiver renewal
   - H.R. 1 federal Medi-Cal cuts, work requirements, eligibility changes
   - California Medi-Cal legislation (AB 2161, SB 1422, budget trailer bills)
2. Discard anything not directly relevant to CalAIM or California Medi-Cal.
3. For each relevant item, write a 2-sentence plain-language summary and assign a tag.

Source items:
{items_text}

Return ONLY a valid JSON object — no explanation, no markdown, no preamble:
{{
  "found": true,
  "scan_date": "{date_str}",
  "items": [
    {{
      "tag": "tag-federal",
      "label": "Federal",
      "headline": "Exact or lightly cleaned headline",
      "url": "https://...",
      "summary": "Two-sentence plain-language summary. Why it matters for ECM/CS providers.",
      "source": "Source Name · {date_str}"
    }}
  ]
}}

If nothing is relevant, return: {{"found": false, "scan_date": "{date_str}", "items": []}}

Valid tags: tag-ecm, tag-cs, tag-mmcd, tag-waiver, tag-federal, tag-legislative, tag-budget"""

else:  # weekly
    prompt = f"""You are a CalAIM policy analyst. Below is a list of headlines and summaries fetched from health policy news sources over the past 7 days (week of {date_str}).

Your job:
1. Identify the most significant items directly relevant to CalAIM in California:
   - Enhanced Care Management (ECM)
   - Community Supports (CS)
   - Medi-Cal Managed Care (APLs, contracts, network adequacy)
   - CalAIM waiver renewal (1115 and 1915b)
   - H.R. 1 federal Medi-Cal cuts and implementation
   - California Medi-Cal legislation
2. Split them into: news_items (journalism/commentary) and policy_items (government guidance, APLs, official reports).
3. Write a 2-sentence plain-language summary for each.

Source items:
{items_text}

Return ONLY a valid JSON object:
{{
  "found": true,
  "scan_date": "{date_str}",
  "news_items": [
    {{
      "tag": "tag-federal",
      "label": "Federal",
      "headline": "Headline",
      "url": "https://...",
      "summary": "Two-sentence summary.",
      "source": "Source · {date_str}"
    }}
  ],
  "policy_items": [
    {{
      "key": "p0",
      "tag": "tag-mmcd",
      "label": "Managed Care",
      "headline": "Headline",
      "url": "https://...",
      "summary": "Two-sentence summary.",
      "source": "Source · {date_str}"
    }}
  ]
}}

If nothing relevant, return: {{"found": false, "scan_date": "{date_str}", "news_items": [], "policy_items": []}}

Valid tags: tag-ecm, tag-cs, tag-mmcd, tag-waiver, tag-federal, tag-legislative, tag-budget
For policy_items, assign sequential keys: p0, p1, p2, etc."""

print(f"Asking Claude to analyze {len(raw_items)} items...")

message = client.messages.create(
    model=MODEL,
    max_tokens=MAX_TOKENS,
    messages=[{"role": "user", "content": prompt}]
)

response_text = message.content[0].text.strip()

# Extract JSON from response (handle cases where model adds preamble)
json_match = re.search(r'\{[\s\S]*\}', response_text)
if not json_match:
    print("ERROR: Claude response did not contain valid JSON")
    print("Response:", response_text[:500])
    sys.exit(1)

try:
    result = json.loads(json_match.group())
except json.JSONDecodeError as e:
    print(f"ERROR: Failed to parse JSON from response: {e}")
    print("Response:", response_text[:500])
    sys.exit(1)

if not result.get("found"):
    print(f"No relevant CalAIM items found in {SCAN_TYPE} scan — no update needed")
    sys.exit(0)

item_count = len(result.get("items", result.get("news_items", [])))
print(f"Found {item_count} relevant items")

# ── Update data.json ──────────────────────────────────────────────────────────

with open(DATA_FILE, "r") as f:
    data = json.load(f)

if SCAN_TYPE == "daily":
    # Add items to dailyUpdates of the current version
    daily_items = result.get("items", [])
    for item in daily_items:
        item["date"] = date_str
    # Replace (not append) so re-running on the same day is idempotent
    existing_daily = [d for d in data["currentVersion"].get("dailyUpdates", [])
                      if d.get("date") != date_str]
    data["currentVersion"]["dailyUpdates"] = daily_items + existing_daily
    data["lastUpdated"] = date_str
    print(f"Added {len(daily_items)} items to dailyUpdates")

else:  # weekly
    # Archive the current version
    current = data["currentVersion"].copy()
    current["id"] = current.get("scanDate", date_id).replace(" ", "-").replace(",", "")
    current["label"] = current.get("scanDate", date_str)
    current["dailyUpdates"] = []  # don't archive daily updates

    archive = data.get("archive", [])
    # Keep last 12 weeks of archive
    archive = [current] + archive[:11]
    data["archive"] = archive

    # Create new current version
    data["currentVersion"] = {
        "id": "current",
        "label": f"{date_str} (current)",
        "scanDate": date_str,
        "dailyUpdates": [],
        "news": result.get("news_items", []),
        "policy": result.get("policy_items", [])
    }
    data["lastUpdated"] = date_str
    print(f"Archived previous version, created new current for {date_str}")

with open(DATA_FILE, "w") as f:
    json.dump(data, f, indent=2)

print(f"data.json updated successfully ({SCAN_TYPE} scan complete)")
