from __future__ import annotations
"""
goals_parser.py
===============
Parses a SwimCloud goals PDF for one swimmer.

SwimCloud PDFs come in two formats:
  TEXT-BASED  → pdfplumber extracts text directly (this parser handles it)
  IMAGE-BASED → pdfplumber finds no text; route through vision_extractor.py

SwimCloud text PDF format — two patterns per event:

  Pattern A — cut achieved:
    50 Free
    SECTIONALS CUT        ← current cut tier achieved
    24.24                 ← personal best
    FUTURES               ← next cut target
    23.89                 ← next cut time needed
    drop 0.35s · 98.5% to goal

  Pattern B — no cut achieved yet:
    100 Free 53.28        ← event name + personal best on same line
    SECTIONALS            ← next cut target (no "CUT" suffix)
    53.09                 ← next cut time needed
    drop 0.19s · 99.6% to goal

Returns a dict keyed by canonical event key (from event_normalizer).
"""

import re

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber required: pip install pdfplumber")

from event_normalizer import normalize_event_key
from meet_file import GoalsData


# ── Cut tier name map ─────────────────────────────────────────────────────────
# Maps uppercase PDF text → display name

_CUT_TIER_MAP = {
    "MOTIVATIONAL":    "Motivational",
    "SECTIONALS":      "Sectionals",
    "FUTURES":         "Futures",
    "WINTERJRS":       "Winter Jr's",
    "WINTERJR'S":      "Winter Jr's",
    "SUMMERJRS":       "Summer Jr's",
    "SUMMERJR'S":      "Summer Jr's",
    "JUNIORNATS":      "Junior Nationals",
    "JUNIORNATIONAL":  "Junior Nationals",
    "JUNIORNATIONALS": "Junior Nationals",
    "SENIORNATS":      "Senior Nationals",
    "SENIORNATIONAL":  "Senior Nationals",
    "SENIORNATIONALS": "Senior Nationals",
    "OLYMPICTRIALS":   "Olympic Trials",
}

# ── Regex patterns ────────────────────────────────────────────────────────────

# Time: 24.24  or  1:51.69  or  10:13.52  or  17:16.82
_TIME_RE = re.compile(r"^(?:\d+:)?\d{2,3}\.\d{2}$")

# Drop line: "drop 0.35s · 98.5% to goal"
_DROP_RE = re.compile(
    r"drop\s+([\d.]+)s\s*[·•·]\s*([\d.]+)%\s*to\s*goal",
    re.IGNORECASE,
)

# Event name contains a known distance
_DISTANCE_RE = re.compile(r"\b(50|100|200|400|500|800|1000|1500|1650)\b")

# Stroke words that appear in event names
_STROKE_WORDS = {
    "free", "freestyle", "back", "backstroke",
    "breast", "breaststroke", "fly", "butterfly",
    "im", "medley",
}

# Lines to skip — headers, footers, column labels
_SKIP_PATTERNS = [
    "SCY", "LCM", "SCM", "SHORT COURSE", "LONG COURSE",
    "swimcloud", "usaswimming", "Sources:", "Events ·",
    "Always confirm", "Class of", "EVENT", "BEST TIME", "NEXT GOAL",
    "Sectionals  Futures",   # column header row
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_time(s):
    return bool(_TIME_RE.match(s.strip()))


def _is_event_name(s):
    """Does this string look like a swim event name?"""
    has_distance = bool(_DISTANCE_RE.search(s))
    has_stroke   = any(w in s.lower() for w in _STROKE_WORDS)
    return has_distance and has_stroke


def _parse_cut_line(s):
    """
    Detect a cut tier line.

    Returns (tier_name, is_achieved) or (None, False).
      "SECTIONALS CUT"  → ("Sectionals", True)
      "FUTURES"         → ("Futures",    False)
      "WINTER JR'S"     → ("Winter Jr's",False)
    """
    clean    = s.strip().upper()
    achieved = clean.endswith(" CUT") or clean == "CUT"
    check    = clean.replace(" CUT", "").strip()

    # Normalize: remove spaces and apostrophes for lookup
    key = check.replace(" ", "").replace("'", "").replace("\u2019", "")

    if key in _CUT_TIER_MAP:
        return _CUT_TIER_MAP[key], achieved

    # Partial match fallback
    for k, v in _CUT_TIER_MAP.items():
        if k.startswith(key) or key.startswith(k):
            return v, achieved

    return None, False


def _should_skip(line):
    return any(p in line for p in _SKIP_PATTERNS)


# ── Line extraction ───────────────────────────────────────────────────────────

def _extract_lines(pdf_path):
    """
    Extract text lines from a PDF.
    Returns empty list if PDF is image-based (no extractable text).
    """
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    line = line.strip()
                    if line:
                        lines.append(line)
    return lines


# ── Block resolver ────────────────────────────────────────────────────────────

def _resolve_block(raw_name, pb_on_name, cut_lines, times, drop_needed, pct_to_goal):
    """
    Turn one event's accumulated data into a GoalsData object.

    Parameters
    ----------
    raw_name    : str   Event name without time
    pb_on_name  : str|None  Time found on the same line as event name
    cut_lines   : list of (tier_name, is_achieved)
    times       : list of time strings found after the event name line
    drop_needed : float
    pct_to_goal : float
    """
    # Personal best
    if pb_on_name:
        personal_best   = pb_on_name
        remaining_times = list(times)
    elif times:
        personal_best   = times[0]
        remaining_times = times[1:]
    else:
        return None

    # Current cut = last achieved tier
    achieved    = [t for t, a in cut_lines if a]
    current_cut = achieved[-1] if achieved else ""

    # Next cut = first non-achieved tier
    not_achieved = [t for t, a in cut_lines if not a]
    if not not_achieved:
        return None   # no next goal — swimmer has achieved all tiers shown
    next_cut = not_achieved[0]

    # Next cut time = first remaining time after personal best
    if not remaining_times:
        return None
    next_cut_time = remaining_times[0]

    return GoalsData(
        personal_best = personal_best,
        current_cut   = current_cut,
        next_cut      = next_cut,
        next_cut_time = next_cut_time,
        drop_needed   = drop_needed or 0.0,
        pct_to_goal   = pct_to_goal or 0.0,
    )


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_goals_pdf(pdf_path):
    """
    Parse a SwimCloud goals PDF and return per-event goals data.

    Parameters
    ----------
    pdf_path : str  Path to the SwimCloud goals PDF.

    Returns
    -------
    dict  { canonical_event_key: GoalsData }

    Raises
    ------
    ValueError  If the PDF appears to be image-based (no extractable text).
                Route image-based PDFs through vision_extractor.py instead.
    """
    lines = _extract_lines(pdf_path)

    if not lines:
        raise ValueError(
            "No text found in this PDF — it appears to be image-based. "
            "Upload it via the screenshot/vision route instead."
        )

    result = {}

    # Current event accumulator
    raw_name    = None
    pb_on_name  = None
    cut_lines   = []
    times       = []
    drop_needed = None
    pct_to_goal = None

    def flush():
        nonlocal raw_name, pb_on_name, cut_lines, times, drop_needed, pct_to_goal
        if raw_name:
            key  = normalize_event_key(raw_name)
            data = _resolve_block(
                raw_name, pb_on_name, cut_lines, times, drop_needed, pct_to_goal
            )
            if key and data:
                result[key] = data
        raw_name    = None
        pb_on_name  = None
        cut_lines   = []
        times       = []
        drop_needed = None
        pct_to_goal = None

    for line in lines:
        if _should_skip(line):
            continue

        # ── New event name? ────────────────────────────────────────────────
        if _is_event_name(line):
            flush()
            # Time may be appended: "100 Free 53.28"
            parts = line.rsplit(" ", 1)
            if len(parts) == 2 and _is_time(parts[1]):
                raw_name   = parts[0].strip()
                pb_on_name = parts[1].strip()
            else:
                raw_name   = line
                pb_on_name = None
            continue

        if raw_name is None:
            continue   # haven't found first event yet

        # ── Cut tier line? ─────────────────────────────────────────────────
        tier, achieved = _parse_cut_line(line)
        if tier:
            cut_lines.append((tier, achieved))
            continue

        # ── Time? ──────────────────────────────────────────────────────────
        if _is_time(line):
            times.append(line)
            continue

        # ── Drop line? ─────────────────────────────────────────────────────
        m = _DROP_RE.search(line)
        if m:
            drop_needed = float(m.group(1))
            pct_to_goal = float(m.group(2))
            continue

    flush()   # handle last event
    return result


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # ── Unit test: block resolver ──────────────────────────────────────────
    print("── Block resolver unit tests ───────────────────────────────")

    # Pattern A: cut achieved, time on own line
    d = _resolve_block(
        raw_name    = "50 Free",
        pb_on_name  = None,
        cut_lines   = [("Sectionals", True), ("Futures", False)],
        times       = ["24.24", "23.89"],
        drop_needed = 0.35,
        pct_to_goal = 98.5,
    )
    assert d.personal_best == "24.24",  f"PB wrong: {d.personal_best}"
    assert d.current_cut   == "Sectionals", f"Cut wrong: {d.current_cut}"
    assert d.next_cut      == "Futures",    f"Next wrong: {d.next_cut}"
    assert d.next_cut_time == "23.89",  f"Time wrong: {d.next_cut_time}"
    assert d.drop_needed   == 0.35,     f"Drop wrong: {d.drop_needed}"
    assert d.pct_to_goal   == 98.5,     f"Pct wrong: {d.pct_to_goal}"
    print("  Pattern A (cut achieved, time on own line)  ✓")

    # Pattern B: no cut, time on name line
    d2 = _resolve_block(
        raw_name    = "100 Free",
        pb_on_name  = "53.28",
        cut_lines   = [("Sectionals", False)],
        times       = ["53.09"],
        drop_needed = 0.19,
        pct_to_goal = 99.6,
    )
    assert d2.personal_best == "53.28",     f"PB wrong: {d2.personal_best}"
    assert d2.current_cut   == "",          f"Cut wrong: {d2.current_cut}"
    assert d2.next_cut      == "Sectionals",f"Next wrong: {d2.next_cut}"
    assert d2.next_cut_time == "53.09",     f"Time wrong: {d2.next_cut_time}"
    print("  Pattern B (no cut, time on name line)        ✓")

    # Long distance time format
    d3 = _resolve_block(
        raw_name    = "1650 Free",
        pb_on_name  = None,
        cut_lines   = [("Sectionals", True), ("Futures", False)],
        times       = ["17:16.82", "17:14.39"],
        drop_needed = 2.43,
        pct_to_goal = 99.8,
    )
    assert d3.personal_best == "17:16.82"
    assert d3.next_cut_time == "17:14.39"
    print("  Long distance time format (17:16.82)          ✓")

    # ── Cut line parser ────────────────────────────────────────────────────
    print("\n── Cut line parser tests ───────────────────────────────────")
    tests = [
        ("SECTIONALS CUT",   "Sectionals",   True),
        ("FUTURES CUT",      "Futures",      True),
        ("SECTIONALS",       "Sectionals",   False),
        ("FUTURES",          "Futures",      False),
        ("WINTER JR'S",      "Winter Jr's",  False),
        ("SUMMER JR'S",      "Summer Jr's",  False),
        ("not a cut line",   None,           False),
    ]
    all_pass = True
    for raw, expected_tier, expected_achieved in tests:
        tier, achieved = _parse_cut_line(raw)
        ok = (tier == expected_tier and achieved == expected_achieved)
        print(f"  {raw:<22} → tier={str(tier):<18} achieved={achieved}  {'✓' if ok else '✗'}")
        if not ok:
            all_pass = False

    # ── Live PDF test (optional) ───────────────────────────────────────────
    print("\n── Live PDF test ───────────────────────────────────────────")
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None

    if pdf_path:
        try:
            goals = parse_goals_pdf(pdf_path)
            print(f"  Events parsed: {len(goals)}")
            for key, g in sorted(goals.items()):
                print(f"  {key:<15}  PB={g.personal_best:<10}"
                      f"  cut={g.current_cut:<12}"
                      f"  next={g.next_cut:<15}"
                      f"  target={g.next_cut_time:<10}"
                      f"  drop={g.drop_needed}s")
        except ValueError as e:
            print(f"  ⚠  {e}")
    else:
        print("  (no PDF path given — pass your goals PDF as an argument)")
        print("  Usage: python goals_parser.py path/to/goals.pdf")

    print()
    print("Unit tests passed ✓" if all_pass else "FAILURES above ✗")