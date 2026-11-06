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


def dataset_ld(mha: parse_bah.Mha, page_url: str, name: str, year: int, source_url: str, bah_page: str, owner: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "description": (
            f"Monthly {year} Basic Allowance for Housing for {mha.name} "
            f"({mha.code}), with and without dependents, by pay grade. "
            "Copied from the official DoD rate file."
        ),
        "url": page_url,
        "temporalCoverage": f"{year}-01-01/{year}-12-31",
        "keywords": ["BAH", "Basic Allowance for Housing", mha.name, mha.code, str(year)],
        "creator": {
            "@type": "GovernmentOrganization",
            "name": "Defense Travel Management Office",
            "url": bah_page,
        },
        "publisher": {"@type": "Person", "name": owner},
        "isBasedOn": source_url,
        "license": "https://www.usa.gov/government-works",
    }


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


# Alternate names that should redirect to the duty-station page for the 2026 MHA.
# Targets are the slugs generated from the official area names in the rate file.
SEO_ALIASES = (
    ("fort-liberty-nc", "Fort Liberty, NC", "fort-bragg-pope-nc",
     "Fort Liberty (formerly Fort Bragg). The installation used that name from 2023 to 2025. The 2026 DoD file lists this housing area as Fort Bragg/Pope, NC."),
    ("fort-cavazos-tx", "Fort Cavazos, TX", "fort-hood-tx",
     "Fort Cavazos (formerly Fort Hood). The installation used that name from 2023 to 2025. The 2026 DoD file lists this housing area as Fort Hood, TX."),
    ("joint-base-lewis-mcchord-wa", "Joint Base Lewis-McChord, WA", "tacoma-wa",
     "Joint Base Lewis-McChord (JBLM), including the former Fort Lewis, is in the Tacoma, WA housing area."),
    ("jblm-wa", "JBLM, WA", "tacoma-wa",
     "JBLM is Joint Base Lewis-McChord, in the Tacoma, WA housing area."),
    ("fort-lewis-wa", "Fort Lewis, WA", "tacoma-wa",
     "Fort Lewis is now part of Joint Base Lewis-McChord, in the Tacoma, WA housing area."),
    ("fort-bliss-tx", "Fort Bliss, TX", "el-paso-tx",
     "Fort Bliss is in the El Paso, TX housing area."),
    ("fort-belvoir-va", "Fort Belvoir, VA", "washington-dc-metro-area",
     "Fort Belvoir's duty ZIP is in the Washington, DC Metro Area housing area. The 2026 file does not have a separate Fort Belvoir row."),
)

PRIMARY_NOTES = {
    "fort-bragg-pope-nc": "Fort Bragg (formerly Fort Liberty). The installation was named Fort Liberty from 2023 to 2025. The 2026 rate file uses Fort Bragg/Pope, NC.",
    "fort-hood-tx": "Fort Hood (formerly Fort Cavazos). The installation was named Fort Cavazos from 2023 to 2025. The 2026 rate file uses Fort Hood, TX.",
    "tacoma-wa": "Joint Base Lewis-McChord (JBLM), including the former Fort Lewis, is in this Tacoma, WA housing area.",
    "el-paso-tx": "Fort Bliss is in this El Paso, TX housing area.",
    "washington-dc-metro-area": "Fort Belvoir is in this Washington, DC Metro Area housing area. DoD did not publish a separate Fort Belvoir rate.",
}

# Common PCS areas, in the order shown on the hub. Not a ranking.
PCS_DESTINATIONS = (
    ("fort-bragg-pope-nc", "Fort Bragg, NC", "Also called Fort Liberty, 2023–2025."),
    ("fort-hood-tx", "Fort Hood, TX", "Also called Fort Cavazos, 2023–2025."),
    ("camp-pendleton-ca", "Camp Pendleton, CA", ""),
    ("san-diego-ca", "San Diego, CA", ""),
    ("norfolk-portsmouth-va", "Norfolk/Portsmouth, VA", ""),
    ("fort-campbell-ky", "Fort Campbell, KY", ""),
    ("camp-lejeune-nc", "Camp Lejeune, NC", ""),
    ("fort-stewart-ga", "Fort Stewart, GA", ""),
)


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
            entry = {"slug": slug, "label": label, "code": mha.code, "refresh": False, "sitemap": False, "note": ""}
            aliases[mha.code].append(entry)
            alias_pages.append(entry)
    by_slug = {mha.slug: mha for mha in data.mhas.values()}
    for slug, label, target_slug, note in SEO_ALIASES:
        target = by_slug.get(target_slug)
        if target is None:
            raise SystemExit(f"SEO alias {slug} has no duty-station page {target_slug}")
        if slug in used:
            raise SystemExit(f"SEO alias slug {slug} collides with an existing page")
        used.add(slug)
        entry = {
            "slug": slug, "label": label, "code": target.code,
            "refresh": True, "sitemap": True, "note": note,
        }
        aliases[target.code].append(entry)
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
          description=f"Search any of the {data.year} DoD duty ZIPs. Each ZIP opens the housing area that holds its BAH rates.",
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

    def ld_for(mha, path, name):
        return dataset_ld(
            mha, public + path, name, data.year, manifest["source_url"], site["bah_page"], site["owner"],
        )

    for mha in data.mhas.values():
        code = mha.code.lower()
        mha_path = f"/mha/{code}/"
        note = PRIMARY_NOTES.get(mha.slug, "")
        write(f"mha/{code}/index.html", "mha.html",
              page_title=f"{data.year} BAH for {mha.name} ({mha.code}) | {site['name']}",
              description=f"Monthly {data.year} BAH for {mha.name}, MHA {mha.code}, with and without dependents, by pay grade.",
              canonical=mha_path, mha=mha, name_note=note,
              dataset=ld_for(mha, mha_path, f"{data.year} BAH rates for {mha.name}"))
        pages.append((mha_path, lastmod))
        heading = mha.name
        duty_path = f"/duty-station/{mha.slug}/"
        duty_title = f"{data.year} BAH Rates {heading} | {site['name']}"
        write(f"duty-station/{mha.slug}/index.html", "duty.html",
              page_title=duty_title,
              description=f"{data.year} BAH rates at {heading} ({mha.code}), with and without dependents. Not a rating.",
              canonical=duty_path, mha=mha, heading=heading, alias_of=None, aliases=aliases[mha.code],
              name_note=note, refresh_url="",
              dataset=ld_for(mha, duty_path, f"{data.year} BAH Rates {heading}"))
        pages.append((duty_path, lastmod))

    for alias in alias_pages:
        mha = data.mhas[alias["code"]]
        primary = f"/duty-station/{mha.slug}/"
        alias_path = f"/duty-station/{alias['slug']}/"
        # Renamed-base pages refresh to the official area and stay in the sitemap.
        # Slash-name aliases stay out of the sitemap and use noindex.
        robots = "index, follow" if alias["sitemap"] else "noindex, follow"
        refresh_url = f"{base}{primary}" if alias["refresh"] else ""
        write(f"duty-station/{alias['slug']}/index.html", "duty.html",
              page_title=f"{data.year} BAH Rates {alias['label']} | {site['name']}",
              description=f"{alias['label']} uses the {mha.name} housing area ({mha.code}). {data.year} BAH with and without dependents.",
              canonical=primary, mha=mha, heading=alias["label"], alias_of=mha, aliases=[],
              name_note=alias["note"], refresh_url=refresh_url, robots=robots,
              dataset=ld_for(mha, primary, f"{data.year} BAH Rates {mha.name}"))
        if alias["sitemap"]:
            pages.append((alias_path, lastmod))

    destinations = []
    for slug, label, note in PCS_DESTINATIONS:
        found = by_slug.get(slug)
        if found is None:
            raise SystemExit(f"PCS destination has no duty-station page: {slug}")
        destinations.append({"slug": slug, "label": label, "note": note, "e5": found.with_rates["E-5"]})
    write("pcs-destinations/index.html", "destinations.html",
          page_title=f"Top PCS destinations {data.year} BAH | {site['name']}",
          description="2026 BAH pages for Fort Bragg, Fort Hood, Camp Pendleton, San Diego, Norfolk, Fort Campbell, Camp Lejeune, and Fort Stewart. Not a ranking.",
          canonical="/pcs-destinations/", destinations=destinations)
    pages.append(("/pcs-destinations/", today))

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
        f"{len(data.zip_to_mha)} ZIPs in the search index, {len(alias_pages)} aliases -> {OUT}"
    )


if __name__ == "__main__":
    main()
