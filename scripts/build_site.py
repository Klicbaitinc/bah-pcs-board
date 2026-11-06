#!/usr/bin/env python3
"""Build the static BAH & PCS site from the DoD ASCII ZIP named in data/manifest.json.

Env (blank values fall back to the GitHub project-site defaults):
  SITE_URL   origin for sitemap and canonical tags.
  BASE_PATH  URL path prefix. Default /bah-pcs-board. Use / for a user site.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import parse_bah  # noqa: E402

OUT = ROOT / "site"
MANIFEST_PATH = ROOT / "data" / "manifest.json"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def env_or(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


_BLANK_LINES = re.compile(r"\n[ \t]*\n+")


def tighten(html: str) -> str:
    return _BLANK_LINES.sub("\n", html).strip() + "\n"


def money(value: int) -> str:
    return f"${value:,.0f}"


def stamp(value: str) -> str:
    if not value:
        return ""
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def offer_href(url: str, placement: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"utm_source", "utm_medium", "utm_content"}]
    query.append(("utm_source", "bahpcsboard"))
    query.append(("utm_medium", "affiliate"))
    query.append(("utm_content", placement))
    return urlunsplit(parts._replace(query=urlencode(query)))


def rate_rows(data: parse_bah.BahData, mha: parse_bah.Mha) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for grade in data.grades:
        group = parse_bah.grade_group(grade)
        with_month = mha.with_rates[grade]
        without_month = mha.without_rates[grade]
        rows.append({
            "grade": grade,
            "label": parse_bah.grade_label(grade, senior_collapsed=data.senior_collapsed),
            "group": group,
            "first": group not in seen,
            "with_month": with_month,
            "without_month": without_month,
            "with_year": with_month * 12,
            "without_year": without_month * 12,
        })
        seen.add(group)
    return rows


def alias_labels(mha: parse_bah.Mha) -> list[str]:
    if mha.kind != "named" or "/" not in mha.name.split(",")[0]:
        return []
    place, _, rest = mha.name.partition(",")
    state = rest.strip()
    labels = []
    for part in place.split("/"):
        part = part.strip()
        if not part:
            continue
        label = f"{part}, {state}" if state else part
        if label != mha.name:
            labels.append(label)
    return labels


def groups_for(mhas: list[parse_bah.Mha]) -> list[dict]:
    named: dict[str, list] = {}
    county = []
    for mha in mhas:
        if mha.kind == "county":
            county.append(mha)
        else:
            named.setdefault(mha.state, []).append(mha)
    groups = []
    for state in sorted(named, key=lambda code: STATE_NAMES.get(code, code)):
        groups.append({
            "title": STATE_NAMES.get(state, state),
            "mhas": sorted(named[state], key=lambda m: m.name),
        })
    if county:
        groups.append({
            "title": "County cost groups",
            "mhas": sorted(county, key=lambda m: m.code),
        })
    return groups


def load_config():
    site = json.loads((ROOT / "config" / "site.json").read_text())
    affiliates = json.loads((ROOT / "config" / "affiliates.json").read_text())
    required = ["name", "owner", "contact", "default_site_url", "default_base_path", "newsletter", "source_page", "lookup_page", "bah_page", "fmr_chapter", "onesource", "mypay"]
    missing = [key for key in required if key not in site]
    if missing:
        raise SystemExit(f"config/site.json is missing {', '.join(missing)}")
    items = affiliates.get("items") or []
    if len(items) < 3:
        raise SystemExit("config/affiliates.json needs the Penske, U-Haul, and Raisin placeholders")
    ids = {item.get("id") for item in items}
    if {"penske", "uhaul", "raisin"} - ids:
        raise SystemExit("config/affiliates.json must include penske, uhaul, and raisin")
    for item in items:
        for key in ("id", "name", "network", "kicker", "headline", "body", "button", "url", "disclosure"):
            if key not in item:
                raise SystemExit(f"affiliate {item.get('id')} is missing {key}")
    return site, items


def main() -> None:
    if not MANIFEST_PATH.exists():
        raise SystemExit("data/manifest.json is missing. Run scripts/fetch_bah.py first.")
    manifest = json.loads(MANIFEST_PATH.read_text())
    blob = (ROOT / "data" / "raw" / manifest["filename"]).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != manifest["sha256"]:
        raise SystemExit("data/manifest.json sha256 does not match the ZIP on disk")
    data = parse_bah.parse_bah_zip(blob)
    site, affiliate_items = load_config()

    site_url = env_or("SITE_URL", site["default_site_url"]).rstrip("/")
    base_path = env_or("BASE_PATH", site["default_base_path"])
    if base_path == "/":
        base = ""
    else:
        base = base_path.rstrip("/")
        if base and not base.startswith("/"):
            base = "/" + base
    public = site_url + base

    rows_cache = {code: rate_rows(data, mha) for code, mha in data.mhas.items()}

    primary_slugs = {mha.slug for mha in data.mhas.values()}
    aliases: dict[str, list[dict]] = {code: [] for code in data.mhas}
    alias_pages = []
    used = set(primary_slugs)
    for mha in data.mhas.values():
        for label in alias_labels(mha):
            slug = parse_bah.slugify(label)
            if not slug or slug in used:
                continue
            used.add(slug)
            entry = {"slug": slug, "label": label, "code": mha.code}
            aliases[mha.code].append(entry)
            alias_pages.append(entry)
    for mha in data.mhas.values():
        mha.alias_text = ", ".join(item["label"] for item in aliases[mha.code])

    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["money"] = money
    env.filters["comma"] = lambda n: f"{int(n):,}"
    env.globals["rate_rows"] = lambda mha: rows_cache[mha.code]
    env.globals["offer_href"] = offer_href

    file_date = stamp(manifest.get("last_modified") or manifest["retrieved_at"])
    retrieved = manifest["retrieved_at"][:16].replace("T", " ")
    common = dict(
        base=base,
        site_url=site_url,
        public_url=public,
        site_name=site["name"],
        owner=site["owner"],
        contact=site["contact"],
        year=dt.date.today().year,
        rate_year=data.year,
        sha256=digest,
        sha_short=digest[:12],
        file_date=file_date,
        retrieved=retrieved,
        source_page=manifest["source_page"],
        source_url=manifest["source_url"],
        members=data.members,
        senior_collapsed=data.senior_collapsed,
        links=site,
        affiliate_items=affiliate_items,
        newsletter=site["newsletter"],
        manifest_filename=manifest["filename"],
        robots="index, follow",
    )

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "static", OUT / "static")

    pages: list[tuple[str, str]] = []
    lastmod = (manifest.get("last_modified") or manifest["retrieved_at"])[:10]
    today = dt.date.today().isoformat()

    def write(path: str, template: str, **kwargs) -> None:
        ctx = dict(common)
        ctx.update(kwargs)
        html = tighten(env.get_template(template).render(**ctx))
        dest = OUT / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html)

    named = [m for m in data.mhas.values() if m.kind == "named"]
    county = [m for m in data.mhas.values() if m.kind == "county"]
    changed = [data.mhas[code] for code in data.changed_vs_old if code in data.mhas]
    n_unrated = sum(1 for code in data.zip_to_mha.values() if code not in data.mhas)
    n_rated = len(data.zip_to_mha) - n_unrated

    write("index.html", "index.html",
          page_title=f"{site['name']}: BAH by ZIP, MHA, and duty station",
          description=f"Look up {data.year} Basic Allowance for Housing by duty ZIP, military housing area, and pay grade, with and without dependents. A PCS cost checklist. Not a DoD site. No ratings.",
          canonical="/",
          n_named=len(named), n_county=len(county), n_zips=len(data.zip_to_mha), changed=changed)
    pages.append(("/", today))

    mha_groups = groups_for(list(data.mhas.values()))
    write("mha/index.html", "mha_index.html",
          page_title=f"BAH housing areas, {data.year} | {site['name']}",
          description=f"All {len(data.mhas)} military housing areas and county cost groups in the {data.year} DoD BAH file.",
          canonical="/mha/", groups=mha_groups)
    pages.append(("/mha/", today))

    write("duty-station/index.html", "duty_index.html",
          page_title=f"Duty station BAH, {data.year} | {site['name']}",
          description="BAH for each named duty-station housing area and for county cost groups. Not a rating of any installation.",
          canonical="/duty-station/", groups=mha_groups)
    pages.append(("/duty-station/", today))

    write("zip/index.html", "zip_index.html",
          page_title=f"BAH by ZIP code, {data.year} | {site['name']}",
          description=f"A page for every ZIP in the {data.year} DoD BAH crosswalk, with and without dependents.",
          canonical="/zip/", n_zips=len(data.zip_to_mha), n_rated=n_rated, n_unrated=n_unrated)
    pages.append(("/zip/", today))

    write("pcs/index.html", "pcs.html",
          page_title=f"PCS move cost checklist | {site['name']}",
          description="A stateside PCS checklist of move costs to price. Not a quote, not mover ratings, and not investment advice.",
          canonical="/pcs/")
    pages.append(("/pcs/", today))

    write("about/index.html", "about.html",
          page_title=f"About and sources | {site['name']}",
          description="How the BAH and PCS board reads the official DoD rate file, and what it does not do.",
          canonical="/about/", changed=changed, n_mhas=len(data.mhas), n_zips=len(data.zip_to_mha))
    pages.append(("/about/", today))

    write("disclosure/index.html", "disclosure.html",
          page_title=f"Affiliate disclosure | {site['name']}",
          description="Penske and U-Haul via FlexOffers, and Raisin savings, are affiliate placeholders. Rates are not for sale. Not investment advice.",
          canonical="/disclosure/")
    pages.append(("/disclosure/", today))

    write("privacy/index.html", "privacy.html",
          page_title=f"Privacy | {site['name']}",
          description="This static site has no accounts, no analytics, and no email collection.",
          canonical="/privacy/")
    pages.append(("/privacy/", today))

    write("404.html", "404.html",
          page_title=f"Page not found | {site['name']}",
          description="That page is not on the BAH and PCS board.",
          canonical="/404.html")

    for mha in data.mhas.values():
        code = mha.code.lower()
        write(f"mha/{code}/index.html", "mha.html",
              page_title=f"{data.year} BAH for {mha.name} ({mha.code}) | {site['name']}",
              description=f"Monthly {data.year} BAH for {mha.name}, MHA {mha.code}, with and without dependents, by pay grade.",
              canonical=f"/mha/{code}/", mha=mha)
        pages.append((f"/mha/{code}/", lastmod))
        heading = mha.name
        write(f"duty-station/{mha.slug}/index.html", "duty.html",
              page_title=f"Duty station BAH: {heading} | {site['name']}",
              description=f"BAH at {heading} ({mha.code}) for a PCS. Published {data.year} rates with and without dependents. Not a rating.",
              canonical=f"/duty-station/{mha.slug}/", mha=mha, heading=heading, alias_of=None, aliases=aliases[mha.code])
        pages.append((f"/duty-station/{mha.slug}/", lastmod))

    for alias in alias_pages:
        mha = data.mhas[alias["code"]]
        primary = f"/duty-station/{mha.slug}/"
        write(f"duty-station/{alias['slug']}/index.html", "duty.html",
              page_title=f"Duty station BAH: {alias['label']} | {site['name']}",
              description=f"{alias['label']} is in the {mha.name} housing area ({mha.code}). {data.year} BAH with and without dependents.",
              canonical=primary, mha=mha, heading=alias["label"], alias_of=mha, aliases=[],
              robots="noindex, follow")
        # Alias pages point their canonical tag at the primary duty-station page.
        # They stay out of the sitemap so the primary URL is the one we ask crawlers to use.

    unknown_name = "UNKNOWN, NA"
    rated_zips = 0
    for zip_code, mha_code in data.zip_to_mha.items():
        mha = data.mhas.get(mha_code)
        if mha is not None:
            write(f"zip/{zip_code}/index.html", "zip.html",
                  page_title=f"BAH for ZIP {zip_code}, {mha.name} | {site['name']}",
                  description=f"{data.year} BAH for duty ZIP {zip_code} in {mha.name} ({mha.code}), with and without dependents, by pay grade.",
                  canonical=f"/zip/{zip_code}/", zip_code=zip_code, mha=mha)
            rated_zips += 1
        else:
            write(f"zip/{zip_code}/index.html", "zip_unrated.html",
                  page_title=f"No BAH rate for ZIP {zip_code} | {site['name']}",
                  description=f"DoD maps ZIP {zip_code} to {mha_code}, which has no BAH rate row in the {data.year} file.",
                  canonical=f"/zip/{zip_code}/", zip_code=zip_code, mha_code=mha_code,
                  raw_name=unknown_name if mha_code == "XX499" else "")
        pages.append((f"/zip/{zip_code}/", lastmod))
        if rated_zips and rated_zips % 5000 == 0 and mha is not None:
            print(f"  {rated_zips} rated ZIP pages", flush=True)

    areas = []
    for mha in data.mhas.values():
        areas.append({"c": mha.code, "n": mha.name, "s": mha.slug, "k": mha.kind})
    for alias in alias_pages:
        mha = data.mhas[alias["code"]]
        areas.append({"c": mha.code, "n": alias["label"], "s": alias["slug"], "k": "alias"})
    search = {
        "areas": areas,
        "zips": [[zip_code, mha_code] for zip_code, mha_code in sorted(data.zip_to_mha.items())],
    }
    (OUT / "static" / "search.json").write_text(json.dumps(search, separators=(",", ":")))

    write("sitemap.xml", "sitemap.xml",
          urls=[(public + path, day) for path, day in pages])
    # sitemap template doesn't extend base; write() still passes common. Fine.
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {public}/sitemap.xml\n")
    (OUT / ".nojekyll").write_text("")
    print(
        f"Built {len(pages)} sitemap URLs, {len(data.mhas)} areas, "
        f"{len(data.zip_to_mha)} ZIP pages, {len(alias_pages)} aliases -> {OUT}"
    )


if __name__ == "__main__":
    main()
