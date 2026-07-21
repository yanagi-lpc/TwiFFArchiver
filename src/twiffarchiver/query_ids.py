from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Iterable

import httpx

from twiffarchiver.auth import config_dir

BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Fallback IDs — X rotates these; discovery refreshes when possible.
DEFAULT_QUERY_IDS: dict[str, str] = {
    "UserByScreenName": "xc8f1g7BYqr6VTzTbvNlGw",
    "Following": "BEkNpEt5pNETESoqMsTEGA",
    "Followers": "kuFUYP9eV1FPoEy4N-pi7w",
}

DISCOVERY_OPERATIONS = tuple(DEFAULT_QUERY_IDS.keys())

DISCOVERY_PAGES = (
    "https://x.com/?lang=en",
    "https://x.com/explore",
)

BUNDLE_URL_RE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web(?:-legacy)?/"
    r"[A-Za-z0-9._-]+\.js"
)

QUERY_ID_PATTERNS: tuple[tuple[re.Pattern[str], int, int], ...] = (
    (
        re.compile(
            r'queryId\s*:\s*["\']([^"\']+)["\']\s*,\s*operationName\s*:\s*["\']([^"\']+)["\']'
        ),
        2,
        1,
    ),
    (
        re.compile(
            r'operationName\s*:\s*["\']([^"\']+)["\']\s*,\s*queryId\s*:\s*["\']([^"\']+)["\']'
        ),
        1,
        2,
    ),
    (
        re.compile(
            r'queryId:"([A-Za-z0-9_-]+)",operationName:"([A-Za-z0-9_]+)"'
        ),
        2,
        1,
    ),
    (
        re.compile(
            r'operationName:"([A-Za-z0-9_]+)",operationType:"query",metadata:\{[^}]*\},queryId:"([A-Za-z0-9_-]+)"'
        ),
        1,
        2,
    ),
)

CACHE_TTL_SECONDS = 24 * 60 * 60
VALID_QUERY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def query_ids_cache_path() -> Path:
    return config_dir() / "query-ids-cache.json"


def _load_cache(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = raw.get("fetched_at")
    ids = raw.get("ids")
    if not isinstance(fetched_at, (int, float)) or not isinstance(ids, dict):
        return None
    if time.time() - float(fetched_at) > CACHE_TTL_SECONDS:
        return None
    clean = {
        str(k): str(v)
        for k, v in ids.items()
        if isinstance(v, str) and VALID_QUERY_ID.match(v)
    }
    return clean or None


def _save_cache(path: Path, ids: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "ids": ids}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _extract_from_js(js: str, wanted: set[str], found: dict[str, str]) -> None:
    for pattern, op_group, qid_group in QUERY_ID_PATTERNS:
        for match in pattern.finditer(js):
            op = match.group(op_group)
            qid = match.group(qid_group)
            if op in wanted and VALID_QUERY_ID.match(qid) and op not in found:
                found[op] = qid
                if len(found) >= len(wanted):
                    return


def discover_query_ids(
    client: httpx.Client | None = None,
    *,
    force: bool = False,
) -> dict[str, str]:
    """Return operationName -> queryId, preferring fresh discovery."""
    cache_path = query_ids_cache_path()
    if not force:
        cached = _load_cache(cache_path)
        if cached and all(op in cached for op in DISCOVERY_OPERATIONS):
            return {**DEFAULT_QUERY_IDS, **cached}

    own_client = client is None
    http = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=30.0,
        follow_redirects=True,
    )
    found: dict[str, str] = {}
    wanted = set(DISCOVERY_OPERATIONS)
    try:
        bundles: list[str] = []
        for page in DISCOVERY_PAGES:
            try:
                html = http.get(page).text
            except httpx.HTTPError:
                continue
            bundles.extend(BUNDLE_URL_RE.findall(html))
        # Prefer unique, keep order
        seen: set[str] = set()
        unique_bundles: list[str] = []
        for url in bundles:
            if url not in seen:
                seen.add(url)
                unique_bundles.append(url)

        for url in unique_bundles:
            if len(found) >= len(wanted):
                break
            try:
                js = http.get(url).text
            except httpx.HTTPError:
                continue
            _extract_from_js(js, wanted, found)
    finally:
        if own_client:
            http.close()

    merged = {**DEFAULT_QUERY_IDS, **found}
    if found:
        _save_cache(cache_path, found)
    return merged


def resolve_query_id(operation: str, ids: dict[str, str] | None = None) -> str:
    mapping = ids or discover_query_ids()
    qid = mapping.get(operation) or DEFAULT_QUERY_IDS.get(operation)
    if not qid:
        raise KeyError(f"Unknown GraphQL operation: {operation}")
    return qid


def iter_fallback_ids(operation: str, primary: str) -> Iterable[str]:
    """Yield primary then other known defaults / cache values."""
    seen: set[str] = set()
    for qid in (primary, DEFAULT_QUERY_IDS.get(operation, "")):
        if qid and qid not in seen:
            seen.add(qid)
            yield qid
    # Extra known historical IDs for UserByScreenName
    extras = {
        "UserByScreenName": (
            "qW5u-DAuXpMEG0zA1F7UGQ",
            "sLVLhk0bGj3MVFEKTdax1w",
            "IGgvgiOx4QZndDHuD3x9TQ",
        ),
        "Following": (
            "AmvGuD6OFYv5HHXQ4HTt_A",
            "UCFedrkjMz7PeEAWCWhqFw",
        ),
        "Followers": (
            "pd8Tt5J25-W0GHNdrOZVaA",
            "FpGYzBsUxUOecYYfso0yA",
        ),
    }
    for qid in extras.get(operation, ()):
        if qid not in seen:
            seen.add(qid)
            yield qid
