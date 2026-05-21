"""
UN diplomatic mission scraper.

Primary source — UN e-Blue Book JSON API (no authentication, no browser):
    https://bluebook.e-delegate.un.org/data.json
    Returns 250 missions and ~2,900 staff in one request.

Supplemental source — un.int Permanent Missions portal (Playwright, optional):
    https://www.un.int/
    Used to fill any gaps left by the Blue Book.

Probe
-----
    python scraper.py probe [url ...]   # save screenshots + HTML for debugging

Fetch test
----------
    python scraper.py fetch             # run fetch_bluebook() and print summary
"""

import asyncio
import json
import re
import ssl
import urllib.request
from pathlib import Path

import certifi

# ── Primary source ────────────────────────────────────────────────────────────

BLUEBOOK_DATA_URL = "https://bluebook.e-delegate.un.org/data.json"
BLUEBOOK_MIRROR_URL = "https://bluebook.unmeetings.org/data.json"

# Functions that identify the head of mission (match against BB_Function, lowercased + stripped)
HEAD_FUNCTIONS = frozenset([
    "permanent representative",
    "permanent observer",
    "chargé d'affaires",
    "chargé d'affaires a.i.",
    "acting permanent representative",
])

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_bluebook() -> list[dict]:
    """
    Fetch and parse the UN Blue Book JSON endpoint.
    Returns a list of mission dicts, each with a 'staff' key.
    This is the primary data source — no browser needed.
    """
    for url in (BLUEBOOK_DATA_URL, BLUEBOOK_MIRROR_URL):
        try:
            print(f"[bluebook] Fetching {url}")
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
                data = json.load(r)
            missions = _parse_bluebook_json(data)
            print(f"[bluebook] Parsed {len(missions)} missions, "
                  f"{sum(len(m['staff']) for m in missions)} staff")
            return missions
        except Exception as e:
            print(f"[bluebook] Error from {url}: {e}")

    print("[bluebook] Both URLs failed.")
    return []


def _parse_bluebook_json(data: dict) -> list[dict]:
    # Build country-keyed mission map from the 'countries' array
    missions: dict[str, dict] = {}
    for c in data.get("countries", []):
        entity = (c.get("MC_Entity") or "").strip()
        if not entity:
            continue
        address_raw = c.get("MC_Address") or ""
        missions[entity] = {
            "country": (c.get("MC_EntityBB") or entity.title()).strip(),
            "iso2": "",
            "mission_name": _first_line(address_raw),
            "address": address_raw.replace("\r\n", "\n").strip(),
            "phone": (c.get("MC_Telephone") or "").strip(),
            "fax": (c.get("MC_Telefax") or "").strip(),
            "email": (c.get("MC_eMail") or "").strip(),
            "website": (c.get("MC_WebSite") or "").strip(),
            "staff": [],
        }

    # Group active staff records by mission
    for s in data.get("bluebooks", []):
        if s.get("BB_Status") != "Active":
            continue
        entity = (s.get("BB_Mission") or "").strip()
        if not entity:
            continue

        # Create a stub for missions not in the countries list (e.g. IGOs)
        if entity not in missions:
            missions[entity] = {
                "country": (s.get("BB_Mission_bluebook") or entity.title()).strip(),
                "iso2": "",
                "mission_name": (s.get("BB_Mission_bluebook") or entity.title()).strip(),
                "address": "",
                "phone": "",
                "fax": "",
                "email": "",
                "website": "",
                "staff": [],
            }

        first = (s.get("BB_FirstName") or "").strip()
        last = (s.get("BB_LastName") or "").strip()
        full_name = f"{first} {last}".strip()
        if not full_name:
            continue

        function = (s.get("BB_Function") or "").strip()
        rank = (s.get("BB_Dipl_Rank_Display") or "").strip()
        is_head = 1 if function.lower().rstrip() in HEAD_FUNCTIONS else 0

        missions[entity]["staff"].append({
            "honorific": _normalise_honorific(s.get("BB_Title") or ""),
            "full_name": full_name,
            "title": function,
            "rank": rank,
            "is_head_of_mission": is_head,
            "accreditation_date": (
                s.get("BB_Cred_Presented") or s.get("BB_Appointment") or ""
            ).strip(),
        })

    return list(missions.values())


def _first_line(text: str) -> str:
    return text.split("\n")[0].split("\r")[0].strip()


def _normalise_honorific(raw: str) -> str:
    """Normalise BB_Title to a clean honorific string."""
    h = raw.strip()
    # Fix stray whitespace variants: "Mr. ", " Mr.", "Mr"
    h = " ".join(h.split())
    # Normalise capitalisation for all-caps variants
    if h.upper() == h and len(h) > 1:
        h = h.capitalize()
    # Ensure trailing dot on standard abbreviations missing it
    if h in ("Mr", "Ms", "Mrs", "Miss"):
        h = h.rstrip(".") + ("." if h != "Miss" else "")
    return h


# ── Merge helper ─────────────────────────────────────────────────────────────

def merge_sources(bluebook: list[dict], unint: list[dict]) -> list[dict]:
    """Merge Blue Book and un.int data; Blue Book takes precedence."""
    merged = {m["country"]: m for m in bluebook}
    for m in unint:
        country = m["country"]
        if country not in merged:
            merged[country] = m
        else:
            ex = merged[country]
            for field in ("address", "phone", "fax", "email", "website"):
                if not ex.get(field) and m.get(field):
                    ex[field] = m[field]
            if not ex.get("staff") and m.get("staff"):
                ex["staff"] = m["staff"]
    return list(merged.values())


# ── Supplemental: un.int (Playwright) ────────────────────────────────────────

UNINT_BASE = "https://www.un.int"

UNINT_HEAD_SELS = [
    ".field-name-field-permanent-representative",
    ".field-name-field-head-of-mission",
    "[class*='ambassador']",
    "[class*='representative']",
    "[class*='head-of-mission']",
]


async def scrape_un_int() -> list[dict]:
    """
    Scrape un.int Permanent Missions portal.
    Optional supplement — the Blue Book already covers all missions.
    Only useful if you need data not in the Blue Book JSON.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("[un.int] playwright not installed — skipping")
        return []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_UA)

        for path in ("/member-states", "/pm", "/"):
            page = await ctx.new_page()
            url = UNINT_BASE + path
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception:
                await page.close()
                continue

            await asyncio.sleep(2)
            mission_urls = await _collect_unint_urls(page)
            await page.close()

            if mission_urls:
                print(f"[un.int] Found {len(mission_urls)} mission links at {url}")
                missions = await _scrape_unint_pages(ctx, mission_urls)
                await browser.close()
                return missions

        print("[un.int] No mission links found.")
        await browser.close()
        return []


async def _collect_unint_urls(page) -> list[tuple[str, str]]:
    links = await page.locator("a[href]").all()
    result, seen = [], set()
    for link in links:
        href = (await link.get_attribute("href") or "").strip()
        text = (await link.text_content() or "").strip()
        if not href or len(text) < 3:
            continue
        if href.startswith("/"):
            href = UNINT_BASE + href
        if not href.startswith(UNINT_BASE):
            continue
        path = href[len(UNINT_BASE):]
        parts = [p for p in path.split("/") if p]
        if not parts or len(parts) > 2:
            continue
        if any(s in path.lower() for s in ("login", "about", "contact", "search", "faq", "news", "event", "member-states")):
            continue
        if href not in seen:
            seen.add(href)
            result.append((text, href))
    return result


async def _scrape_unint_pages(ctx, urls: list[tuple[str, str]]) -> list[dict]:
    missions = []
    for text, url in urls:
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20_000)
            await asyncio.sleep(1)
            mission = await _parse_unint_page(page)
            if mission:
                missions.append(mission)
                print(f"  [un.int] {mission['country']} — {len(mission['staff'])} staff")
            await page.close()
            await asyncio.sleep(2)
        except Exception as e:
            print(f"  [un.int] Error on {url}: {e}")
    return missions


async def _parse_unint_page(page) -> dict | None:
    country = await _first_text(page, ["h1", ".page-title"])
    if not country:
        return None

    address = await _first_text(page, [".field-name-field-address", "[class*='address']", "address"])
    phone = await _first_text(page, [".field-name-field-phone", "[class*='phone']"])

    email_el = page.locator("a[href^='mailto']").first
    email = ""
    if await email_el.count() > 0:
        email = (await email_el.get_attribute("href") or "").replace("mailto:", "").strip()

    website = ""
    ext = page.locator(f"a[href^='http']:not([href*='un.int'])").first
    if await ext.count() > 0:
        website = (await ext.get_attribute("href") or "").strip()

    staff = []
    for sel in UNINT_HEAD_SELS:
        el = page.locator(sel).first
        if await el.count() > 0:
            name = (await el.text_content() or "").strip()
            if name:
                staff.append({
                    "full_name": name,
                    "title": "Permanent Representative",
                    "rank": "Ambassador",
                    "is_head_of_mission": 1,
                    "accreditation_date": "",
                })
                break

    return {
        "country": country,
        "iso2": "",
        "mission_name": f"Permanent Mission of {country}",
        "address": address,
        "phone": phone,
        "fax": "",
        "email": email,
        "website": website or page.url,
        "staff": staff,
    }


async def _first_text(page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                text = (await el.text_content() or "").strip()
                if text:
                    return text
        except Exception:
            pass
    return ""


# ── Probe helper ──────────────────────────────────────────────────────────────

async def probe(urls: list[str] | None = None, out_dir: str = "probe_output") -> None:
    """Save a screenshot + HTML for each URL. Useful for selector debugging."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright not installed — run: pip install playwright && playwright install chromium")
        return

    if not urls:
        urls = [BLUEBOOK_DATA_URL, UNINT_BASE + "/"]

    Path(out_dir).mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for url in urls:
            slug = re.sub(r"[^a-zA-Z0-9]", "_", url)[:60]
            page = await browser.new_page(user_agent=_UA)
            print(f"[probe] {url}")
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                await asyncio.sleep(3)
            except Exception:
                pass
            await page.screenshot(path=f"{out_dir}/{slug}.png", full_page=True)
            Path(f"{out_dir}/{slug}.html").write_text(await page.content(), encoding="utf-8")
            print(f"  → {out_dir}/{slug}.{{png,html}}")
            await page.close()
        await browser.close()


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    cmd = args[0] if args else "fetch"

    if cmd == "probe":
        asyncio.run(probe(args[1:] or None))
    elif cmd == "fetch":
        missions = fetch_bluebook()
        total_staff = sum(len(m["staff"]) for m in missions)
        print(f"\nResult: {len(missions)} missions, {total_staff} staff")
        if missions:
            sample = next((m for m in missions if m["staff"]), missions[0])
            print(f"Sample mission: {sample['country']}")
            print(f"  Address: {sample['address'][:80]}")
            if sample["staff"]:
                s = sample["staff"][0]
                print(f"  Staff[0]: {s['full_name']} — {s['title']} ({s['rank']})")
    else:
        print("Usage: python scraper.py [fetch|probe] [url...]")
