from __future__ import annotations
"""
schedule_parser.py
==================
Parses a HY-TEK session report PDF and returns per-event schedule data.

Format per event row:
  Prelims 18 Boys 14 Year Olds 100 Backstroke 9 2 u 09:39 AM _______
  Finals  45 Girls 11-12 400 IM               6 1 u 10:54 AM _______

Returns dict keyed by event_num (int):
  {
    event_num: {
      "event_num":    int,
      "event_name":   str,
      "event_key":    str,   # canonical key e.g. "100_back"
      "round":        str,   # "Prelims" | "Finals"
      "session_num":  int,
      "session_name": str,   # e.g. "Friday Prelims"
      "day":          int,   # 1-indexed day of meet
      "est_start":    str,   # e.g. "9:39 AM"
    }
  }

Note: Events appearing in BOTH a prelims and finals session get two
entries — the prelims entry is stored under round="Prelims" and the
finals entry under round="Finals". The app uses both.
"""

import re

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber required: pip install pdfplumber")

from event_normalizer import normalize_event_key


# ── Regex patterns ────────────────────────────────────────────────────────────

# Session header: "Session: 2 Friday Prelims"
SESSION_RE = re.compile(
    r"Session:\s*(\d+)\s+(.+)$", re.IGNORECASE
)

# Day line: "Day of Meet: 2 Starts at 09:00 AM ..."
DAY_RE = re.compile(
    r"Day of Meet:\s*(\d+)\s+Starts at\s+(\d+:\d{2}\s+[AP]M)",
    re.IGNORECASE
)

# Event row:
#   "Prelims 18 Boys 14 Year Olds 100 Backstroke 9 2 u 09:39 AM _______"
#   "Finals  45 Girls 11-12 400 IM 6 1 u 10:54 AM _______"
# Pattern: round, event_num, event_name (greedy up to last nums), time
EVENT_ROW_RE = re.compile(
    r"^(Prelims|Finals)\s+"          # round
    r"(\d+)\s+"                      # event number
    r"(.+?)\s+"                      # event name (non-greedy)
    r"\d+\s+"                        # entries count
    r"\d+\s+"                        # heats count
    r"(?:u\s+)?"                     # optional "u" flag
    r"(\d+:\d{2}\s+[AP]M)"          # estimated start time
    r"(?:\s+_+)?$",                  # optional trailing underscores
    re.IGNORECASE
)

# Lines to skip
SKIP_RE = re.compile(
    r"^(Idaho Central|HY-TEK|2026 Mountain|Sanction|Session Report|"
    r"Round\s+Event|Swimmers Counts|Entry|Finish Time|Break:)",
    re.IGNORECASE
)


# ── Time formatter ────────────────────────────────────────────────────────────

def _fmt_time(raw):
    """
    Normalize time string for display.
    "09:39 AM" → "9:39 AM"   (drop leading zero on hour)
    "11:06 AM" → "11:06 AM"  (unchanged)
    """
    raw = raw.strip()
    # Remove leading zero from hour only
    if raw.startswith("0"):
        raw = raw[1:]
    return raw


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_schedule(pdf_path):
    """
    Parse a HY-TEK session report PDF.

    Parameters
    ----------
    pdf_path : str  Path to the session report PDF.

    Returns
    -------
    dict  { event_num (int): [schedule_dict, ...] }

    Each event may appear multiple times (prelims + finals).
    Returns a LIST per event_num to preserve both entries.
    Use get_prelim_schedule() / get_finals_schedule() helpers for lookup.
    """
    results = {}   # event_num → list of schedule dicts

    session_num  = None
    session_name = None
    day          = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for raw_line in text.split("\n"):
                # Normalize tabs to spaces, fix ϐ ligature
                line = raw_line.replace("\t", " ").replace("\u03b2", "f").strip()
                if not line or SKIP_RE.match(line):
                    continue

                # Session header
                m = SESSION_RE.search(line)
                if m:
                    session_num  = int(m.group(1))
                    session_name = m.group(2).strip()
                    continue

                # Day line
                m = DAY_RE.search(line)
                if m:
                    day = int(m.group(1))
                    continue

                if session_num is None or day is None:
                    continue

                # Event row
                m = EVENT_ROW_RE.match(line)
                if m:
                    round_str  = m.group(1).capitalize()
                    ev_num     = int(m.group(2))
                    ev_name    = m.group(3).strip()
                    est_start  = _fmt_time(m.group(4))
                    ev_key     = normalize_event_key(ev_name)

                    entry = {
                        "event_num":    ev_num,
                        "event_name":   ev_name,
                        "event_key":    ev_key,
                        "round":        round_str,
                        "session_num":  session_num,
                        "session_name": session_name,
                        "day":          day,
                        "est_start":    est_start,
                    }

                    if ev_num not in results:
                        results[ev_num] = []
                    # Avoid duplicates (same event/round)
                    existing = [
                        e for e in results[ev_num]
                        if e["round"] == round_str
                        and e["session_num"] == session_num
                    ]
                    if not existing:
                        results[ev_num].append(entry)

    return results


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_prelim_schedule(parsed, event_num):
    """Return the Prelims schedule entry for an event, or None."""
    for entry in parsed.get(event_num, []):
        if entry["round"] == "Prelims":
            return entry
    return None


def get_finals_schedule(parsed, event_num):
    """Return the Finals schedule entry for an event, or None."""
    for entry in parsed.get(event_num, []):
        if entry["round"] == "Finals":
            return entry
    return None


def get_event_schedule(parsed, event_num):
    """
    Return the most useful schedule entry for an event.
    Prefers Prelims if it exists, else Finals.
    """
    return (
        get_prelim_schedule(parsed, event_num)
        or get_finals_schedule(parsed, event_num)
    )


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("── Time formatter tests ────────────────────────────────────")
    time_tests = [
        ("09:39 AM", "9:39 AM"),
        ("11:06 AM", "11:06 AM"),
        ("05:00 PM", "5:00 PM"),
        ("12:12 PM", "12:12 PM"),
    ]
    all_pass = True
    for raw, expected in time_tests:
        got = _fmt_time(raw)
        ok  = got == expected
        print(f"  {raw!r:<14} → {got!r:<14}  {'✓' if ok else '✗'}")
        if not ok:
            all_pass = False

    print("\n── Live PDF test ───────────────────────────────────────────")
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None

    if pdf_path:
        parsed = parse_schedule(pdf_path)
        total  = sum(len(v) for v in parsed.values())
        print(f"  Events parsed : {len(parsed)} unique event numbers")
        print(f"  Total entries : {total} (prelims + finals combined)")

        # Show a sample of Friday prelim events
        print("\n  Sample — Friday Prelims events 9, 18, 44, 45, 48:")
        for num in [9, 18, 44, 45, 48]:
            prelim = get_prelim_schedule(parsed, num)
            finals = get_finals_schedule(parsed, num)
            if prelim:
                print(f"    Ev {num:>3}  PRELIM  "
                      f"{prelim['session_name']:<20}"
                      f"  Day {prelim['day']}"
                      f"  {prelim['est_start']}")
            if finals:
                print(f"    Ev {num:>3}  FINALS  "
                      f"{finals['session_name']:<20}"
                      f"  Day {finals['day']}"
                      f"  {finals['est_start']}")

        # Verify specific known times from the PDF
        print("\n  Verification against known times:")
        checks = [
            (18, "Prelims", "9:39 AM"),
            (44, "Prelims", "10:50 AM"),
            (45, "Finals",  "10:54 AM"),
            (48, "Finals",  "11:17 AM"),
        ]
        for ev_num, round_str, expected_time in checks:
            entries = parsed.get(ev_num, [])
            match   = next(
                (e for e in entries if e["round"] == round_str), None
            )
            got = match["est_start"] if match else "NOT FOUND"
            ok  = got == expected_time
            print(f"    Ev {ev_num:>3} {round_str:<8}"
                  f"  expected={expected_time:<10}"
                  f"  got={got:<10}  {'✓' if ok else '✗'}")
            if not ok:
                all_pass = False

    else:
        print("  No PDF given — pass path as argument.")
        print("  Usage: python schedule_parser.py path/to/timeline.pdf")

    print()
    print("All tests passed ✓" if all_pass else "FAILURES above ✗")