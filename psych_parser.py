from __future__ import annotations
"""
psych_parser.py
===============
Parses a HY-TEK psych sheet PDF into a structured events dictionary.

Handles:
  - Two-column page layout (left processed fully before right)
  - Shared event state across columns and pages
  - Girls/Boys (age group) and Women/Men (senior) event headers
  - Split swimmer rows (name/age on one line, team/time on next)
  - Special characters in names (ϐ ligature, hyphens, apostrophes)
  - YB / LB time suffixes
  - Relay events (parsed but swimmers skipped)

UPDATE v2: Accepts optional MeetConfig parameter.
"""

import re
from collections import defaultdict

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber required: pip install pdfplumber")

from event_normalizer import normalize_event_key


# ── Constants ─────────────────────────────────────────────────────────────────

COL_SPLIT_X = 295
HEADER_Y    = 80


# ── Regex patterns ────────────────────────────────────────────────────────────

EVENT_HEADER_RE = re.compile(
    r"^Event\s+(\d+)\s+(Girls|Boys|Women|Men)\s+(.+)$", re.IGNORECASE
)
EVENT_CONT_RE = re.compile(
    r"^Event\s+(\d+)\s+\.\.\.\((.+)\)$", re.IGNORECASE
)
RELAY_RE = re.compile(r"relay", re.IGNORECASE)

# Swimmer row — requires team ending in -XX and a time
SWIMMER_RE = re.compile(
    r"^(\d+)\s+"                               # rank
    r"(.+?)\s+"                                # name
    r"(\d{1,2})\s+"                            # age
    r"(.+?-[A-Z]{2,3})\s+"                    # team
    r"(NT|\d+:\d{2}\.\d{2}|\d{2,3}\.\d{2})"  # time
    r"(?:[A-Z]{0,2})?\s*$"
)

# Split row detection
_PARTIAL_RE = re.compile(r"^(\d+)\s+.+?\s+(\d{1,2})\s*$")
_ORPHAN_RE  = re.compile(
    r"^(.+?-[A-Z]{2,3})\s+(NT|\d+:\d{2}\.\d{2}|\d{2,3}\.\d{2})"
    r"(?:[A-Z]{0,2})?\s*$"
)

SKIP_RE = re.compile(
    r"^(Name|Team|Age|Seed|Meet Qualifying|Idaho Central|"
    r"2026 Mountain|2026 NW|Sanction|Psych Sheet)",
    re.IGNORECASE
)


# ── Split row merger ──────────────────────────────────────────────────────────

def _merge_split_rows(lines):
    """
    Merge swimmer rows split across two lines.

    Pattern A (partial then orphan):
      '1 Hank Cheng 14'   +   'Sawtooth-SR 53.98'

    Pattern B (orphan then partial):
      'Sawtooth-SR 53.98'   +   '1 Hank Cheng 14'
    """
    merged = []
    i = 0
    while i < len(lines):
        line     = lines[i]
        has_next = i + 1 < len(lines)

        if has_next and _PARTIAL_RE.match(line):
            nxt = lines[i + 1]
            if _ORPHAN_RE.match(nxt):
                merged.append(line + " " + nxt)
                i += 2
                continue

        if has_next and _ORPHAN_RE.match(line):
            nxt = lines[i + 1]
            if _PARTIAL_RE.match(nxt):
                merged.append(nxt + " " + line)
                i += 2
                continue

        merged.append(line)
        i += 1
    return merged


# ── Column extraction ─────────────────────────────────────────────────────────

def _extract_page_columns(page):
    """
    Split a page into left and right column line lists.
    Merges swimmer rows split across y-buckets.
    """
    words      = page.extract_words()
    left_rows  = defaultdict(list)
    right_rows = defaultdict(list)

    for w in words:
        if w["top"] < HEADER_Y:
            continue
        y = round(w["top"] / 2) * 2
        if w["x0"] < COL_SPLIT_X:
            left_rows[y].append((w["x0"], w["text"]))
        else:
            right_rows[y].append((w["x0"], w["text"]))

    def to_lines(rows):
        raw = [
            " ".join(t for _, t in sorted(rows[y]))
            for y in sorted(rows.keys())
        ]
        return _merge_split_rows(raw)

    return to_lines(left_rows), to_lines(right_rows)


# ── Line parser ───────────────────────────────────────────────────────────────

def _parse_lines(lines, events, state, meet_config=None):
    for raw in lines:
        line = raw.strip()
        if not line or SKIP_RE.match(line):
            continue

        # New event header
        m = EVENT_HEADER_RE.match(line)
        if m:
            num        = int(m.group(1))
            gender_raw = m.group(2).lower()
            gender     = "Women" if gender_raw in ("girls", "women") else "Men"
            name       = (m.group(2).strip() + " " + m.group(3).strip()) \
                         .replace("\u03b2", "f") \
                         .replace("\u03d0", "f") \
                         .replace("\uFB06", "st") \
                         .replace("\uFB05", "st")
            ev_key     = normalize_event_key(name)
            is_relay   = bool(RELAY_RE.search(name))

            state["current_event"] = num
            state["is_relay"]      = is_relay

            if num not in events:
                format_type = "PRELIM_FINALS"
                if meet_config and ev_key and not is_relay:
                    format_type = meet_config.get_format(ev_key).value

                events[num] = {
                    "event_num":   num,
                    "gender":      gender,
                    "name":        name,
                    "event_key":   ev_key,
                    "format_type": format_type,
                    "is_relay":    is_relay,
                    "swimmers":    []
                }
            continue

        # Continuation header
        m = EVENT_CONT_RE.match(line)
        if m:
            num = int(m.group(1))
            state["current_event"] = num
            state["is_relay"] = (
                events[num]["is_relay"] if num in events else False
            )
            continue

        if state["current_event"] is None or state["is_relay"]:
            continue

        ev = events[state["current_event"]]

        # Swimmer row
        m = SWIMMER_RE.match(line)
        if m:
            rank      = int(m.group(1))
            full_name = m.group(2).strip()
            age       = int(m.group(3))
            team      = m.group(4).strip()
            seed_time = m.group(5).strip()

            if rank not in {s["rank"] for s in ev["swimmers"]}:
                ev["swimmers"].append({
                    "rank":      rank,
                    "name":      full_name,
                    "age":       age,
                    "team":      team,
                    "seed_time": seed_time,
                })


# ── Public API ────────────────────────────────────────────────────────────────

def parse_pdf(pdf_path, meet_config=None):
    """
    Parse a psych sheet PDF and return a structured events dictionary.
    """
    events = {}
    state  = {"current_event": None, "is_relay": False}

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            left_lines, right_lines = _extract_page_columns(page)
            _parse_lines(left_lines,  events, state, meet_config)
            _parse_lines(right_lines, events, state, meet_config)

    for ev in events.values():
        ev["swimmers"].sort(key=lambda s: s["rank"])

    return events


def find_swimmer(events, last_name, first_name, gender):
    last  = last_name.lower().strip()
    first = first_name.lower().strip()
    results = []
    for num, ev in sorted(events.items()):
        if ev["is_relay"] or ev["gender"] != gender:
            continue
        for s in ev["swimmers"]:
            name_lower = s["name"].lower()
            if last in name_lower and first in name_lower:
                results.append((num, s, ev))
    return results


def get_swimmer_at_rank(events, event_num, rank):
    ev = events.get(event_num)
    if not ev:
        return None
    for s in ev["swimmers"]:
        if s["rank"] == rank:
            return s
    return None


def verify_events(events):
    warnings = {}
    for num, ev in sorted(events.items()):
        if ev["is_relay"] or not ev["swimmers"]:
            continue
        issues   = []
        seen     = set()
        expected = 1
        for s in ev["swimmers"]:
            r = s["rank"]
            if r in seen:
                issues.append(f"Duplicate rank {r}: {s['name']}")
            seen.add(r)
            if r != expected:
                issues.append(
                    f"Gap: expected {expected}, got {r} ({s['name']})"
                )
            expected = r + 1
        if issues:
            warnings[num] = issues
    return warnings


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("── Import and structure tests ──────────────────────────────")
    assert callable(parse_pdf)
    assert callable(find_swimmer)
    assert callable(get_swimmer_at_rank)
    assert callable(verify_events)
    print("  All functions importable              ✓")

    from meet_config import MeetConfig, Course, MeetType
    cfg = MeetConfig(course=Course.SCY, meet_type=MeetType.SENIOR,
                     consolation_tiers=3)
    assert cfg.get_format("1650_free").value == "PURE_TIMED"
    assert cfg.get_format("100_back").value  == "PRELIM_FINALS"
    print("  MeetConfig integration                ✓")

    from event_normalizer import normalize_event_key
    assert normalize_event_key("Women 100 Yard Backstroke") == "100_back"
    assert normalize_event_key("Men 1650 Yard Freestyle")   == "1650_free"
    print("  Event key normalization               ✓")

    print("\n── Live PDF test ───────────────────────────────────────────")
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if pdf_path:
        events     = parse_pdf(pdf_path, meet_config=cfg)
        individual = sum(1 for ev in events.values() if not ev["is_relay"])
        print(f"  Events parsed: {individual} individual")
        print("\n  Hank Cheng events:")
        for num, ev in sorted(events.items()):
            if ev["is_relay"]: continue
            for s in ev["swimmers"]:
                if "cheng" in s["name"].lower() and "hank" in s["name"].lower():
                    print(f"    Ev {num:>3}  {ev['name']:<40}"
                          f"  #{s['rank']}  {s['seed_time']}")
    else:
        print("  No PDF given — pass path as argument.")

    print("\nAll structural tests passed ✓")