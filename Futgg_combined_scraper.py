#!/usr/bin/env python3
"""
Combined FUT.GG Objectives + Evolutions Scraper - LOCAL CONTINUOUS VERSION
Runs both scrapes back-to-back, uploads each to the dashboard via
/api/upload_objectives and /api/upload_evolutions, then sleeps for
CYCLE_SECONDS (12 hours) before repeating. Ctrl+C to stop.

No local database writes - PythonAnywhere's objectives.db/evolutions.db
(read directly by dashboard_server.py) are the single source of truth.
"""

import time
import re
import os
import sys
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

# ========== CONFIGURATION ==========
UPLOAD_URL_BASE = os.environ.get('PA_UPLOAD_URL_BASE', 'https://AusYeahNah.pythonanywhere.com')
API_KEY = os.environ.get('PA_API_KEY')

OBJECTIVES_URL = "https://www.fut.gg/objectives/"
EVOLUTIONS_URL = "https://www.fut.gg/evolutions/"

OBJECTIVES_PROFILE_DIR = Path(__file__).parent / "objectives_profile"
EVOLUTIONS_PROFILE_DIR = Path(__file__).parent / "evolutions_profile"

CATEGORY_MAP = {
    "Evolutions (26)": "Standard Evolutions",
    "Rewards (56)": "Reward Evolutions",
    "PlayStyles Lab (72)": "Playstyle Evolutions",
    "Roles++ (12)": "Role Evolutions"
}


# ========== SHARED HELPERS ==========
def parse_expiry_days(expiry_text):
    if not expiry_text:
        return None
    match = re.search(r'(\d+)\s*days?', expiry_text, re.IGNORECASE)
    if match:
        days = int(match.group(1))
        return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    return None

def parse_expiry_days_or_months(expiry_text):
    if not expiry_text:
        return None
    text = expiry_text.lower().replace('in ', '').strip()
    match = re.search(r'(\d+)\s*days?', text)
    if match:
        return (datetime.now() + timedelta(days=int(match.group(1)))).strftime('%Y-%m-%d')
    match = re.search(r'(\d+)\s*months?', text)
    if match:
        return (datetime.now() + timedelta(days=int(match.group(1)) * 30)).strftime('%Y-%m-%d')
    return None

def upload(endpoint, payload):
    if not payload:
        print(f"  Nothing to upload to {endpoint}.")
        return
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    url = f"{UPLOAD_URL_BASE}{endpoint}"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Upload to {endpoint} successful: "
                  f"{data.get('accepted', 0)} accepted, {data.get('rejected', 0)} rejected")
        else:
            print(f"  Upload to {endpoint} failed: HTTP {resp.status_code}")
            print(f"  {resp.text}")
    except Exception as e:
        print(f"  Upload error on {endpoint}: {e}")


# ========== OBJECTIVES SCRAPE ==========
def scrape_objective_detail(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    desc_el = page.query_selector("p.text-sm.text-gray-300")
    description = desc_el.inner_text().strip() if desc_el else ""

    challenges = []
    challenge_containers = page.query_selector_all("div.bg-gray-800.rounded-lg.p-1.grid")
    for cont in challenge_containers:
        name_el = cont.query_selector("h4.font-bold")
        if not name_el:
            continue
        name = name_el.inner_text().strip()
        req_el = cont.query_selector("p.text-sm.text-gray-300")
        requirement = req_el.inner_text().strip() if req_el else ""
        reward_el = cont.query_selector("span.text-xs.font-bold.text-gray-300")
        reward = reward_el.inner_text().strip() if reward_el else ""
        challenges.append({"name": name, "requirement": requirement, "reward": reward})

    all_rewards = []
    reward_items = page.query_selector_all(".bg-gray-900.rounded.px-2.py-2 span.text-xs.font-bold")
    for item in reward_items:
        all_rewards.append(item.inner_text().strip())

    return {"description": description, "challenges": challenges, "rewards": all_rewards}

def scrape_objectives():
    print("\n" + "=" * 60)
    print("OBJECTIVES SCRAPE")
    print("=" * 60)
    OBJECTIVES_PROFILE_DIR.mkdir(exist_ok=True)
    results = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(OBJECTIVES_PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = context.new_page()
        print("Loading FUT.GG objectives...")
        page.goto(OBJECTIVES_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        last_height = 0
        scroll_attempts = 0
        while scroll_attempts < 3:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                scroll_attempts += 1
            else:
                scroll_attempts = 0
            last_height = new_height

        cards = page.query_selector_all("div.bg-gray-800.rounded-lg.p-1.grid")
        print(f"Found {len(cards)} objective cards")

        for card in cards:
            title_el = card.query_selector("h3.font-bold")
            title = title_el.inner_text().strip() if title_el else ""
            if not title:
                continue

            reward_el = card.query_selector("span.text-xs.font-bold.text-gray-300")
            reward_summary = reward_el.inner_text().strip() if reward_el else ""

            expiry_el = card.query_selector("span.text-sm.font-bold.text-gray-300")
            expiry_text = expiry_el.inner_text().strip() if expiry_el else ""
            expiry_date = parse_expiry_days(expiry_text)

            link = card.query_selector("a")
            detail_url = ""
            if link:
                href = link.get_attribute("href")
                if href:
                    detail_url = href if href.startswith("http") else "https://www.fut.gg" + href
            if not detail_url:
                print(f"Skipping {title} - no detail URL")
                continue

            print(f"Processing: {title}")
            detail_page = context.new_page()
            try:
                full_data = scrape_objective_detail(detail_page, detail_url)
                results.append({
                    "title": title,
                    "description": full_data.get("description", ""),
                    "reward_summary": reward_summary,
                    "expiry_date": expiry_date,
                    "detail_url": detail_url,
                    "full_data": full_data
                })
                print(f"  Got {title} with {len(full_data['challenges'])} challenges")
            except Exception as e:
                print(f"  Error scraping {title}: {e}")
            finally:
                detail_page.close()
            time.sleep(2)

        context.close()

    print(f"Objectives scrape complete: {len(results)} objectives")
    return results


# ========== EVOLUTIONS SCRAPE ==========
def scrape_evolution_detail(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    requirements = {}
    req_container = page.query_selector("div:has(> div > h2:has-text('Player Requirements'))")
    if req_container:
        rows = req_container.query_selector_all(".flex.justify-between.items-center")
        for row in rows:
            key_el = row.query_selector(".text-gray-400")
            val_el = row.query_selector(".text-gray-300")
            if key_el and val_el:
                requirements[key_el.inner_text().strip()] = val_el.inner_text().strip()

    details = {}
    details_container = page.query_selector("div:has(> div > h2:has-text('Details'))")
    if details_container:
        rows = details_container.query_selector_all(".flex.justify-between.items-center")
        for row in rows:
            key_el = row.query_selector(".text-gray-400")
            val_el = row.query_selector(".text-gray-300, .text-gray-400:last-child")
            if key_el and val_el:
                details[key_el.inner_text().strip()] = val_el.inner_text().strip()

    total_upgrades = []
    upgrades_container = page.query_selector("div:has(> div > h2:has-text('Evolution Upgrades'))")
    if upgrades_container:
        upgrade_items = upgrades_container.query_selector_all(".bg-gray.p-1.px-2.rounded.font-bold.text-xs")
        for item in upgrade_items:
            total_upgrades.append(item.inner_text().strip())

    levels = []
    level_blocks = page.query_selector_all("div.grid.gap-2.bg-gray-800.rounded-md.border.border-gray.p-1")
    for block in level_blocks:
        level_title = block.query_selector("h2.font-bold")
        if not level_title or "Level" not in level_title.inner_text():
            continue
        level_num = level_title.inner_text().strip().replace("Level", "").strip()
        upgrades_level = []
        upg_section = block.query_selector("div.bg-gray-850.rounded-md.p-3.flex.flex-col.gap-2:first-child")
        if upg_section:
            upg_items = upg_section.query_selector_all(".bg-gray.p-1.px-2.rounded.font-bold.text-xs")
            for item in upg_items:
                upgrades_level.append(item.inner_text().strip())
        challenge = ""
        challenge_section = block.query_selector("div.bg-gray-850.rounded-md.p-3.flex.flex-col.gap-2:last-child")
        if challenge_section:
            challenge_el = challenge_section.query_selector(".text-sm.text-gray-300")
            if challenge_el:
                challenge = challenge_el.inner_text().strip()
        levels.append({"level": level_num, "upgrades": upgrades_level, "challenge": challenge})

    return {"requirements": requirements, "details": details,
            "total_upgrades": total_upgrades, "levels": levels}

def scrape_evolutions():
    print("\n" + "=" * 60)
    print("EVOLUTIONS SCRAPE")
    print("=" * 60)
    EVOLUTIONS_PROFILE_DIR.mkdir(exist_ok=True)
    results = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(EVOLUTIONS_PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = context.new_page()
        print("Loading FUT.GG evolutions...")
        page.goto(EVOLUTIONS_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        try:
            accept = page.query_selector("button:has-text('Accept'), button:has-text('OK')")
            if accept:
                accept.click()
                time.sleep(1)
        except Exception:
            pass

        tab_buttons = page.query_selector_all(".flex.items-center.gap-2.mb-4.overflow-x-auto button")
        if not tab_buttons:
            print("Tabs not found. Skipping evolutions this cycle.")
            context.close()
            return results

        for btn in tab_buttons:
            tab_text = btn.inner_text().strip()
            category = CATEGORY_MAP.get(tab_text, "Other")
            print(f"\nProcessing {category} ({tab_text})")
            btn.click()
            time.sleep(3)

            last_height = 0
            while True:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            cards = page.query_selector_all("div.bg-gray-800.rounded.p-2.group.grid")
            print(f"Found {len(cards)} cards")

            for card in cards:
                title_el = card.query_selector("h2.font-bold")
                title = title_el.inner_text().strip() if title_el else ""
                if not title:
                    continue

                link = card.query_selector("a")
                detail_url = link.get_attribute("href") if link else ""
                if detail_url and not detail_url.startswith("http"):
                    detail_url = "https://www.fut.gg" + detail_url
                if not detail_url:
                    continue

                expiry_el = card.query_selector(".text-gray-300.text-sm.flex.items-center.gap-1.xl\\:gap-2.font-bold")
                if not expiry_el:
                    expiry_el = card.query_selector(".flex.flex-row.items-center.justify-center.gap-4 .text-gray-300.text-sm")
                expiry_text = expiry_el.inner_text().strip() if expiry_el else ""
                expiry_date = parse_expiry_days_or_months(expiry_text)

                print(f"  Scraping details for {title}...")
                detail_page = context.new_page()
                try:
                    full_data = scrape_evolution_detail(detail_page, detail_url)
                except Exception as e:
                    print(f"    Error scraping details: {e}")
                    full_data = {}
                finally:
                    detail_page.close()
                time.sleep(1)

                results.append({
                    "title": title,
                    "category": category,
                    "expiry_date": expiry_date,
                    "detail_url": detail_url,
                    "full_data": full_data
                })
                print(f"  Got: {title}")

        context.close()

    print(f"Evolutions scrape complete: {len(results)} evolutions")
    return results


# ========== MAIN (single run - exits when done, no internal loop) ==========
def main():
    if not API_KEY:
        print("ERROR: PA_API_KEY environment variable not set.")
        print("  Windows (cmd):        set PA_API_KEY=your-key-here")
        print("  Windows (PowerShell): $env:PA_API_KEY=\"your-key-here\"")
        sys.exit(1)

    print("=" * 60)
    print("FUT.GG Objectives + Evolutions Scraper (single run)")
    print(f"Uploading to: {UPLOAD_URL_BASE}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    start = time.monotonic()

    try:
        objectives = scrape_objectives()
        upload("/api/upload_objectives", objectives)
    except Exception as e:
        print(f"Objectives run failed: {e}")

    try:
        evolutions = scrape_evolutions()
        upload("/api/upload_evolutions", evolutions)
    except Exception as e:
        print(f"Evolutions run failed: {e}")

    duration = time.monotonic() - start
    print(f"\nRun complete in {duration/60:.1f} min. Exiting.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")