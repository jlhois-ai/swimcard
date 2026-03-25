from __future__ import annotations
"""
heat_parser.py
==============
Parses a HY-TEK heat sheet PDF and returns heat/lane assignments.

Handles two-column page layout (left column fully processed before right),
continuation heat headers across page breaks, empty lanes, and YB/LB suffixes.

Returns dict keyed by event_num (int):
  {
    "event_num":   int,
    "event_name":  str,
    "event_key":   str,   # canonical key e.g. "100_back"
    "round":       str,   # "Prelims" | "Finals"
    "assignments": { "Last, First": {"heat": int, "lane": int} }
  }
"""

import re
from collections import defaultdict

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber required: pip install pdfplumber")

from event_normalizer import normalize_event_key


# ── Constants ─────────────────────────────────────────────────────────────────

COL_SPLIT = 306   # x-coordinate midpoint between left and right columns


# ── Regex patterns ────────────────────────────────────────────────────────────

EVENT_RE = re.compile(
    r"^Event\s+(\d+)\s+(.+)$", re.IGNORECASE
)
HEAT_RE = re.compile(
    r"^Heat\s+(\d+)\s+of\s+\d+\s+(Prelims|Finals)", re.IGNORECASE
)
HEAT_CONT_RE = re.compile(
    r"^Heat\s+(\d+)\s+(Prelims|Finals)\s*\(#(\d+)", re.IGNORECASE
)
SWIMMER_RE = re.compile(
    r"^([1-8])\s+(.+?)\s+(\d{1,2})\s+(.+?-[A-Z]{2,3})\s+"
    r"(NT|\d+:\d{2}\.\d{2}|\d{2,3}\.\d{2})(?:[A-Z\s]{0,4})?\s*$"
)
EMPTY_LANE_RE = re.compile(r"^[1-8]$")
SKIP_RE = re.compile(
    r"^(Idaho Central|HY-TEK|2026 Mountain|Sanction|Meet Program|"
    r"Lane\s+Name|Page\s+\d+)",
    re.IGNORECASE,
)


# ── Column extraction ─────────────────────────────────────────────────────────

def _extract_columns(page):
    """Split page into left and right column line lists."""
    words = page.extract_words()
    left  = defaultdict(list)
    right = defaultdict(list)

    for w in words:
        y = round(w["top"] / 2) * 2
        if w["x0"] < COL_SPLIT:
            left[y].append((w["x0"], w["text"]))
        else:
            right[y].append((w["x0"], w["text"]))

    def to_lines(rows):
        return [
            " ".join(t for _, t in sorted(rows[y]))
            for y in sorted(rows)
        ]

    return to_lines(left), to_lines(right)


# ── Name converter ────────────────────────────────────────────────────────────

def _to_last_first(full_name):
    """
    Convert "First Last" to "Last, First" to match psych_parser.py format.
    "Mary Frances Brundige" → "Brundige, Mary Frances"
    """
    parts = full_name.strip().split()
    if len(parts) < 2:
        return full_name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


# ── Line parser ───────────────────────────────────────────────────────────────

def _parse_lines(lines, events, state):
    """Parse one column's lines into events dict."""
    for raw in lines:
        line = raw.strip()
        if not line or SKIP_RE.match(line):
            continue

        # Continuation heat header
        m = HEAT_CONT_RE.match(line)
        if m:
            state["heat_num"]  = int(m.group(1))
            state["round"]     = m.group(2).capitalize()
            state["event_num"] = int(m.group(3))
            continue

        # Event header
        m = EVENT_RE.match(line)
        if m:
            ev_num  = int(m.group(1))
            ev_name = m.group(2).strip()
            state["event_num"]  = ev_num
            state["event_name"] = ev_name
            state["round"]      = None
            state["heat_num"]   = None
            if ev_num not in events:
                events[ev_num] = {
                    "event_num":   ev_num,
                    "event_name":  ev_name,
                    "event_key":   normalize_event_key(ev_name),
                    "round":       None,
                    "assignments": {},
                }
            continue

        if state["event_num"] is None:
            continue

        # Heat header
        m = HEAT_RE.match(line)
        if m:
            state["heat_num"] = int(m.group(1))
            state["round"]    = m.group(2).capitalize()
            if state["event_num"] in events:
                events[state["event_num"]]["round"] = state["round"]
            continue

        if state["heat_num"] is None:
            continue

        # Empty lane
        if EMPTY_LANE_RE.match(line):
            continue

        # Swimmer row
        m = SWIMMER_RE.match(line)
        if m:
            lane       = int(m.group(1))
            last_first = _to_last_first(m.group(2).strip())
            ev_num     = state["event_num"]
            if ev_num in events:
                events[ev_num]["assignments"][last_first] = {
                    "heat": state["heat_num"],
                    "lane": lane,
                }


# ── Public API ────────────────────────────────────────────────────────────────

def parse_heat_sheet(pdf_path):
    """
    Parse a HY-TEK heat sheet PDF.

    Parameters
    ----------
    pdf_path : str

    Returns
    -------
    dict { event_num (int): event_dict }
    """
    events = {}
    state  = {"event_num": None, "event_name": None,
              "round": None, "heat_num": None}

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            left, right = _extract_columns(page)
            _parse_lines(left,  events, state)
            _parse_lines(right, events, state)

    return events


def find_swimmer_heats(parsed, last_name, first_name):
    """
    Find all heat assignments for a swimmer across all events.

    Returns list of dicts sorted by event number:
      [{ event_num, event_name, event_key, round, heat, lane }]
    """
    last  = last_name.strip().lower()
    first = first_name.strip().lower()
    found = []

    for ev_num, ev in sorted(parsed.items()):
        for name_key, assignment in ev["assignments"].items():
            name_lower = name_key.lower()
            if last in name_lower and first in name_lower:
                found.append({
                    "event_num":  ev_num,
                    "event_name": ev["event_name"],
                    "event_key":  ev["event_key"],
                    "round":      ev["round"],
                    "heat":       assignment["heat"],
                    "lane":       assignment["lane"],
                })
                break

    return found


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("── Name converter tests ────────────────────────────────────")
    name_tests = [
        ("Hank Cheng",              "Cheng, Hank"),
        ("Mary Frances Brundige",   "Brundige, Mary Frances"),
        ("Atticus Hendricks-Smith", "Hendricks-Smith, Atticus"),
        ("Phebe Freienmuth Fisher", "Fisher, Phebe Freienmuth"),
    ]
    all_pass = True
    for full, expected in name_tests:
        got = _to_last_first(full)
        ok  = got == expected
        print(f"  {full:<30} → {got:<30}  {'✓' if ok else '✗'}")
        if not ok:
            all_pass = False

    print("\n── Live PDF test ───────────────────────────────────────────")
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None

    if pdf_path:
        parsed = parse_heat_sheet(pdf_path)
        print(f"  Events parsed: {len(parsed)}")

        print("\n  First 5 events:")
        for num, ev in list(sorted(parsed.items()))[:5]:
            print(f"    Ev {num:>3}  {ev['event_name']:<40}"
                  f"  round={ev['round']:<8}"
                  f"  swimmers={len(ev['assignments'])}")

        print("\n  Searching for Hank Cheng:")
        for h in find_swimmer_heats(parsed, "Cheng", "Hank"):
            print(f"    Ev {h['event_num']:>3}  {h['event_name']:<38}"
                  f"  {h['round']:<8}"
                  f"  Heat {h['heat']} Lane {h['lane']}")
    else:
        print("  No PDF given — pass path as argument.")
        print("  Usage: python heat_parser.py path/to/heat_sheet.pdf")

    print()
    print("Unit tests passed ✓" if all_pass else "FAILURES above ✗")