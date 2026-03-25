from __future__ import annotations
"""
vision_extractor.py
===================
Uses the Claude API to extract structured data from screenshots and
image-based PDFs that cannot be parsed with pdfplumber.

Three extraction modes:
  1. HEAT ASSIGNMENT  — screenshot from Meet Mobile showing heat/lane
  2. RESULTS         — screenshot from Meet Mobile showing swim result
  3. GOALS PDF       — image-based SwimCloud goals PDF (like Brayleigh's)

All extractions return a dict for the confirmation screen.
The user must confirm before data is written to the meet file.

Requires:
  pip install anthropic
  ANTHROPIC_API_KEY set in environment or Streamlit secrets
"""

import base64
import json
import os
import re

try:
    import anthropic
except ImportError:
    raise ImportError("anthropic required: pip install anthropic")

from meet_file import GoalsData


# ── Model ─────────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"   # vision-capable model


# ── Image loader ──────────────────────────────────────────────────────────────

def _load_image_b64(path):
    """Load an image or PDF file as base64 string."""
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _media_type(path):
    """Infer media type from file extension."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif":  "image/gif",
    }.get(ext, "image/png")


def _image_block(path):
    """Build an Anthropic API image content block from a file path."""
    return {
        "type": "image",
        "source": {
            "type":       "base64",
            "media_type": _media_type(path),
            "data":       _load_image_b64(path),
        },
    }


def _image_block_from_bytes(data, media_type="image/png"):
    """Build an Anthropic API image content block from raw bytes."""
    return {
        "type": "image",
        "source": {
            "type":       "base64",
            "media_type": media_type,
            "data":       base64.standard_b64encode(data).decode("utf-8"),
        },
    }


# ── API client ────────────────────────────────────────────────────────────────

def _get_client(api_key=None):
    """
    Return an Anthropic client.
    API key is read from:
      1. api_key parameter (passed explicitly)
      2. ANTHROPIC_API_KEY environment variable
      3. Streamlit secrets (when running inside Streamlit)
    """
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    # Try environment variable
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return anthropic.Anthropic(api_key=env_key)

    # Try Streamlit secrets (only available when running inside Streamlit)
    try:
        import streamlit as st
        return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    except Exception:
        pass

    raise ValueError(
        "No Anthropic API key found. Set ANTHROPIC_API_KEY environment "
        "variable or add it to Streamlit secrets."
    )


def _call_api(client, prompt, image_blocks):
    """Make a single Claude API call with image(s) and return response text."""
    content = image_blocks + [{"type": "text", "text": prompt}]
    message = client.messages.create(
        model      = MODEL,
        max_tokens = 1024,
        messages   = [{"role": "user", "content": content}],
    )
    return message.content[0].text


# ── JSON response parser ──────────────────────────────────────────────────────

def _parse_json_response(text):
    """
    Extract and parse a JSON object from Claude's response text.
    Claude sometimes wraps JSON in markdown code fences — strip them.
    """
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object within the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse JSON from response:\n{text}")


# ── Mode 1: Heat assignment extraction ────────────────────────────────────────

HEAT_PROMPT = """You are extracting heat and lane assignments from a swim meet screenshot.

Swimmer to find: {first_name} {last_name}

Look through this screenshot from Meet Mobile or a heat sheet.
Find the row for {first_name} {last_name} and extract their heat number and lane number.

Respond ONLY with a JSON object in exactly this format:
{{
  "found": true,
  "swimmer_name": "exact name as shown",
  "event_name": "event name as shown",
  "round": "Prelims" or "Finals",
  "heat": 2,
  "lane": 4
}}

If the swimmer is not found in the screenshot, respond with:
{{
  "found": false,
  "reason": "brief explanation"
}}

No other text. JSON only."""


def extract_heat_assignment(
    image_path_or_bytes,
    last_name,
    first_name,
    api_key=None,
    media_type="image/png",
):
    """
    Extract heat and lane assignment for a swimmer from a screenshot.

    Parameters
    ----------
    image_path_or_bytes : str | bytes
        File path or raw image bytes.
    last_name  : str
    first_name : str
    api_key    : str | None
    media_type : str  Used only if image_path_or_bytes is bytes.

    Returns
    -------
    dict with keys: found, swimmer_name, event_name, round, heat, lane
         OR:        found=False, reason
    """
    client = _get_client(api_key)
    prompt = HEAT_PROMPT.format(first_name=first_name, last_name=last_name)

    if isinstance(image_path_or_bytes, (str, os.PathLike)):
        img_block = _image_block(str(image_path_or_bytes))
    else:
        img_block = _image_block_from_bytes(image_path_or_bytes, media_type)

    response = _call_api(client, prompt, [img_block])
    return _parse_json_response(response)


# ── Mode 2: Results extraction ────────────────────────────────────────────────

RESULTS_PROMPT = """You are extracting swim meet results from a screenshot.

Swimmer to find: {first_name} {last_name}

Look through this screenshot from Meet Mobile showing results.
Find the result for {first_name} {last_name} and extract their data.

Respond ONLY with a JSON object in exactly this format:
{{
  "found": true,
  "swimmer_name": "exact name as shown",
  "event_name": "event name as shown",
  "round": "Prelims" or "Finals",
  "official_time": "1:23.45",
  "place_in_heat": 2,
  "dq": false
}}

If the swimmer was disqualified, set "dq": true and "official_time": "DQ".
If the swimmer did not swim (scratch/NS), set "found": false with reason.
If the swimmer is not found, respond with:
{{
  "found": false,
  "reason": "brief explanation"
}}

No other text. JSON only."""


def extract_result(
    image_path_or_bytes,
    last_name,
    first_name,
    api_key=None,
    media_type="image/png",
):
    """
    Extract swim result for a swimmer from a results screenshot.

    Returns
    -------
    dict with keys: found, swimmer_name, event_name, round,
                    official_time, place_in_heat, dq
         OR:        found=False, reason
    """
    client = _get_client(api_key)
    prompt = RESULTS_PROMPT.format(first_name=first_name, last_name=last_name)

    if isinstance(image_path_or_bytes, (str, os.PathLike)):
        img_block = _image_block(str(image_path_or_bytes))
    else:
        img_block = _image_block_from_bytes(image_path_or_bytes, media_type)

    response = _call_api(client, prompt, [img_block])
    return _parse_json_response(response)


# ── Mode 3: Goals PDF extraction ──────────────────────────────────────────────

GOALS_PROMPT = """You are extracting swimming cut times and goals from a SwimCloud goals PDF.

This PDF shows a swimmer's personal bests and cut time targets for each event.

For each event you can see, extract:
- Event name (e.g. "50 Free", "100 Back", "400 IM")
- Personal best time
- Current cut achieved (e.g. "Sectionals", "Futures") — may be blank
- Next cut target (e.g. "Futures", "Winter Jr's")
- Next cut target time
- Drop needed in seconds
- Percentage to next goal

Respond ONLY with a JSON object in exactly this format:
{{
  "events": [
    {{
      "event_name": "50 Free",
      "personal_best": "24.24",
      "current_cut": "Sectionals",
      "next_cut": "Futures",
      "next_cut_time": "23.89",
      "drop_needed": 0.35,
      "pct_to_goal": 98.5
    }}
  ]
}}

If current_cut is not shown (swimmer has not achieved any cut for that event),
use an empty string "" for current_cut.

No other text. JSON only."""


def extract_goals_pdf(
    pdf_path_or_bytes,
    api_key=None,
    media_type="application/pdf",
):
    """
    Extract goals data from an image-based SwimCloud goals PDF.

    Use this when goals_parser.py raises ValueError (image-based PDF).

    Returns
    -------
    dict { canonical_event_key: GoalsData }
    """
    from event_normalizer import normalize_event_key

    client = _get_client(api_key)

    if isinstance(pdf_path_or_bytes, (str, os.PathLike)):
        img_block = _image_block(str(pdf_path_or_bytes))
    else:
        img_block = _image_block_from_bytes(pdf_path_or_bytes, media_type)

    response = _call_api(client, GOALS_PROMPT, [img_block])
    data     = _parse_json_response(response)

    result = {}
    for ev in data.get("events", []):
        key = normalize_event_key(ev.get("event_name", ""))
        if not key:
            continue
        result[key] = GoalsData(
            personal_best = ev.get("personal_best", ""),
            current_cut   = ev.get("current_cut", ""),
            next_cut      = ev.get("next_cut", ""),
            next_cut_time = ev.get("next_cut_time", ""),
            drop_needed   = float(ev.get("drop_needed", 0)),
            pct_to_goal   = float(ev.get("pct_to_goal", 0)),
        )

    return result


# ── Convenience: auto-route goals PDF ─────────────────────────────────────────

def load_goals(pdf_path, api_key=None):
    """
    Load goals from a SwimCloud PDF — automatically routes to text parser
    or vision extractor depending on whether the PDF has extractable text.

    Parameters
    ----------
    pdf_path : str  Path to the SwimCloud goals PDF.
    api_key  : str | None

    Returns
    -------
    dict { canonical_event_key: GoalsData }
    """
    from goals_parser import parse_goals_pdf

    try:
        return parse_goals_pdf(pdf_path)
    except ValueError:
        # Image-based PDF — use vision
        return extract_goals_pdf(pdf_path, api_key=api_key)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("── Import and structure tests ──────────────────────────────")

    # Verify all functions are importable and callable
    assert callable(extract_heat_assignment), "extract_heat_assignment missing"
    assert callable(extract_result),          "extract_result missing"
    assert callable(extract_goals_pdf),       "extract_goals_pdf missing"
    assert callable(load_goals),              "load_goals missing"
    print("  All functions importable              ✓")

    # Verify prompt templates have correct placeholders
    assert "{first_name}" in HEAT_PROMPT,    "HEAT_PROMPT missing {first_name}"
    assert "{last_name}"  in HEAT_PROMPT,    "HEAT_PROMPT missing {last_name}"
    assert "{first_name}" in RESULTS_PROMPT, "RESULTS_PROMPT missing {first_name}"
    assert "{last_name}"  in RESULTS_PROMPT, "RESULTS_PROMPT missing {last_name}"
    print("  Prompt templates valid                ✓")

    # Verify JSON parser handles markdown fences
    raw = '```json\n{"found": true, "heat": 2, "lane": 4}\n```'
    parsed = _parse_json_response(raw)
    assert parsed["heat"] == 2, "JSON parser failed on markdown fences"
    print("  JSON parser strips markdown fences    ✓")

    # Verify JSON parser handles plain JSON
    raw2 = '{"found": false, "reason": "not found"}'
    parsed2 = _parse_json_response(raw2)
    assert parsed2["found"] is False, "JSON parser failed on plain JSON"
    print("  JSON parser handles plain JSON        ✓")

    # Verify media type detection
    assert _media_type("photo.jpg")  == "image/jpeg"
    assert _media_type("scan.pdf")   == "application/pdf"
    assert _media_type("shot.png")   == "image/png"
    print("  Media type detection                  ✓")

    # Live API test (only if API key is available)
    print("\n── Live API test ───────────────────────────────────────────")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ANTHROPIC_API_KEY not set — skipping live test.")
        print("  Set it to test: export ANTHROPIC_API_KEY=your_key_here")
    else:
        pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
        if pdf_path:
            print(f"  Testing goals extraction on: {pdf_path}")
            try:
                goals = load_goals(pdf_path, api_key=api_key)
                print(f"  Events extracted: {len(goals)}")
                for key, g in sorted(goals.items()):
                    print(f"    {key:<15}  PB={g.personal_best:<10}"
                          f"  cut={g.current_cut:<12}"
                          f"  next={g.next_cut}")
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print("  No PDF path given.")
            print("  Usage: python vision_extractor.py path/to/goals.pdf")

    print("\nAll structural tests passed ✓")