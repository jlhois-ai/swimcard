from __future__ import annotations
"""
meet_file.py
============
Save, load, and update the meet JSON state file.

One file per swimmer per meet, e.g. "Sectionals_2026_Brayleigh_Hoisington.json"
This file is the single source of truth for all meet data.
It is downloaded to the user's device after every phase and re-uploaded
at the next session to restore state. No database needed.
"""

import json
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from meet_config import MeetConfig, Course, MeetType, EventFormat


# ── Event state dataclass ─────────────────────────────────────────────────────

@dataclass
class HeatInfo:
    heat: int
    lane: int


@dataclass
class ResultInfo:
    time:            str
    place_in_heat:   int
    drop_from_seed:  float        # negative = faster than seed (good)
    finals_qualified: bool = False
    uploaded:        str   = ""   # ISO timestamp


@dataclass
class GoalsData:
    personal_best:  str
    current_cut:    str           # e.g. "Sectionals"
    next_cut:       str           # e.g. "Futures"
    next_cut_time:  str           # e.g. "23.89"
    drop_needed:    float         # seconds to drop
    pct_to_goal:    float         # e.g. 98.5


@dataclass
class ScheduleInfo:
    session:    str               # e.g. "Friday Prelims"
    day:        int               # 1-indexed day of meet
    est_start:  str               # e.g. "9:39 AM"


@dataclass
class EventState:
    # Core psych sheet data (set during Phase 0, never changes)
    event_key:    str             # canonical key e.g. "100_back"
    event_num:    int
    event_name:   str
    seed_rank:    int
    seed_time:    str
    tier:         str             # e.g. "tier-a"
    format_type:  str             # PRELIM_FINALS | SEEDED_SPLIT | PURE_TIMED

    # Goal data (set during Phase 0)
    goal:         Optional[dict]  = None

    # Schedule data (set during Phase 0 if timeline uploaded)
    schedule:     Optional[ScheduleInfo] = None

    # Goals PDF data (set during Phase 0 if goals PDF uploaded)
    goals_data:   Optional[GoalsData]   = None

    # Heat assignments (set during Phase 1 / Phase 3)
    prelim_heat:  Optional[HeatInfo]    = None
    finals_heat:  Optional[HeatInfo]    = None

    # Results (set during Phase 2 / Phase 4)
    prelim_result: Optional[ResultInfo] = None
    finals_result: Optional[ResultInfo] = None

    # Lock status — True after finals result confirmed
    locked:       bool = False

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON storage."""
        def _dc(obj):
            if obj is None:
                return None
            if hasattr(obj, "__dataclass_fields__"):
                return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
            return obj

        return {
            "event_key":     self.event_key,
            "event_num":     self.event_num,
            "event_name":    self.event_name,
            "seed_rank":     self.seed_rank,
            "seed_time":     self.seed_time,
            "tier":          self.tier,
            "format_type":   self.format_type,
            "goal":          self.goal,
            "schedule":      _dc(self.schedule),
            "goals_data":    _dc(self.goals_data),
            "prelim_heat":   _dc(self.prelim_heat),
            "finals_heat":   _dc(self.finals_heat),
            "prelim_result": _dc(self.prelim_result),
            "finals_result": _dc(self.finals_result),
            "locked":        self.locked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EventState:
        """Reconstruct from a plain dict loaded from JSON."""
        def _load(klass, data):
            if data is None:
                return None
            return klass(**data)

        return cls(
            event_key    = d["event_key"],
            event_num    = d["event_num"],
            event_name   = d["event_name"],
            seed_rank    = d["seed_rank"],
            seed_time    = d["seed_time"],
            tier         = d["tier"],
            format_type  = d["format_type"],
            goal         = d.get("goal"),
            schedule     = _load(ScheduleInfo,  d.get("schedule")),
            goals_data   = _load(GoalsData,     d.get("goals_data")),
            prelim_heat  = _load(HeatInfo,      d.get("prelim_heat")),
            finals_heat  = _load(HeatInfo,      d.get("finals_heat")),
            prelim_result= _load(ResultInfo,    d.get("prelim_result")),
            finals_result= _load(ResultInfo,    d.get("finals_result")),
            locked       = d.get("locked", False),
        )


# ── MeetFile dataclass ────────────────────────────────────────────────────────

@dataclass
class MeetFile:
    """
    Complete state for one swimmer at one meet.
    Serializes to / from a single JSON file.
    """
    # Swimmer identity
    swimmer_last:  str
    swimmer_first: str
    gender:        str
    age:           int
    team:          str

    # Meet configuration
    config:        MeetConfig

    # All events keyed by canonical event key
    events:        dict = field(default_factory=dict)   # {event_key: EventState}

    # Timestamps
    created:       str  = ""
    last_updated:  str  = ""

    def __post_init__(self):
        if not self.created:
            self.created = _now()
        if not self.last_updated:
            self.last_updated = _now()

    # ── Event access ──────────────────────────────────────────────────────────

    def get_event(self, event_key: str) -> Optional[EventState]:
        return self.events.get(event_key)

    def set_event(self, event_key: str, state: EventState):
        self.events[event_key] = state
        self.last_updated = _now()

    def all_events_locked(self) -> bool:
        return bool(self.events) and all(e.locked for e in self.events.values())

    def events_needing_prelim_heat(self) -> list:
        return [
            e for e in self.events.values()
            if not e.locked and e.prelim_heat is None
            and e.format_type != "PURE_TIMED"
        ]

    def events_needing_results(self) -> list:
        return [
            e for e in self.events.values()
            if not e.locked and e.prelim_result is None
        ]

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "meta": {
                "swimmer_last":  self.swimmer_last,
                "swimmer_first": self.swimmer_first,
                "gender":        self.gender,
                "age":           self.age,
                "team":          self.team,
                "created":       self.created,
                "last_updated":  self.last_updated,
            },
            "meet_config": self.config.to_dict(),
            "events": {
                k: v.to_dict() for k, v in self.events.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> MeetFile:
        meta   = d["meta"]
        config = MeetConfig.from_dict(d["meet_config"])
        mf     = cls(
            swimmer_last  = meta["swimmer_last"],
            swimmer_first = meta["swimmer_first"],
            gender        = meta["gender"],
            age           = meta.get("age", 0),
            team          = meta.get("team", ""),
            config        = config,
            created       = meta.get("created", ""),
            last_updated  = meta.get("last_updated", ""),
        )
        for key, ev_dict in d.get("events", {}).items():
            mf.events[key] = EventState.from_dict(ev_dict)
        return mf

    # ── File I/O ──────────────────────────────────────────────────────────────

    def save(self, path: str):
        """Write meet file to disk as JSON."""
        self.last_updated = _now()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def to_json_bytes(self) -> bytes:
        """Return JSON as bytes — for Streamlit download button."""
        self.last_updated = _now()
        return json.dumps(self.to_dict(), indent=2).encode("utf-8")

    @classmethod
    def load(cls, path: str) -> MeetFile:
        """Load meet file from disk."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_json_bytes(cls, data: bytes) -> MeetFile:
        """Load meet file from uploaded bytes — for Streamlit file uploader."""
        return cls.from_dict(json.loads(data.decode("utf-8")))

    # ── Filename helper ───────────────────────────────────────────────────────

    def default_filename(self) -> str:
        """
        Generate a clean filename for this meet file.
        e.g. "Sectionals_2026_Brayleigh_Hoisington.json"
        """
        safe = lambda s: "".join(c if c.isalnum() else "_" for c in s)
        meet = safe(self.config.meet_name) if self.config.meet_name else "Meet"
        return f"{meet}_{safe(self.swimmer_first)}_{safe(self.swimmer_last)}.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("── Create meet file ────────────────────────────────────────")
    config = MeetConfig(
        course            = Course.SCY,
        meet_type         = MeetType.SENIOR,
        consolation_tiers = 3,
        meet_name         = "Sectionals 2026",
        meet_dates        = "Mar 12-15, 2026",
    )
    mf = MeetFile(
        swimmer_last  = "Hoisington",
        swimmer_first = "Brayleigh",
        gender        = "Women",
        age           = 16,
        team          = "Sawtooth Aquatic Club",
        config        = config,
    )
    print(f"Swimmer  : {mf.swimmer_first} {mf.swimmer_last}")
    print(f"Filename : {mf.default_filename()}")

    print("\n── Add an event ────────────────────────────────────────────")
    ev = EventState(
        event_key   = "100_back",
        event_num   = 14,
        event_name  = "Women 100 Yard Backstroke",
        seed_rank   = 3,
        seed_time   = "56.07",
        tier        = "tier-a",
        format_type = "PRELIM_FINALS",
    )
    mf.set_event("100_back", ev)
    print(f"Events stored : {list(mf.events.keys())}")

    print("\n── Add heat assignment ─────────────────────────────────────")
    mf.events["100_back"].prelim_heat = HeatInfo(heat=4, lane=5)
    print(f"Prelim heat  : {mf.events['100_back'].prelim_heat}")

    print("\n── Add prelim result ───────────────────────────────────────")
    mf.events["100_back"].prelim_result = ResultInfo(
        time            = "55.89",
        place_in_heat   = 1,
        drop_from_seed  = -0.18,
        finals_qualified= True,
        uploaded        = _now(),
    )
    print(f"Prelim result: {mf.events['100_back'].prelim_result.time}")
    print(f"Time drop    : {mf.events['100_back'].prelim_result.drop_from_seed}s")

    print("\n── All events locked? ──────────────────────────────────────")
    print(f"Locked (should be False): {mf.all_events_locked()}")
    mf.events["100_back"].locked = True
    print(f"Locked (should be True) : {mf.all_events_locked()}")

    print("\n── Save and reload from disk ───────────────────────────────")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, mf.default_filename())
        mf.save(path)
        mf2  = MeetFile.load(path)
        print(f"Reloaded swimmer : {mf2.swimmer_first} {mf2.swimmer_last}")
        print(f"Reloaded event   : {mf2.events['100_back'].event_name}")
        print(f"Reloaded result  : {mf2.events['100_back'].prelim_result.time}")
        print(f"Reloaded locked  : {mf2.events['100_back'].locked}")

    print("\n── Round-trip via JSON bytes (Streamlit path) ──────────────")
    raw   = mf.to_json_bytes()
    mf3   = MeetFile.from_json_bytes(raw)
    print(f"Bytes length     : {len(raw)}")
    print(f"Swimmer via bytes: {mf3.swimmer_first} {mf3.swimmer_last}")

    print("\nAll checks complete ✓")