#!/usr/bin/env python3
"""Parse a DTMO BAH-ASCII zip (with dependents, without dependents, ZIP, names).

The bundle is the file distributed from the BAH rate-files page:
https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Files-Alt

Layout, confirmed against the 2026 bundle and the ASCII-FILE-FORMAT.pdf inside it:
  sorted_zipmhaYY.txt   whitespace "ZIP MHA"
  bahwYY.txt            headerless CSV, rates WITH dependents
  bahwoYY.txt           headerless CSV, rates WITHOUT dependents
  mhanamesYY.txt        "MHA;NAME"
  "* - old.txt"         previous publication; never used for the tables

Rate columns, after the MHA code:
  E-1..E-9, W-1..W-5, O-1E, O-2E, O-3E, O-1..O-10
O-7, O-8, O-9, and O-10 are the same amount in the published files (DoD's
"O-7 and above" bucket). County cost groups use MHA codes that start with ZZ.
XX499 is in the ZIP crosswalk and the name file ("UNKNOWN, NA") and has no rate row.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field

MHA_RE = re.compile(r"^[A-Z]{2}\d{3}$")
OLD_RE = re.compile(r"\bold\b|\bprev(?:ious)?\b|\bbackup\b", re.IGNORECASE)

RATE_COLUMNS: tuple[str, ...] = (
    *(f"E-{i}" for i in range(1, 10)),
    *(f"W-{i}" for i in range(1, 6)),
    "O-1E",
    "O-2E",
    "O-3E",
    *(f"O-{i}" for i in range(1, 11)),
)
FIELD_COUNT = 1 + len(RATE_COLUMNS)  # 28
SENIOR = ("O-7", "O-8", "O-9", "O-10")

ACRONYMS = {
    "AFB", "SFB", "NAS", "NAF", "MCB", "MCAS", "JB", "JRB", "NSA", "NSB",
    "NWS", "AAF", "CG", "US", "USA", "DC", "AB",
}
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}


class ParseError(ValueError):
    pass


@dataclass
class Mha:
    code: str
    name: str
    raw_name: str
    kind: str  # "named" or "county"
    state: str
    with_rates: dict[str, int]
    without_rates: dict[str, int]
    zips: list[str] = field(default_factory=list)
    slug: str = ""
    changed_vs_old: bool = False


@dataclass
class BahData:
    year: int
    mhas: dict[str, Mha]
    zip_to_mha: dict[str, str]
    grades: list[str]
    senior_collapsed: bool
    changed_vs_old: list[str]
    warnings: list[str]
    members: dict[str, str]


def nice_token(tok: str, *, state_slot: bool) -> str:
    match = re.match(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$", tok)
    if not match:
        return tok
    pre, core, post = match.groups()
    upper = core.upper()
    if upper in ACRONYMS or (state_slot and upper in STATE_CODES):
        return f"{pre}{upper}{post}"
    if upper == "ST":
        if post.startswith("."):
            return f"{pre}St{post}"
        if post == "":
            return f"{pre}St."
    if upper == "AND" and not state_slot:
        return f"{pre}and{post}"
    return f"{pre}{core.capitalize()}{post}"


def nice_name(raw: str) -> str:
    """Turn 'FORT BRAGG/POPE, NC' into 'Fort Bragg/Pope, NC'."""
    words = " ".join(raw.split()).split(" ")
    out = []
    last = len(words) - 1
    for i, word in enumerate(words):
        state_slot = i == last
        if "/" in word:
            out.append("/".join(nice_token(part, state_slot=state_slot) for part in word.split("/")))
        else:
            out.append(nice_token(word, state_slot=state_slot))
    return " ".join(out)


def _members(archive: zipfile.ZipFile) -> dict[str, str]:
    return {name.rsplit("/", 1)[-1]: name for name in archive.namelist()}


def _pick(by_base: dict[str, str], exact: str, prefix: str) -> str:
    if exact in by_base:
        return by_base[exact]
    folded = {k.lower(): v for k, v in by_base.items()}
    if exact.lower() in folded:
        return folded[exact.lower()]
    candidates = sorted(
        base for base in by_base
        if base.lower().startswith(prefix.lower())
        and base.lower().endswith(".txt")
        and not OLD_RE.search(base)
        and not (prefix.lower() == "bahw" and base.lower().startswith("bahwo"))
    )
    if not candidates:
        raise ParseError(
            f"no member named {exact!r} (prefix {prefix!r}); bundle has {sorted(by_base)}"
        )
    return by_base[candidates[0]]


def _read(archive: zipfile.ZipFile, by_base: dict[str, str], exact: str, prefix: str) -> tuple[str, str]:
    name = _pick(by_base, exact, prefix)
    text = archive.read(by_base[name]).decode("utf-8-sig", errors="replace")
    return name, text


def _parse_rates(text: str, source: str) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    for line_no, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != FIELD_COUNT:
            raise ParseError(
                f"{source} line {line_no}: expected {FIELD_COUNT} fields, got {len(row)}"
            )
        code = row[0].strip().upper()
        if not MHA_RE.match(code):
            raise ParseError(f"{source} line {line_no}: {code!r} is not an MHA code")
        rates: dict[str, int] = {}
        for grade, cell in zip(RATE_COLUMNS, row[1:]):
            cell = cell.strip().replace(",", "").lstrip("$")
            if not cell:
                raise ParseError(f"{source} line {line_no}: empty rate for {code} {grade}")
            try:
                value = float(cell)
            except ValueError as exc:
                raise ParseError(
                    f"{source} line {line_no}: {code} {grade} is not a number ({cell!r})"
                ) from exc
            if value < 0 or abs(value - round(value)) > 0.001:
                raise ParseError(f"{source} line {line_no}: {code} {grade} is not a whole dollar amount")
            rates[grade] = int(round(value))
        table[code] = rates
    if not table:
        raise ParseError(f"{source} contained no rate rows")
    return table


def _parse_names(text: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            continue
        if ";" in stripped:
            code, name = stripped.split(";", 1)
        else:
            code, name = stripped[:5], stripped[5:]
        code = code.strip().upper()
        if not MHA_RE.match(code):
            continue
        name = " ".join(name.strip().strip('"').split())
        if name:
            names[code] = name
    return names


def _parse_zips(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2 or not parts[0].isdigit():
            raise ParseError(f"ZIP crosswalk line {line_no} is not 'ZIP MHA': {stripped!r}")
        mapping[parts[0].zfill(5)] = parts[1].strip().upper()
    if not mapping:
        raise ParseError("ZIP crosswalk was empty")
    return mapping


def _changed(archive: zipfile.ZipFile, by_base: dict[str, str], year: int, current_with: dict, current_without: dict) -> list[str]:
    yy = f"{year % 100:02d}"
    changed: set[str] = set()
    for label, current, exact in (
        ("with", current_with, f"bahw{yy} - old.txt"),
        ("without", current_without, f"bahwo{yy} - old.txt"),
    ):
        key = next((base for base in by_base if base.lower() == exact.lower()), None)
        if key is None:
            continue
        old = _parse_rates(archive.read(by_base[key]).decode("utf-8-sig", errors="replace"), key)
        codes = set(current) | set(old)
        for code in codes:
            if current.get(code) != old.get(code):
                changed.add(code)
        del label
    return sorted(changed)


def slugify(text: str) -> str:
    slug = text.lower().replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def parse_bah_zip(blob: bytes) -> BahData:
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        by_base = _members(archive)
        year = _detect_year(by_base)
        yy = f"{year % 100:02d}"
        zip_name, zip_text = _read(archive, by_base, f"sorted_zipmha{yy}.txt", "sorted_zipmha")
        with_name, with_text = _read(archive, by_base, f"bahw{yy}.txt", "bahw")
        without_name, without_text = _read(archive, by_base, f"bahwo{yy}.txt", "bahwo")
        if with_name.lower() == without_name.lower() or with_name.lower().startswith("bahwo"):
            raise ParseError(f"with-dependents file resolved to {with_name}, not bahw")
        names_name, names_text = _read(archive, by_base, f"mhanames{yy}.txt", "mhanames")
        with_rates = _parse_rates(with_text, with_name)
        without_rates = _parse_rates(without_text, without_name)
        if set(with_rates) != set(without_rates):
            raise ParseError("with-dependents and without-dependents MHA sets differ")
        names = _parse_names(names_text)
        zip_to_mha = _parse_zips(zip_text)
        changed = _changed(archive, by_base, year, with_rates, without_rates)

    senior_collapsed = True
    for rates in with_rates.values():
        if len({rates[g] for g in SENIOR}) != 1:
            senior_collapsed = False
            break
    for rates in without_rates.values():
        if len({rates[g] for g in SENIOR}) != 1:
            senior_collapsed = False
            break
    if not senior_collapsed:
        warnings.append("O-7 through O-10 are not identical in this file; each grade is shown on its own.")

    grades = [g for g in RATE_COLUMNS if not (senior_collapsed and g in ("O-8", "O-9", "O-10"))]

    zips_for: dict[str, list[str]] = {code: [] for code in with_rates}
    unknown = 0
    for zip_code, mha in zip_to_mha.items():
        if mha in zips_for:
            zips_for[mha].append(zip_code)
        else:
            unknown += 1
    if unknown:
        warnings.append(f"{unknown} ZIP codes map to an MHA with no rate row (usually XX499).")

    mhas: dict[str, Mha] = {}
    used_slugs: set[str] = set()
    for code in sorted(with_rates):
        raw = names.get(code, code)
        display = nice_name(raw)
        kind = "county" if code.startswith("ZZ") else "named"
        state = code[:2]
        slug = slugify(display)
        if not slug or slug in used_slugs:
            slug = slugify(f"{display}-{code}")
        used_slugs.add(slug)
        for grade in RATE_COLUMNS:
            if grade not in with_rates[code] or grade not in without_rates[code]:
                raise ParseError(f"{code} is missing {grade}")
        mhas[code] = Mha(
            code=code,
            name=display,
            raw_name=raw,
            kind=kind,
            state=state if state in STATE_CODES or state == "ZZ" else state,
            with_rates=with_rates[code],
            without_rates=without_rates[code],
            zips=sorted(zips_for[code]),
            slug=slug,
            changed_vs_old=code in changed,
        )

    return BahData(
        year=year,
        mhas=mhas,
        zip_to_mha=zip_to_mha,
        grades=grades,
        senior_collapsed=senior_collapsed,
        changed_vs_old=changed,
        warnings=warnings,
        members={
            "zip_mha": zip_name,
            "with_dependents": with_name,
            "without_dependents": without_name,
            "mha_names": names_name,
        },
    )


def _detect_year(by_base: dict[str, str]) -> int:
    for base in by_base:
        match = re.fullmatch(r"bahw(\d{2})\.txt", base, flags=re.IGNORECASE)
        if match and not OLD_RE.search(base):
            yy = int(match.group(1))
            return 2000 + yy
    raise ParseError(f"could not find bahwYY.txt in {sorted(by_base)}")


def grade_label(grade: str, *, senior_collapsed: bool) -> str:
    if senior_collapsed and grade == "O-7":
        return "O-7 and above"
    return grade


def grade_group(grade: str) -> str:
    if grade.startswith("E-"):
        return "Enlisted"
    if grade.startswith("W-"):
        return "Warrant"
    if grade.endswith("E"):
        return "Officers with prior enlisted service"
    return "Officers"
