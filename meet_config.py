from __future__ import annotations
"""
meet_config.py
==============
MeetConfig dataclass — stores all settings for one meet.
Created once during Phase 0 and saved into the meet JSON file.

Settings:
  course          : SCY | LCM | SCM
  meet_type       : age_group | senior
  finals_size     : 8 (always — configurable consolation tiers vary)
  consolation_tiers: 0 (age group) | 1–3 (senior, B/C/D)
  event_formats   : dict mapping canonical event key → format type
                    auto-filled from course defaults, overridable per event
"""

from dataclasses import dataclass, field
from enum import Enum

from event_normalizer import (
    SCY_INDIVIDUAL_EVENTS,
    LCM_INDIVIDUAL_EVENTS,
    SCM_INDIVIDUAL_EVENTS,
)


# ── Enums ─────────────────────────────────────────────────────────────────────

class Course(str, Enum):
    SCY = "SCY"   # Short Course Yards  (25 yards  — USA standard)
    LCM = "LCM"   # Long Course Meters  (50 meters — Olympic)
    SCM = "SCM"   # Short Course Meters (25 meters — international)


class MeetType(str, Enum):
    AGE_GROUP = "age_group"   # Top 8 finals only, no consolation
    SENIOR    = "senior"      # Top 8 + configurable consolation tiers


class EventFormat(str, Enum):
    PRELIM_FINALS = "PRELIM_FINALS"   # Swim twice — prelim to qualify, finals for time
    SEEDED_SPLIT  = "SEEDED_SPLIT"    # Swim once — fast heat vs slow heat, all times count
    PURE_TIMED    = "PURE_TIMED"      # Swim once — no advancement, just your time


# ── Default event formats by course ──────────────────────────────────────────
# Everything is PRELIM_FINALS except the long distance free events.
# SEEDED_SPLIT is never a default — manual override only.

_SCY_PURE_TIMED = {"1000_free", "1650_free"}
_LCM_PURE_TIMED = {"800_free", "1500_free"}


def _build_default_formats(course: Course) -> dict:
    """
    Return a dict of {event_key: EventFormat} for every individual event
    in the given course, using blueprint-specified defaults.
    """
    if course == Course.SCY:
        events    = SCY_INDIVIDUAL_EVENTS
        pure_timed = _SCY_PURE_TIMED
    elif course == Course.LCM:
        events    = LCM_INDIVIDUAL_EVENTS
        pure_timed = _LCM_PURE_TIMED
    else:  # SCM
        events    = SCM_INDIVIDUAL_EVENTS
        pure_timed = _LCM_PURE_TIMED   # SCM uses same pure-timed events as LCM

    return {
        ev: (EventFormat.PURE_TIMED if ev in pure_timed else EventFormat.PRELIM_FINALS)
        for ev in events
    }


# ── MeetConfig dataclass ──────────────────────────────────────────────────────

@dataclass
class MeetConfig:
    """
    All configuration for one meet. Created once, saved in the meet JSON file.

    Parameters
    ----------
    course             : Course enum
    meet_type          : MeetType enum
    consolation_tiers  : int  0 = age group (no consolation)
                              1 = B finals only
                              2 = B + C finals
                              3 = B + C + D finals
    event_formats      : dict  Auto-filled from defaults. Override per event as needed.
    meet_name          : str   Display name e.g. "2026 NW Spring Speedo Sectionals"
    meet_dates         : str   Display dates e.g. "Mar 12–15, 2026"
    """
    course            : Course
    meet_type         : MeetType
    consolation_tiers : int             = 0
    event_formats     : dict            = field(default_factory=dict)
    meet_name         : str             = ""
    meet_dates        : str             = ""

    def __post_init__(self):
        # Auto-fill event formats from course defaults if not provided
        if not self.event_formats:
            self.event_formats = _build_default_formats(self.course)

        # Age group never has consolation tiers
        if self.meet_type == MeetType.AGE_GROUP:
            self.consolation_tiers = 0

        # Cap consolation tiers at 3 (B, C, D)
        self.consolation_tiers = min(self.consolation_tiers, 3)

    @property
    def finals_size(self) -> int:
        """Championship finals always top 8."""
        return 8

    @property
    def total_finals_spots(self) -> int:
        """Total ranked spots including all consolation tiers."""
        return self.finals_size * (1 + self.consolation_tiers)

    def get_format(self, event_key: str) -> EventFormat:
        """Return the format for a specific event key."""
        return self.event_formats.get(event_key, EventFormat.PRELIM_FINALS)

    def set_format(self, event_key: str, fmt: EventFormat):
        """Override the format for a specific event."""
        self.event_formats[event_key] = fmt

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return {
            "course":             self.course.value,
            "meet_type":          self.meet_type.value,
            "consolation_tiers":  self.consolation_tiers,
            "meet_name":          self.meet_name,
            "meet_dates":         self.meet_dates,
            "event_format_overrides": {
                k: v.value
                for k, v in self.event_formats.items()
                if v != (EventFormat.PURE_TIMED
                         if k in (_SCY_PURE_TIMED | _LCM_PURE_TIMED)
                         else EventFormat.PRELIM_FINALS)
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> MeetConfig:
        """Reconstruct from a plain dict loaded from JSON."""
        course    = Course(d["course"])
        meet_type = MeetType(d["meet_type"])
        config    = cls(
            course            = course,
            meet_type         = meet_type,
            consolation_tiers = d.get("consolation_tiers", 0),
            meet_name         = d.get("meet_name", ""),
            meet_dates        = d.get("meet_dates", ""),
        )
        # Apply any per-event overrides saved in the file
        for key, fmt_str in d.get("event_format_overrides", {}).items():
            config.set_format(key, EventFormat(fmt_str))
        return config


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("── SCY Senior config ───────────────────────────────────────")
    cfg = MeetConfig(
        course            = Course.SCY,
        meet_type         = MeetType.SENIOR,
        consolation_tiers = 3,
        meet_name         = "2026 NW Spring Speedo Sectionals",
        meet_dates        = "Mar 12–15, 2026",
    )
    print(f"Course          : {cfg.course.value}")
    print(f"Meet type       : {cfg.meet_type.value}")
    print(f"Finals size     : {cfg.finals_size}")
    print(f"Consolation     : {cfg.consolation_tiers} tier(s)")
    print(f"Total spots     : {cfg.total_finals_spots}")
    print(f"1650 free format: {cfg.get_format('1650_free').value}")
    print(f"100 back format : {cfg.get_format('100_back').value}")

    print("\n── Age group config (no consolation) ───────────────────────")
    cfg2 = MeetConfig(
        course            = Course.SCY,
        meet_type         = MeetType.AGE_GROUP,
        consolation_tiers = 3,   # should be forced to 0
    )
    print(f"Consolation tiers (should be 0): {cfg2.consolation_tiers}")

    print("\n── Override 500 free to SEEDED_SPLIT ───────────────────────")
    cfg.set_format("500_free", EventFormat.SEEDED_SPLIT)
    print(f"500 free format : {cfg.get_format('500_free').value}")

    print("\n── Round-trip to/from dict ─────────────────────────────────")
    d      = cfg.to_dict()
    cfg3   = MeetConfig.from_dict(d)
    print(f"500 free after round-trip: {cfg3.get_format('500_free').value}")
    print(f"1650 free after round-trip: {cfg3.get_format('1650_free').value}")

    print("\n── LCM config ──────────────────────────────────────────────")
    cfg4 = MeetConfig(course=Course.LCM, meet_type=MeetType.SENIOR)
    print(f"800 free format : {cfg4.get_format('800_free').value}")
    print(f"1500 free format: {cfg4.get_format('1500_free').value}")
    print(f"200 fly format  : {cfg4.get_format('200_fly').value}")

    print("\nAll checks complete ✓")