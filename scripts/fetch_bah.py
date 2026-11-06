#!/usr/bin/env python3
"""Download the official DoD BAH ASCII bundle and store it under data/raw/.

The BAH rate-files page is the source of record:

  https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Files-Alt

That page is DTMO's alternate download for the "all locations, all pay grades"
ZIP (ASCII, and a separate PDF/Excel bundle). The ASCII file itself is served
from the DTMO document library:

  .../BAH_Rates_All_Locations_All_Pay_Grades/ASCII/BAH-ASCII-{year}.zip

travel.dod.mil is behind a WAF that rejects non-browser clients. This script
sends a normal browser header set over HTTP/2, which is what the host accepts.
A failed download keeps the last good ZIP already in the repo.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import parse_bah  # noqa: E402

RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "data" / "manifest.json"
SOURCE_PAGE = "https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Files-Alt"
PORTAL = (
    "https://www.travel.dod.mil/Portals/119/Documents/BAH/"
    "BAH_Rates_All_Locations_All_Pay_Grades/ASCII/BAH-ASCII-{year}.zip"
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

MIN_MHAS = 300
MIN_ZIPS = 30000


def _client():
    import httpx

    try:
        return httpx.Client(timeout=60, follow_redirects=True, http2=True, headers=BROWSER_HEADERS)
    except ImportError as exc:
        raise SystemExit(
            "HTTP/2 support is missing. Install dependencies with: pip install -r requirements.txt"
        ) from exc


def _get(client, url: str):
    import httpx

    last = None
    for attempt in range(4):
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            last = exc
            continue
        if response.status_code == 200:
            return response
        if response.status_code in (403, 429) or response.status_code >= 500:
            last = response
            time.sleep(1.5 * (attempt + 1))
            continue
        return response
    return last


def _years() -> list[int]:
    today = dt.date.today().year
    # Next year first so a December release wins over the file still online.
    return [today + 1, today, today - 1]


def _urls_from_page(html: str) -> list[str]:
    """ZIP links actually published on the rate-files page, if the widget rendered them."""
    found = []
    for match in re.findall(r"https?://[^\"'\s>]+\.zip|/[Pp]ortals/[^\"'\s>]+\.zip", html):
        if "BAH" in match.upper() and "ASCII" in match.upper():
            if match.startswith("/"):
                match = "https://www.travel.dod.mil" + match
            found.append(match.split("?")[0])
    return found


def _looks_like_zip(blob: bytes) -> bool:
    return len(blob) > 1000 and blob[:2] == b"PK"


def _validate(blob: bytes) -> parse_bah.BahData:
    if not _looks_like_zip(blob):
        raise parse_bah.ParseError("download is not a ZIP")
    data = parse_bah.parse_bah_zip(blob)
    if len(data.mhas) < MIN_MHAS or len(data.zip_to_mha) < MIN_ZIPS:
        raise parse_bah.ParseError(
            f"parsed file is too small ({len(data.mhas)} MHAs, {len(data.zip_to_mha)} ZIPs)"
        )
    return data


def _iso(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parsed = email.utils.parsedate_to_datetime(header_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(blob: bytes, data: parse_bah.BahData, source_url: str, last_modified: str | None, alt_status: int | None) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    filename = f"BAH-ASCII-{data.year}.zip"
    for old in RAW.glob("BAH-ASCII-*.zip"):
        if old.name != filename:
            old.unlink()
    (RAW / filename).write_bytes(blob)
    manifest = {
        "year": data.year,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        "filename": filename,
        "source_page": SOURCE_PAGE,
        "source_url": source_url,
        "source_note": (
            "Downloaded from the DTMO document library that the BAH rate-files "
            "page uses for its ASCII 'all locations, all pay grades' ZIP. "
            "Tables use bahw (with dependents), bahwo (without dependents), "
            "sorted_zipmha, and mhanames. Members marked old are ignored."
        ),
        "alt_page_status": alt_status,
        "retrieved_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_modified": last_modified,
        "mhas": len(data.mhas),
        "zips": len(data.zip_to_mha),
        "members_used": data.members,
        "areas_changed_vs_old": data.changed_vs_old,
        "warnings": data.warnings,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"saved {filename} sha256={manifest['sha256']} "
        f"mhas={manifest['mhas']} zips={manifest['zips']} changed={data.changed_vs_old}"
    )


def _existing_ok() -> bool:
    if not MANIFEST.exists():
        return False
    try:
        manifest = json.loads(MANIFEST.read_text())
        blob = (RAW / manifest["filename"]).read_bytes()
    except (OSError, KeyError, json.JSONDecodeError):
        return False
    if hashlib.sha256(blob).hexdigest() != manifest.get("sha256"):
        return False
    try:
        _validate(blob)
    except parse_bah.ParseError:
        return False
    return True


def main() -> int:
    import httpx

    candidates: list[str] = []
    alt_status = None
    try:
        with _client() as client:
            page = _get(client, SOURCE_PAGE)
            if isinstance(page, httpx.Response):
                alt_status = page.status_code
                print(f"alt page HTTP {page.status_code} {page.url}")
                if page.status_code == 200:
                    candidates.extend(_urls_from_page(page.text))
            else:
                print(f"alt page failed: {page}", file=sys.stderr)
            for year in _years():
                candidates.append(PORTAL.format(year=year))
            # de-dupe, keep order
            seen: set[str] = set()
            ordered = []
            for url in candidates:
                if url not in seen:
                    seen.add(url)
                    ordered.append(url)
            errors = []
            best = None
            for url in ordered:
                response = _get(client, url)
                if not isinstance(response, httpx.Response):
                    errors.append(f"{url} -> {response}")
                    continue
                if response.status_code != 200:
                    errors.append(f"{url} -> HTTP {response.status_code}")
                    continue
                blob = response.content
                try:
                    data = _validate(blob)
                except parse_bah.ParseError as exc:
                    errors.append(f"{url} -> {exc}")
                    continue
                print(f"ok {url} year={data.year} bytes={len(blob)}")
                if best is None or data.year > best[0].year:
                    best = (data, blob, url, _iso(response.headers.get("last-modified")))
            if best:
                data, blob, url, modified = best
                digest = hashlib.sha256(blob).hexdigest()
                if MANIFEST.exists():
                    try:
                        current = json.loads(MANIFEST.read_text())
                    except json.JSONDecodeError:
                        current = {}
                    same_file = (RAW / current.get("filename", "")).exists()
                    if current.get("sha256") == digest and same_file:
                        print(f"rate file unchanged sha256={digest}")
                        return 0
                _write(blob, data, url, modified, alt_status)
                return 0
            print("no BAH ZIP validated", file=sys.stderr)
            for line in errors:
                print(f"  {line}", file=sys.stderr)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - keep the last good file
        print(f"fetch error: {type(exc).__name__}: {exc}", file=sys.stderr)

    if _existing_ok():
        print("keeping the BAH ZIP already in data/raw/", file=sys.stderr)
        return 0
    print("no usable BAH ZIP on disk", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
