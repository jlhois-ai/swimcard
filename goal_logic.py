from __future__ import annotations
"""
goal_logic.py
=============
Tier classification and goal calculation for each swimmer/event pairing.

UPDATE v2:
  - Accepts finals_size and consolation_tiers from MeetConfig
  - Aware of format_type (PRELIM_FINALS, SEEDED_SPLIT, PURE_TIMED)
  - PURE_TIMED events get no goal (single swim, no advancement)
  - SEEDED_SPLIT events get a modified goal label
  - Tier boundaries calculated dynamically from finals_size

Tier structure (Senior, finals_size=8, consolation_tiers=3):
  Rank 1        → Top Seed
  Ranks 2–8     → Championship Finals  (goal: beat #1)
  Ranks 9–16    → Consolation B Finals (goal: beat #8)
  Ranks 17–24   → Consolation C Finals (goal: beat #16)
  Ranks 25–32   → Bonus Finals         (goal: beat #24)
  Outside 32    → Outside Finals       (goal: beat #32)

Age Group (finals_size=8, consolation_tiers=0):
  Rank 1        → Top Seed
  Ranks 2–8     → Championship Finals
  Outside 8     → Outside Finals
"""

from dataclasses import dataclass
from typing import Optional


# ── Tier configuration ────────────────────────────────────────────────────────

def build_tiers(finals_size=8, consolation_tiers=0):
    """
    Build a tier list dynamically from finals_size and consolation_tiers.

    Returns list of (rank_lo, rank_hi, label, css_class, comp_rank):
      comp_rank = rank of swimmer to beat to advance to next tier
                  None for top seed (no goal needed)
    """
    tiers = [
        (1, 1, "★ Top Seed", "tier-1", None),
        (2, finals_size, "Championship Finals", "tier-a", 1),
    ]

    consolation_labels = [
        ("Consolation B Finals", "tier-b"),
        ("Consolation C Finals", "tier-c"),
        ("Bonus Finals",         "tier-d"),
    ]

    for i in range(consolation_tiers):
        lo        = finals_size * (i + 1) + 1
        hi        = finals_size * (i + 2)
        comp_rank = finals_size * (i + 1)   # beat the last qualifier of tier above
        label, css = consolation_labels[i]
        tiers.append((lo, hi, label, css, comp_rank))

    return tiers


def get_tier(rank, finals_size=8, consolation_tiers=0):
    """
    Return (label, css_class, comp_rank) for a given rank.
    comp_rank is the rank to beat to advance — None for top seed.
    """
    tiers = build_tiers(finals_size, consolation_tiers)

    for lo, hi, label, css, comp_rank in tiers:
        if lo <= rank <= hi:
            return label, css, comp_rank

    # Outside all tiers
    total_spots = finals_size * (1 + consolation_tiers)
    return "Outside Finals", "tier-out", total_spots


# ── Time helpers ──────────────────────────────────────────────────────────────

def time_to_seconds(t):
    """Convert 'm:ss.ff' or 'ss.ff' or 'NT' to float seconds (NT → 0.0)."""
    t = t.strip()
    if t in ("NT", "DQ", ""):
        return 0.0
    if ":" in t:
        mins, rest = t.split(":", 1)
        return int(mins) * 60 + float(rest)
    return float(t)


def seconds_to_time(s):
    """Convert float seconds to 'm:ss.ff' or 'ss.ff' string."""
    if s < 60:
        return f"{s:.2f}"
    mins = int(s) // 60
    secs = s - mins * 60
    return f"{mins}:{secs:05.2f}"


def calc_drop(seed_time, result_time):
    """
    Calculate time drop from seed to result.
    Returns float seconds — negative means faster (good), positive = slower.
    Returns None if either time is invalid.
    """
    seed_s   = time_to_seconds(seed_time)
    result_s = time_to_seconds(result_time)
    if seed_s == 0 or result_s == 0:
        return None
    return result_s - seed_s


def format_drop(drop_seconds):
    """
    Format a time drop for display.
    drop_seconds < 0 → "▼ 0.57s drop"   (faster — good)
    drop_seconds > 0 → "▲ 0.18s slower" (slower — bad)
    drop_seconds = 0 → "even"
    """
    if drop_seconds is None:
        return ""
    if abs(drop_seconds) < 0.005:
        return "even"
    if drop_seconds < 0:
        return f"▼ {abs(drop_seconds):.2f}s drop"
    return f"▲ {drop_seconds:.2f}s slower"


# ── Goal data structures ──────────────────────────────────────────────────────

@dataclass
class GoalInfo:
    comp_rank:   int
    comp_name:   str
    comp_time:   str
    gap_seconds: float
    gap_str:     str       # e.g. "+2.10s"
    target_time: str       # same as comp_time
    reach_label: str       # e.g. "reach Championship Finals"


@dataclass
class EventResult:
    event_num:    int
    event_name:   str
    event_key:    str
    gender:       str
    rank:         int
    seed_time:    str
    tier_label:   str
    tier_css:     str
    format_type:  str       # PRELIM_FINALS | SEEDED_SPLIT | PURE_TIMED
    is_top_seed:  bool
    goal:         Optional[GoalInfo]
    data_warning: Optional[str] = None


# ── Reach label map ───────────────────────────────────────────────────────────

def _reach_label(tier_css, format_type):
    """
    What does the swimmer reach by beating the comp?
    Adjusted for format type.
    """
    base = {
        "tier-a":   "reach Top Seed",
        "tier-b":   "reach Championship Finals",
        "tier-c":   "reach Consolation B Finals",
        "tier-d":   "reach Consolation C Finals",
        "tier-out": "reach Bonus Finals",
    }.get(tier_css, "advance")

    if format_type == "SEEDED_SPLIT":
        return base + " (finals heat)"

    return base


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_swimmer(
    events,
    last_name,
    first_name,
    gender,
    finals_size=8,
    consolation_tiers=0,
):
    """
    Build a list of EventResult objects for a swimmer across all events.

    Parameters
    ----------
    events            : dict from psych_parser.parse_pdf()
    last_name         : str
    first_name        : str
    gender            : "Men" | "Women"
    finals_size       : int  Championship finals size (default 8)
    consolation_tiers : int  Number of consolation tiers (default 0)

    Returns
    -------
    list of EventResult, sorted by event number
    """
    last    = last_name.lower().strip()
    first   = first_name.lower().strip()
    results = []

    for num, ev in sorted(events.items()):
        if ev["is_relay"] or ev["gender"] != gender:
            continue

        # Find this swimmer
        swimmer = None
        for s in ev["swimmers"]:
            if last in s["name"].lower() and first in s["name"].lower():
                swimmer = s
                break
        if swimmer is None:
            continue

        rank        = swimmer["rank"]
        format_type = ev.get("format_type", "PRELIM_FINALS")
        tier_label, tier_css, comp_rank = get_tier(
            rank, finals_size, consolation_tiers
        )
        is_top_seed = (rank == 1)

        # PURE_TIMED — no goal, single swim
        if format_type == "PURE_TIMED":
            results.append(EventResult(
                event_num    = num,
                event_name   = ev["name"],
                event_key    = ev.get("event_key", ""),
                gender       = gender,
                rank         = rank,
                seed_time    = swimmer["seed_time"],
                tier_label   = "Timed Final",
                tier_css     = "tier-timed",
                format_type  = format_type,
                is_top_seed  = False,
                goal         = None,
            ))
            continue

        # Build goal info for PRELIM_FINALS and SEEDED_SPLIT
        goal = None
        if not is_top_seed and comp_rank is not None:
            by_rank      = {s["rank"]: s for s in ev["swimmers"]}
            swimmer_secs = time_to_seconds(swimmer["seed_time"])

            comp = by_rank.get(comp_rank)

            # Anomaly check — comp time implausibly fast (< 80% of swimmer's)
            if comp and swimmer_secs > 0:
                comp_secs = time_to_seconds(comp["seed_time"])
                if comp_secs > 0 and comp_secs / swimmer_secs < 0.80:
                    comp = None
                    for r in sorted(by_rank.keys()):
                        cand      = by_rank[r]
                        cand_secs = time_to_seconds(cand["seed_time"])
                        if cand_secs > 0 and cand_secs / swimmer_secs >= 0.80:
                            comp = cand
                            break

            if comp:
                comp_secs = time_to_seconds(comp["seed_time"])
                gap       = swimmer_secs - comp_secs
                gap_str   = f"+{gap:.2f}s" if gap >= 0 else f"{gap:.2f}s"

                goal = GoalInfo(
                    comp_rank   = comp["rank"],
                    comp_name   = comp["name"],
                    comp_time   = comp["seed_time"],
                    gap_seconds = gap,
                    gap_str     = gap_str,
                    target_time = comp["seed_time"],
                    reach_label = _reach_label(tier_css, format_type),
                )

        results.append(EventResult(
            event_num   = num,
            event_name  = ev["name"],
            event_key   = ev.get("event_key", ""),
            gender      = gender,
            rank        = rank,
            seed_time   = swimmer["seed_time"],
            tier_label  = tier_label,
            tier_css    = tier_css,
            format_type = format_type,
            is_top_seed = is_top_seed,
            goal        = goal,
        ))

    return results


# ── Footer summary ────────────────────────────────────────────────────────────

def build_footer_summary(results):
    """
    Build a compact footer string summarising event count and finals breakdown.
    Top Seed events count toward Championship Finals total.
    e.g. "6 Events · Championship Finals: 4 · Consolation B: 2"
    """
    from collections import Counter
    counts = Counter(r.tier_label for r in results)

    # Fold Top Seed into Championship Finals
    champ = counts.get("Championship Finals", 0) + counts.get("★ Top Seed", 0)
    if champ:
        counts["Championship Finals"] = champ
    counts.pop("★ Top Seed", None)

    display_order = [
        "Championship Finals",
        "Consolation B Finals",
        "Consolation C Finals",
        "Bonus Finals",
        "Outside Finals",
        "Timed Final",
    ]

    parts = [f"{len(results)} Event{'s' if len(results) != 1 else ''}"]
    for tier in display_order:
        if counts.get(tier):
            parts.append(f"{tier}: {counts[tier]}")

    return "  ·  ".join(parts)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("── Tier builder tests ──────────────────────────────────────")

    # Age group: top 8, no consolation
    tiers_ag = build_tiers(finals_size=8, consolation_tiers=0)
    label, css, comp = get_tier(1,  8, 0)
    assert label == "★ Top Seed",          f"Wrong: {label}"
    assert comp  is None,                  f"Wrong comp: {comp}"
    label, css, comp = get_tier(5,  8, 0)
    assert label == "Championship Finals", f"Wrong: {label}"
    assert comp  == 1,                     f"Wrong comp: {comp}"
    label, css, comp = get_tier(9,  8, 0)
    assert label == "Outside Finals",      f"Wrong: {label}"
    assert comp  == 8,                     f"Wrong comp: {comp}"
    print("  Age group tiers (top 8, no consolation)  ✓")

    # Senior: top 8, 3 consolation tiers
    label, css, comp = get_tier(1,  8, 3)
    assert label == "★ Top Seed",            f"Wrong: {label}"
    label, css, comp = get_tier(8,  8, 3)
    assert label == "Championship Finals",   f"Wrong: {label}"
    label, css, comp = get_tier(9,  8, 3)
    assert label == "Consolation B Finals",  f"Wrong: {label}"
    assert comp  == 8,                       f"Wrong comp: {comp}"
    label, css, comp = get_tier(16, 8, 3)
    assert label == "Consolation B Finals",  f"Wrong: {label}"
    label, css, comp = get_tier(17, 8, 3)
    assert label == "Consolation C Finals",  f"Wrong: {label}"
    assert comp  == 16,                      f"Wrong comp: {comp}"
    label, css, comp = get_tier(25, 8, 3)
    assert label == "Bonus Finals",          f"Wrong: {label}"
    assert comp  == 24,                      f"Wrong comp: {comp}"
    label, css, comp = get_tier(33, 8, 3)
    assert label == "Outside Finals",        f"Wrong: {label}"
    assert comp  == 32,                      f"Wrong comp: {comp}"
    print("  Senior tiers (top 8, 3 consolation)       ✓")

    print("\n── Time helper tests ───────────────────────────────────────")
    assert time_to_seconds("1:23.45") == 83.45,  "m:ss.ff failed"
    assert time_to_seconds("56.07")   == 56.07,  "ss.ff failed"
    assert time_to_seconds("NT")      == 0.0,    "NT failed"
    assert seconds_to_time(83.45)     == "1:23.45", "to_time failed"
    assert seconds_to_time(56.07)     == "56.07",   "to_time short failed"
    print("  time_to_seconds / seconds_to_time          ✓")

    assert format_drop(-0.57) == "▼ 0.57s drop",   f"drop format: {format_drop(-0.57)}"
    assert format_drop(0.18)  == "▲ 0.18s slower", f"drop format: {format_drop(0.18)}"
    assert format_drop(0.0)   == "even",            f"drop format: {format_drop(0.0)}"
    print("  format_drop                                ✓")

    print("\n── Footer summary test ─────────────────────────────────────")
    mock = [
        EventResult(1,"100 Back","100_back","Women",1,"56.07",
                    "★ Top Seed","tier-1","PRELIM_FINALS",True,None),
        EventResult(2,"200 Back","200_back","Women",3,"2:00.65",
                    "Championship Finals","tier-a","PRELIM_FINALS",False,None),
        EventResult(3,"1650 Free","1650_free","Women",5,"17:16.82",
                    "Timed Final","tier-timed","PURE_TIMED",False,None),
    ]
    summary = build_footer_summary(mock)
    assert "3 Events"              in summary, f"Wrong summary: {summary}"
    assert "Championship Finals: 2" in summary, f"Wrong summary: {summary}"
    assert "Timed Final: 1"         in summary, f"Wrong summary: {summary}"
    print(f"  Footer: {summary}  ✓")

    print("\nAll tests passed ✓")