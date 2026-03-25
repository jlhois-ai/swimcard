from __future__ import annotations
"""
card_template.py
================
Generates the HTML card string for a swimmer.

Renders all progressive phases:
  Phase 0 — seed rank, tier badge, goal, schedule, goals progress bar
  Phase 1 — heat and lane assignment
  Phase 2 — prelim result, time drop, finals qualification
  Phase 3 — finals heat and lane
  Phase 4 — finals result, locked indicator

Design:
  480px wide dark card (on-screen / PNG)
  White printer-friendly version (CSS @media print)
  Women accent: #00c8ff (cyan)
  Men accent:   #ff6b35 (orange-red)
"""

from goal_logic import EventResult, build_footer_summary
from meet_file  import EventState, HeatInfo, ResultInfo, GoalsData


# ── Accent colors ─────────────────────────────────────────────────────────────

ACCENT = {
    "Women": "#00c8ff",
    "Men":   "#ff6b35",
}


# ── Tier display labels for card template ─────────────────────────────────────

REACH_LABEL = {
    "tier-a":   "Beat to become Top Seed",
    "tier-b":   "Beat to reach Championship Finals",
    "tier-c":   "Beat to reach Consolation B Finals",
    "tier-d":   "Beat to reach Consolation C Finals",
    "tier-out": "Beat to enter Bonus Finals",
}


# ── Goals progress bar ────────────────────────────────────────────────────────

def _goals_bar(goals_data):
    """Render the goals progress bar section."""
    if not goals_data:
        return ""

    pct   = min(max(goals_data.pct_to_goal, 0), 100)
    cut   = goals_data.current_cut or "No cut yet"
    nxt   = goals_data.next_cut
    drop  = goals_data.drop_needed
    tgt   = goals_data.next_cut_time

    # Bar fill color based on percentage
    if pct >= 99:
        bar_color = "#ffd700"   # gold — very close
    elif pct >= 95:
        bar_color = "#64dc82"   # green
    else:
        bar_color = "#00c8ff"   # cyan

    return f"""
    <div class="goals-bar-section">
      <div class="goals-bar-header">
        <span class="goals-current">{cut}</span>
        <span class="goals-next">Next: {nxt} ({tgt})</span>
      </div>
      <div class="goals-bar-track">
        <div class="goals-bar-fill" style="width:{pct:.1f}%;background:{bar_color};"></div>
      </div>
      <div class="goals-bar-footer">
        <span class="goals-drop">drop {drop:.2f}s needed</span>
        <span class="goals-pct">{pct:.1f}% to goal</span>
      </div>
    </div>"""


# ── Heat line ─────────────────────────────────────────────────────────────────

def _heat_line(heat_info, schedule_info, round_label="Prelims"):
    """Render heat + lane + estimated time line."""
    if not heat_info:
        return ""

    heat_str = f"Heat {heat_info.heat} · Lane {heat_info.lane}"

    schedule_str = ""
    if schedule_info:
        schedule_str = (
            f" · {schedule_info.session} ~{schedule_info.est_start}"
        )

    return f"""
    <div class="heat-line">
      <span class="heat-icon">🏊</span>
      <span class="heat-text">{round_label}: {heat_str}{schedule_str}</span>
    </div>"""


# ── Result block ──────────────────────────────────────────────────────────────

def _result_block(result_info, round_label, seed_time, finals_qualified=None):
    """Render a prelim or finals result block."""
    if not result_info:
        return ""

    time_str  = result_info.time
    place_str = f"{result_info.place_in_heat}{_ordinal(result_info.place_in_heat)} in heat"

    # Time drop display
    drop = result_info.drop_from_seed
    if drop is None:
        drop_str = ""
    elif abs(drop) < 0.005:
        drop_str = "even"
    elif drop < 0:
        drop_str = f"▼ {abs(drop):.2f}s drop"
    else:
        drop_str = f"▲ {drop:.2f}s slower"

    # Finals qualification badge (prelim only)
    qual_html = ""
    if finals_qualified is True:
        qual_html = '<span class="qual-badge">FINALS ✓</span>'
    elif finals_qualified is False:
        qual_html = '<span class="no-qual-badge">Did not advance</span>'

    return f"""
    <div class="result-block">
      <div class="result-label">{round_label.upper()} RESULT</div>
      <div class="result-row">
        <span class="result-time">{time_str}</span>
        <span class="result-drop">{drop_str}</span>
        <span class="result-place">{place_str}</span>
        {qual_html}
      </div>
    </div>"""


def _ordinal(n):
    """1 → 'st', 2 → 'nd', 3 → 'rd', 4+ → 'th'"""
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ── Event block ───────────────────────────────────────────────────────────────

def _event_block_from_state(
    result:        EventResult,
    state:         EventState,
    prelim_sched=  None,
    finals_sched=  None,
):
    """
    Build a complete event block HTML combining psych data + live state.

    Parameters
    ----------
    result       : EventResult   from goal_logic.analyze_swimmer()
    state        : EventState    from meet_file (heat, results, lock status)
    prelim_sched : ScheduleInfo | None
    finals_sched : ScheduleInfo | None
    """
    tier_css = result.tier_css

    # Badge label
    if result.is_top_seed:
        badge_label = "★ Top Seed"
    elif tier_css == "tier-timed":
        badge_label = "Timed Final"
    elif tier_css == "tier-seeded":
        badge_label = f"#{result.rank} Seed"
    else:
        badge_label = f"#{result.rank} Seed"

    # Goal detail
    goal_html = ""
    if not result.is_top_seed and result.goal and tier_css != "tier-timed":
        g          = result.goal
        reach_text = REACH_LABEL.get(tier_css, "Beat to advance")
        suffix     = " (finals heat)" if result.format_type == "SEEDED_SPLIT" else ""
        goal_html  = f"""
    <div class="goal-detail">
      <div class="goal-text-{tier_css.replace('tier-', '')}">
        vs {g.comp_name} &mdash; #{g.comp_rank} seed<br>{reach_text}{suffix}
      </div>
      <div class="goal-numbers">
        <div class="goal-diff">{g.gap_str} behind</div>
        <div class="goal-target">Need: {g.target_time} or faster</div>
      </div>
    </div>"""

    # Goals progress bar
    goals_bar_html = _goals_bar(
        state.goals_data if state else None
    )

    # Format type note (pure timed / seeded split)
    format_note = ""
    if result.format_type == "PURE_TIMED":
        format_note = '<div class="format-note">Timed Final · single swim · all times count</div>'
    elif result.format_type == "SEEDED_SPLIT":
        format_note = '<div class="format-note">Seeded Split · morning times count for awards</div>'

    # Prelim heat line
    prelim_heat_html = ""
    if state and state.prelim_heat:
        prelim_heat_html = _heat_line(state.prelim_heat, prelim_sched, "Prelims")

    # Prelim result
    prelim_result_html = ""
    if state and state.prelim_result:
        fq = state.prelim_result.finals_qualified if result.format_type == "PRELIM_FINALS" else None
        prelim_result_html = _result_block(
            state.prelim_result, "Prelim", result.seed_time, fq
        )

    # Finals heat line
    finals_heat_html = ""
    if state and state.finals_heat:
        finals_heat_html = _heat_line(state.finals_heat, finals_sched, "Finals")

    # Finals result
    finals_result_html = ""
    if state and state.finals_result:
        finals_result_html = _result_block(
            state.finals_result, "Finals", result.seed_time
        )

    # Lock indicator
    lock_html = ""
    if state and state.locked:
        lock_html = '<div class="lock-badge">🔒 DONE</div>'

    return f"""
  <div class="event-block{'  event-locked' if (state and state.locked) else ''}">
    <div class="event-top-row">
      <div class="event-name">
        <span class="event-num">Ev {result.event_num}</span>
        {result.event_name}
      </div>
      <div class="seed-time">{result.seed_time}</div>
      <div class="rank-badge {tier_css}">{badge_label}</div>
    </div>
    {goal_html}
    {goals_bar_html}
    {format_note}
    {prelim_heat_html}
    {prelim_result_html}
    {finals_heat_html}
    {finals_result_html}
    {lock_html}
  </div>"""


def _event_block_simple(result: EventResult):
    """
    Simplified event block — psych data only, no live state.
    Used when rendering a base card before any meet file exists.
    """
    return _event_block_from_state(result, None)


# ── Full card HTML ────────────────────────────────────────────────────────────

def build_card_html(
    last_name:   str,
    first_name:  str,
    age:         int,
    team:        str,
    gender:      str,
    meet_name:   str,
    meet_dates:  str,
    results:     list,
    states:      dict = None,     # { event_key: EventState }
    sched_map:   dict = None,     # { event_num: {"prelim": ScheduleInfo, "finals": ScheduleInfo} }
) -> str:
    """
    Return a complete standalone HTML card string.

    Parameters
    ----------
    last_name   : str
    first_name  : str
    age         : int
    team        : str
    gender      : str
    meet_name   : str
    meet_dates  : str
    results     : list of EventResult
    states      : dict { event_key: EventState } — optional, for live phases
    sched_map   : dict { event_num: {"prelim": ScheduleInfo|None,
                                     "finals": ScheduleInfo|None} }
    """
    accent      = ACCENT.get(gender, "#00c8ff")
    footer_text = build_footer_summary(results)
    full_name   = f"{first_name} {last_name}"
    meta_line   = f"Age {age} · {team} · {gender}'s Events"

    states   = states   or {}
    sched_map= sched_map or {}

    event_blocks = ""
    for r in results:
        state        = states.get(r.event_key)
        sched        = sched_map.get(r.event_num, {})
        prelim_sched = sched.get("prelim")
        finals_sched = sched.get("finals")
        event_blocks += _event_block_from_state(
            r, state, prelim_sched, finals_sched
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  /* ── On-screen dark card ── */
  body {{
    background: #0a0e1a;
    font-family: Arial, sans-serif;
    padding: 20px;
    width: 480px;
  }}

  .card {{
    background: linear-gradient(145deg, #0d1526, #111d35);
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }}

  /* ── Header ── */
  .header {{
    padding: 16px 20px 14px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    position: relative;
    overflow: hidden;
    background: linear-gradient(135deg, #0d1526, #0a1830);
  }}
  .header::before {{
    content: '';
    position: absolute;
    top: -40px; right: -40px;
    width: 150px; height: 150px;
    border-radius: 50%;
    background: {accent};
    opacity: 0.05;
  }}
  .meet-label {{
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: {accent};
    margin-bottom: 5px;
  }}
  .athlete-name {{
    font-size: 28px;
    font-weight: 900;
    color: #ffffff;
    line-height: 1;
    letter-spacing: 0.3px;
  }}
  .athlete-meta {{
    font-size: 12px;
    color: #ffffff;
    margin-top: 5px;
  }}

  /* ── Event blocks ── */
  .event-block {{
    border-bottom: 1px solid rgba(255,255,255,0.05);
    padding: 10px 20px;
  }}
  .event-block:last-child {{ border-bottom: none; }}
  .event-locked {{ opacity: 0.85; }}

  .event-top-row {{
    display: grid;
    grid-template-columns: 1fr 80px 100px;
    align-items: center;
  }}
  .event-name {{
    font-size: 12px;
    font-weight: 700;
    color: rgba(255,255,255,0.8);
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }}
  .event-num {{
    font-size: 10px;
    color: rgba(255,255,255,0.5);
    font-weight: 400;
    display: block;
    margin-bottom: 1px;
  }}
  .seed-time {{
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    text-align: center;
  }}

  /* ── Badges ── */
  .rank-badge {{
    font-size: 11px;
    font-weight: 800;
    padding: 3px 8px;
    border-radius: 6px;
    text-align: center;
  }}
  .tier-1      {{ background: rgba(255,215,0,0.18);   color: #ffd700; }}
  .tier-a      {{ background: rgba(0,200,255,0.13);   color: #00c8ff; }}
  .tier-b      {{ background: rgba(100,220,130,0.13); color: #64dc82; }}
  .tier-c      {{ background: rgba(255,165,0,0.13);   color: #ffa500; }}
  .tier-d      {{ background: rgba(255,100,100,0.13); color: #ff8080; }}
  .tier-out    {{ background: rgba(210,40,40,0.15);   color: #e05555; }}
  .tier-timed  {{ background: rgba(150,150,150,0.15); color: #aaaaaa; }}
  .tier-seeded {{ background: rgba(160,80,220,0.15);  color: #c080ff; }}

  /* ── Goal detail ── */
  .goal-detail {{
    margin-top: 6px;
    padding-top: 5px;
    border-top: 1px solid rgba(255,255,255,0.04);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}
  .goal-text-a   {{ font-size: 10px; line-height: 1.4; color: #00c8ff; }}
  .goal-text-b   {{ font-size: 10px; line-height: 1.4; color: #64dc82; }}
  .goal-text-c   {{ font-size: 10px; line-height: 1.4; color: #ffa500; }}
  .goal-text-d   {{ font-size: 10px; line-height: 1.4; color: #ff8080; }}
  .goal-text-out {{ font-size: 10px; line-height: 1.4; color: #e05555; }}
  .goal-numbers  {{ text-align: right; }}
  .goal-diff     {{ font-size: 11px; font-weight: 700; color: #ffd700; }}
  .goal-target   {{ font-size: 10px; color: #ffd700; margin-top: 1px; }}

  /* ── Goals progress bar ── */
  .goals-bar-section {{
    margin-top: 6px;
    padding-top: 5px;
    border-top: 1px solid rgba(255,255,255,0.04);
  }}
  .goals-bar-header {{
    display: flex;
    justify-content: space-between;
    margin-bottom: 3px;
  }}
  .goals-current {{
    font-size: 9px;
    font-weight: 700;
    color: #64dc82;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .goals-next {{
    font-size: 9px;
    color: rgba(255,255,255,0.5);
  }}
  .goals-bar-track {{
    height: 4px;
    background: rgba(255,255,255,0.08);
    border-radius: 2px;
    overflow: hidden;
  }}
  .goals-bar-fill {{
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
  }}
  .goals-bar-footer {{
    display: flex;
    justify-content: space-between;
    margin-top: 2px;
  }}
  .goals-drop {{ font-size: 9px; color: rgba(255,255,255,0.4); }}
  .goals-pct  {{ font-size: 9px; color: rgba(255,255,255,0.4); }}

  /* ── Format note ── */
  .format-note {{
    font-size: 9px;
    color: rgba(255,255,255,0.35);
    margin-top: 4px;
    font-style: italic;
  }}

  /* ── Heat line ── */
  .heat-line {{
    margin-top: 6px;
    padding-top: 5px;
    border-top: 1px solid rgba(255,255,255,0.04);
    display: flex;
    align-items: center;
    gap: 5px;
  }}
  .heat-icon {{ font-size: 10px; }}
  .heat-text {{
    font-size: 10px;
    color: {accent};
    font-weight: 600;
  }}

  /* ── Result block ── */
  .result-block {{
    margin-top: 6px;
    padding: 6px 8px;
    background: rgba(255,255,255,0.03);
    border-radius: 6px;
    border-left: 2px solid {accent};
  }}
  .result-label {{
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 1.5px;
    color: rgba(255,255,255,0.4);
    margin-bottom: 3px;
  }}
  .result-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .result-time  {{
    font-size: 16px;
    font-weight: 900;
    color: #ffffff;
  }}
  .result-drop  {{
    font-size: 11px;
    font-weight: 700;
    color: #64dc82;
  }}
  .result-place {{
    font-size: 10px;
    color: rgba(255,255,255,0.5);
  }}
  .qual-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #64dc82;
    background: rgba(100,220,130,0.12);
    padding: 2px 6px;
    border-radius: 4px;
  }}
  .no-qual-badge {{
    font-size: 10px;
    color: rgba(255,255,255,0.3);
  }}

  /* ── Lock badge ── */
  .lock-badge {{
    margin-top: 5px;
    font-size: 9px;
    font-weight: 700;
    color: rgba(255,255,255,0.3);
    text-align: right;
    letter-spacing: 1px;
  }}

  /* ── Footer ── */
  .footer {{
    padding: 10px 20px;
    border-top: 1px solid rgba(255,255,255,0.07);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .footer-left  {{ font-size: 10px; color: #ffffff; line-height: 1.5; }}
  .footer-right {{ font-size: 10px; color: #ffffff; }}

  /* ── Printer-friendly PDF version ── */
  @media print {{
    body {{
      background: #ffffff;
      padding: 0;
      width: 100%;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #dddddd;
      box-shadow: none;
      border-radius: 8px;
    }}
    .header {{
      background: #ffffff;
      border-bottom: 2px solid #333333;
    }}
    .header::before {{ display: none; }}
    .meet-label     {{ color: #333333; }}
    .athlete-name   {{ color: #000000; }}
    .athlete-meta   {{ color: #333333; }}
    .event-name     {{ color: #000000; }}
    .event-num      {{ color: #666666; }}
    .seed-time      {{ color: #000000; }}
    .event-block    {{ border-bottom: 1px solid #eeeeee; }}

    /* Keep badge accent colors in print */
    .tier-1      {{ background: rgba(255,215,0,0.15);   color: #b8960a; }}
    .tier-a      {{ background: rgba(0,150,200,0.12);   color: #007aaa; }}
    .tier-b      {{ background: rgba(50,160,80,0.12);   color: #2a7a40; }}
    .tier-c      {{ background: rgba(200,120,0,0.12);   color: #8a5200; }}
    .tier-d      {{ background: rgba(200,60,60,0.12);   color: #8a2020; }}
    .tier-out    {{ background: rgba(160,30,30,0.12);   color: #6a1010; }}
    .tier-timed  {{ background: rgba(100,100,100,0.12); color: #555555; }}
    .tier-seeded {{ background: rgba(120,50,180,0.12);  color: #6a2aaa; }}

    .goal-text-a   {{ color: #007aaa; }}
    .goal-text-b   {{ color: #2a7a40; }}
    .goal-text-c   {{ color: #8a5200; }}
    .goal-text-d   {{ color: #8a2020; }}
    .goal-text-out {{ color: #6a1010; }}
    .goal-diff     {{ color: #8a6800; }}
    .goal-target   {{ color: #8a6800; }}

    .goals-bar-track {{ background: #eeeeee; }}
    .goals-current   {{ color: #2a7a40; }}
    .goals-next      {{ color: #666666; }}
    .goals-drop      {{ color: #999999; }}
    .goals-pct       {{ color: #999999; }}
    .format-note     {{ color: #999999; }}

    .heat-text     {{ color: #007aaa; }}
    .result-block  {{
      background: #f8f8f8;
      border-left: 2px solid #007aaa;
    }}
    .result-label  {{ color: #999999; }}
    .result-time   {{ color: #000000; }}
    .result-drop   {{ color: #2a7a40; }}
    .result-place  {{ color: #666666; }}
    .qual-badge    {{ color: #2a7a40; background: rgba(50,160,80,0.1); }}
    .no-qual-badge {{ color: #999999; }}
    .lock-badge    {{ color: #cccccc; }}

    .footer-left  {{ color: #333333; }}
    .footer-right {{ color: #333333; }}
  }}
</style>
</head>
<body>
<div class="card">

  <div class="header">
    <div class="meet-label">{meet_name}</div>
    <div class="athlete-name">{full_name}</div>
    <div class="athlete-meta">{meta_line}</div>
  </div>

{event_blocks}

  <div class="footer">
    <div class="footer-left">{footer_text}</div>
    <div class="footer-right">{meet_dates}</div>
  </div>

</div>
</body>
</html>
"""


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from goal_logic import EventResult, GoalInfo
    from meet_file  import EventState, HeatInfo, ResultInfo, GoalsData, ScheduleInfo

    print("── Build test card ─────────────────────────────────────────")

    # Mock results
    goal_100back = GoalInfo(
        comp_rank=1, comp_name="Smith, Jane",
        comp_time="55.50", gap_seconds=0.57,
        gap_str="+0.57s", target_time="55.50",
        reach_label="reach Top Seed",
    )
    results = [
        EventResult(
            event_num=18, event_name="Women 100 Yard Backstroke",
            event_key="100_back", gender="Women",
            rank=3, seed_time="56.07",
            tier_label="Championship Finals", tier_css="tier-a",
            format_type="PRELIM_FINALS", is_top_seed=False,
            goal=goal_100back,
        ),
        EventResult(
            event_num=33, event_name="Women 1650 Yard Freestyle",
            event_key="1650_free", gender="Women",
            rank=5, seed_time="17:16.82",
            tier_label="Timed Final", tier_css="tier-timed",
            format_type="PURE_TIMED", is_top_seed=False,
            goal=None,
        ),
    ]

    # Mock states — 100 back has full Phase 0-4 data
    states = {
        "100_back": EventState(
            event_key   = "100_back",
            event_num   = 18,
            event_name  = "Women 100 Yard Backstroke",
            seed_rank   = 3,
            seed_time   = "56.07",
            tier        = "tier-a",
            format_type = "PRELIM_FINALS",
            goals_data  = GoalsData(
                personal_best = "56.07",
                current_cut   = "Futures",
                next_cut      = "Winter Jr's",
                next_cut_time = "55.09",
                drop_needed   = 0.98,
                pct_to_goal   = 98.2,
            ),
            schedule    = ScheduleInfo(
                session   = "Friday Prelims",
                day       = 2,
                est_start = "9:39 AM",
            ),
            prelim_heat = HeatInfo(heat=2, lane=4),
            prelim_result = ResultInfo(
                time           = "55.89",
                place_in_heat  = 1,
                drop_from_seed = -0.18,
                finals_qualified = True,
                uploaded       = "2026-03-20T09:52:00",
            ),
            finals_heat = HeatInfo(heat=1, lane=4),
            finals_result = ResultInfo(
                time           = "55.41",
                place_in_heat  = 2,
                drop_from_seed = -0.66,
                finals_qualified = False,
                uploaded       = "2026-03-20T17:44:00",
            ),
            locked = True,
        ),
    }

    html = build_card_html(
        last_name  = "Hoisington",
        first_name = "Brayleigh",
        age        = 16,
        team       = "Sawtooth Aquatic Club",
        gender     = "Women",
        meet_name  = "2026 NW Spring Speedo Sectionals · Boise · Mar 12-15",
        meet_dates = "Mar 12-15, 2026",
        results    = results,
        states     = states,
    )

    # Write test card to file
    out_path = "/tmp/test_card.html"
    with open(out_path, "w") as f:
        f.write(html)

    print(f"  Card HTML written to {out_path}")
    print(f"  HTML length: {len(html)} chars")
    assert "<div class=\"card\">"    in html, "Missing card div"
    assert "Brayleigh Hoisington"   in html, "Missing swimmer name"
    assert "55.89"                  in html, "Missing prelim result"
    assert "55.41"                  in html, "Missing finals result"
    assert "🔒 DONE"                in html, "Missing lock badge"
    assert "98.2% to goal"          in html, "Missing progress bar"
    assert "Heat 2 · Lane 4"        in html, "Missing heat line"
    assert "Timed Final"            in html, "Missing timed final badge"
    assert "@media print"           in html, "Missing print CSS"
    print("  All assertions passed              ✓")
    print(f"\n  Open in browser to preview:")
    print(f"  open {out_path}")
    print("\nAll tests passed ✓")