"""
Auto-photo lookup for diplomatic staff via Wikipedia's public REST API.

No API key required. Returns a Wikipedia CDN thumbnail URL, or None if not found.
Results are cached by the caller (app.py uses @st.cache_data).
"""

import json
import ssl
import urllib.parse
import urllib.request

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "UN-Missions-Roster/1.0 (educational, github.com)"

_DIPLOMAT_KEYWORDS = frozenset([
    "ambassador", "diplomat", "representative", "minister", "politician",
    "foreign", "envoy", "consul", "permanent",
])


def _summary(title: str) -> dict | None:
    """Fetch a Wikipedia page summary dict, or None on any error / missing page."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=7) as r:
            data = json.load(r)
        if data.get("type") == "standard":
            return data
    except Exception:
        pass
    return None


def _is_relevant(page: dict, country: str) -> bool:
    text = (page.get("description", "") + " " + page.get("extract", "")).lower()
    country_word = country.split()[-1].lower()
    return bool(
        any(kw in text for kw in _DIPLOMAT_KEYWORDS) or country_word in text
    )


def _thumb(page: dict) -> str | None:
    return (page.get("thumbnail") or {}).get("source") or \
           (page.get("originalimage") or {}).get("source")


def fetch_wikipedia_photo(full_name: str, country: str) -> str | None:
    """
    Try to find a headshot photo for a diplomat on Wikipedia.
    Attempts multiple name variants, then falls back to a keyword search.
    Returns a URL string or None.
    """
    words = full_name.split()

    # Name candidates (ordered by likelihood of being a valid Wikipedia title)
    candidates: list[str] = [full_name]
    if len(words) >= 3:
        # "Roberto Ampuero Espinoza" → try "Roberto Ampuero" (first + second word)
        candidates.append(f"{words[0]} {words[1]}")
    if len(words) >= 4:
        # Extra variant: first + third (skips a particle like "de", "van", etc.)
        candidates.append(f"{words[0]} {words[2]}")

    for name in candidates:
        page = _summary(name)
        if page and _is_relevant(page, country):
            url = _thumb(page)
            if url:
                return url

    # Search fallback — slower but catches name mismatches
    query = f"{full_name} permanent representative united nations {country}"
    search_url = (
        "https://en.wikipedia.org/w/api.php"
        "?action=query&list=search&format=json&srlimit=3&srprop=snippet"
        f"&srsearch={urllib.parse.quote(query)}"
    )
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=7) as r:
            data = json.load(r)
        results = data.get("query", {}).get("search", [])
        last = words[-1].lower()
        title = next(
            (r["title"] for r in results if last in r["title"].lower()), None
        )
        if title:
            page = _summary(title)
            if page and _is_relevant(page, country):
                url = _thumb(page)
                if url:
                    return url
    except Exception:
        pass

    return None
