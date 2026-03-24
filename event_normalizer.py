from __future__ import annotations
"""
event_normalizer.py
Maps event names from all sources to a canonical key.
Format: "{distance}_{stroke}"  e.g. 100_back, 400_im, 1650_free
"""

import re

_STROKE_ALIASES = {
    "free": "free", "freestyle": "free", "fr": "free",
    "back": "back", "backstroke": "back", "bk": "back",
    "breast": "breast", "breaststroke": "breast", "br": "breast",
    "fly": "fly", "butterfly": "fly", "fl": "fly",
    "im": "im", "medley": "im", "individual": None,
}

_DISTANCE_ALIASES = {
    "50": "50", "100": "100", "200": "200", "400": "400",
    "500": "500", "800": "800", "1000": "1000",
    "1500": "1500", "1650": "1650",
}

_RELAY_MULT_RE = re.compile(r"\b4\s*[xX×]\s*(\d+)\b")

_NOISE_WORDS = frozenset({
    "yard", "yards", "meter", "meters", "metre", "metres",
    "event", "women", "men", "boys", "girls", "mixed",
    "year", "olds", "old", "age", "group",
    "open", "senior", "junior", "masters", "a", "b", "c", "d",
})


def _normalize_encoding(text):
    text = text.replace("\u03b2", "f")
    text = text.replace("\ufb00", "ff")
    return text


def normalize_event_key(raw_name):
    if not raw_name or not raw_name.strip():
        return None

    text = _normalize_encoding(raw_name)
    text = text.lower().replace(",", "").strip()

    is_relay    = "relay" in text
    relay_match = _RELAY_MULT_RE.search(text)
    relay_leg   = relay_match.group(1) if relay_match else None

    tokens    = re.split(r"[\s\-/]+", text)
    distances = []
    stroke    = None

    for tok in tokens:
        tok = tok.strip(".,")
        if tok in _NOISE_WORDS or tok == "relay":
            continue
        if tok in _DISTANCE_ALIASES:
            distances.append(_DISTANCE_ALIASES[tok])
            continue
        if tok in _STROKE_ALIASES:
            mapped = _STROKE_ALIASES[tok]
            if mapped is not None:
                if tok == "medley" and is_relay:
                    stroke = "medley"
                else:
                    stroke = mapped

    if relay_match and relay_leg:
        distance = relay_leg
    elif distances:
        distance = distances[-1]
    else:
        return None

    if stroke is None:
        if "medley" in text and is_relay:
            stroke = "medley"
        else:
            return None

    if is_relay:
        return f"4x{distance}_{stroke}_relay"
    return f"{distance}_{stroke}"


def is_relay_name(raw_name):
    if not raw_name:
        return False
    return "relay" in _normalize_encoding(raw_name).lower()


def canonical_to_display(key):
    if not key:
        return key
    parts = key.split("_")
    if "relay" in parts:
        return f"{parts[0]} {parts[1].title()} Relay"
    distance = parts[0]
    stroke   = "_".join(parts[1:])
    stroke_display = "IM" if stroke == "im" else stroke.title()
    return f"{distance} {stroke_display}"


SCY_INDIVIDUAL_EVENTS = [
    "50_free",  "100_free",  "200_free",  "500_free",
    "1000_free","1650_free",
    "50_back",  "100_back",  "200_back",
    "50_breast","100_breast","200_breast",
    "50_fly",   "100_fly",   "200_fly",
    "200_im",   "400_im",
]

LCM_INDIVIDUAL_EVENTS = [
    "50_free",  "100_free",  "200_free",  "400_free",
    "800_free", "1500_free",
    "50_back",  "100_back",  "200_back",
    "50_breast","100_breast","200_breast",
    "50_fly",   "100_fly",   "200_fly",
    "200_im",   "400_im",
]

SCM_INDIVIDUAL_EVENTS = LCM_INDIVIDUAL_EVENTS


if __name__ == "__main__":
    TEST_CASES = [
        ("Event 14 Women 100 Yard Backstroke",   "100_back"),
        ("Event 33 Women 1650 Yard Freestyle",   "1650_free"),
        ("Boys 14 Year Olds 200 Yard Butterfly", "200_fly"),
        ("Women 400 Individual Medley",          "400_im"),
        ("Mixed 4x100 Medley Relay",             "4x100_medley_relay"),
        ("50 Free",                              "50_free"),
        ("100 Back",                             "100_back"),
        ("400 IM",                               "400_im"),
        ("1,650 Free",                           "1650_free"),
        ("100 backstroke",                       "100_back"),
        ("200 butterfly",                        "200_fly"),
        ("",                                     None),
    ]

    print(f"{'Input':<45}  {'Expected':<25}  {'Got':<25}  OK?")
    print("─" * 105)
    all_pass = True
    for raw, expected in TEST_CASES:
        got = normalize_event_key(raw)
        ok  = "✓" if got == expected else "✗"
        if got != expected:
            all_pass = False
        print(f"{raw!r:<45}  {str(expected):<25}  {str(got):<25}  {ok}")

    print()
    print("All tests passed ✓" if all_pass else "FAILURES above ✗")