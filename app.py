from __future__ import annotations
"""
app.py
======
SwimCard — Streamlit web app
Single swimmer, progressive meet card.

Flow:
  Phase 0  — Meet setup (one time)
  Update   — Repeating daily loop:
               Heats tab:   upload prelim or finals heat sheet (PDF or screenshot)
               Results tab: upload prelim or finals results (screenshot)
  Complete — All events locked → download final card
"""

import os
import tempfile
from datetime import datetime

import streamlit as st

from event_normalizer import normalize_event_key
from meet_config      import MeetConfig, Course, MeetType, EventFormat
from meet_file        import (
    MeetFile, EventState, HeatInfo, ResultInfo, ScheduleInfo
)
from psych_parser     import parse_pdf
from goal_logic       import (
    analyze_swimmer, calc_drop, format_drop, EventResult, GoalInfo, get_tier
)
from card_template    import build_card_html
from schedule_parser  import parse_schedule, get_prelim_schedule, get_finals_schedule
from heat_parser      import parse_heat_sheet, find_swimmer_heats
from vision_extractor import load_goals, extract_heat_assignment, extract_result


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SwimCard",
    page_icon="🏊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .main .block-container { max-width: 520px; padding: 1rem; }
  .stButton > button {
    width: 100%;
    border-radius: 10px;
    padding: 0.6rem 1rem;
    font-weight: 600;
  }
  .swimmer-bar {
    background: linear-gradient(135deg, #0d1526, #111d35);
    color: white;
    padding: 10px 16px;
    border-radius: 10px;
    margin-bottom: 1rem;
    font-size: 14px;
    font-weight: 700;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .info-box {
    background: #f0f4ff;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    margin: 8px 0;
  }
  .confirm-box {
    background: #fffbe6;
    border-left: 3px solid #ffc107;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 13px;
    margin: 8px 0;
  }
</style>
""", unsafe_allow_html=True)


# ── Password ──────────────────────────────────────────────────────────────────

def check_password():
    try:
        correct = st.secrets["passwords"]["app"]
    except Exception:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.markdown("## 🏊 SwimCard")
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pwd == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ── State helpers ─────────────────────────────────────────────────────────────

def _get(key, default=None):
    return st.session_state.get(key, default)

def _set(key, value):
    st.session_state[key] = value

def _clear():
    for k in [k for k in st.session_state if k != "authenticated"]:
        del st.session_state[k]

def _mf() -> MeetFile | None:
    return st.session_state.get("meet_file")

def _set_mf(mf):
    st.session_state["meet_file"] = mf


# ── API key helper ────────────────────────────────────────────────────────────

def _api_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


# ── Temp file helper ──────────────────────────────────────────────────────────

def _tmp_pdf(uploaded_file):
    """Write uploaded file to a temp path, return path. Caller must delete."""
    data = uploaded_file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        return f.name


# ── Download button ───────────────────────────────────────────────────────────

def _download(mf, label="📥 Download Meet File"):
    st.download_button(
        label=label,
        data=mf.to_json_bytes(),
        file_name=mf.default_filename(),
        mime="application/json",
        use_container_width=True,
    )


# ── Card preview ──────────────────────────────────────────────────────────────

def _preview(mf, results, sched_map):
    html = build_card_html(
        last_name  = mf.swimmer_last,
        first_name = mf.swimmer_first,
        age        = mf.age,
        team       = mf.team,
        gender     = mf.gender,
        meet_name  = mf.config.meet_name,
        meet_dates = mf.config.meet_dates,
        results    = results,
        states     = dict(mf.events),
        sched_map  = sched_map,
    )
    st.components.v1.html(html, height=len(results) * 160 + 200, scrolling=True)


# ── Swimmer header bar ────────────────────────────────────────────────────────

def _header(mf):
    locked = sum(1 for e in mf.events.values() if e.locked)
    total  = len(mf.events)
    st.markdown(
        f'<div class="swimmer-bar">'
        f'🏊 {mf.swimmer_first} {mf.swimmer_last} &nbsp;·&nbsp; '
        f'{mf.config.meet_name}'
        f'<span style="font-size:11px;opacity:0.7">'
        f'🔒 {locked}/{total}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Rebuild results from meet file ────────────────────────────────────────────

def _rebuild_results(mf):
    results = []
    for ev_key, ev_state in sorted(
        mf.events.items(), key=lambda x: x[1].event_num
    ):
        goal = None
        if ev_state.goal:
            g = ev_state.goal
            goal = GoalInfo(
                comp_rank   = g.get("comp_rank", 0),
                comp_name   = g.get("comp_name", ""),
                comp_time   = g.get("comp_time", ""),
                gap_seconds = 0,
                gap_str     = g.get("gap_str", ""),
                target_time = g.get("target_time", ""),
                reach_label = g.get("reach_label", ""),
            )
        tier_label, tier_css, _ = get_tier(
            ev_state.seed_rank,
            mf.config.finals_size,
            mf.config.consolation_tiers,
        )
        results.append(EventResult(
            event_num   = ev_state.event_num,
            event_name  = ev_state.event_name,
            event_key   = ev_key,
            gender      = mf.gender,
            rank        = ev_state.seed_rank,
            seed_time   = ev_state.seed_time,
            tier_label  = tier_label,
            tier_css    = ev_state.tier,
            format_type = ev_state.format_type,
            is_top_seed = (ev_state.seed_rank == 1),
            goal        = goal,
        ))
    return results


# ── Apply heat assignments from parsed heat sheet ─────────────────────────────

def _apply_heats(mf, found, round_filter=None):
    """
    Apply heat assignments to meet file.
    round_filter: "Prelims" | "Finals" | None (apply all)
    Returns count of events updated.
    """
    count = 0
    for h in found:
        if round_filter and h["round"] != round_filter:
            continue

        # Match by event_key first, then event_num fallback
        ev_key = h.get("event_key") or normalize_event_key(h["event_name"])
        if not ev_key or ev_key not in mf.events:
            ev_key = next(
                (k for k, v in mf.events.items()
                 if v.event_num == h["event_num"]),
                None
            )
        if not ev_key or ev_key not in mf.events:
            continue

        hi = HeatInfo(heat=h["heat"], lane=h["lane"])
        if h["round"] == "Finals":
            mf.events[ev_key].finals_heat = hi
        else:
            mf.events[ev_key].prelim_heat = hi
        count += 1
    return count


# ── Phase 0 — Meet setup ──────────────────────────────────────────────────────

def phase_0():
    st.markdown("## 🏊 SwimCard · New Meet")

    with st.form("setup_form"):
        st.markdown("**Swimmer**")
        c1, c2 = st.columns(2)
        with c1:
            first = st.text_input("First name", placeholder="Payson")
        with c2:
            last = st.text_input("Last name", placeholder="Johns")

        gender_sel = st.radio(
            "Gender", ["Girls / Women", "Boys / Men"], horizontal=True
        )
        gender_val = "Women" if gender_sel.startswith("Girls") else "Men"

        st.markdown("**Meet**")
        meet_name  = st.text_input("Meet name", placeholder="2026 MWAG Champs")
        meet_dates = st.text_input("Dates", placeholder="Mar 19–22, 2026")

        c3, c4 = st.columns(2)
        with c3:
            course_str = st.selectbox("Course", ["SCY", "LCM", "SCM"])
        with c4:
            type_str = st.selectbox("Meet type", ["Age Group", "Senior / Open"])

        consolation = 0
        if type_str == "Senior / Open":
            consolation = st.selectbox(
                "Consolation tiers", [0, 1, 2, 3],
                format_func=lambda x: {
                    0: "None", 1: "B finals",
                    2: "B + C finals", 3: "B + C + D finals"
                }[x],
            )

        st.markdown("**Psych Sheet PDF** *(required)*")
        psych_file = st.file_uploader("Psych sheet", type=["pdf"],
                                      key="psych_up")

        st.markdown("**Timeline PDF** *(optional)*")
        sched_file = st.file_uploader("Timeline / session report", type=["pdf"],
                                      key="sched_up")

        st.markdown("**Goals PDF** *(optional)*")
        goals_file = st.file_uploader("SwimCloud goals PDF", type=["pdf"],
                                      key="goals_up")

        submitted = st.form_submit_button("🏗️ Build Base Card",
                                          use_container_width=True)

    if not submitted:
        return

    if not first or not last:
        st.error("Enter swimmer name.")
        return
    if not psych_file:
        st.error("Upload psych sheet PDF.")
        return
    if not meet_name:
        st.error("Enter meet name.")
        return

    with st.spinner("Parsing psych sheet…"):
        config = MeetConfig(
            course            = Course(course_str),
            meet_type         = MeetType.AGE_GROUP if "Age" in type_str
                                else MeetType.SENIOR,
            consolation_tiers = consolation,
            meet_name         = meet_name,
            meet_dates        = meet_dates,
        )
        tmp = _tmp_pdf(psych_file)
        try:
            events = parse_pdf(tmp, meet_config=config)
        finally:
            os.unlink(tmp)

        results = analyze_swimmer(
            events, last, first, gender_val,
            finals_size       = config.finals_size,
            consolation_tiers = config.consolation_tiers,
        )

    if not results:
        st.error(
            f"Could not find **{first} {last}** in the psych sheet. "
            f"Check spelling."
        )
        return

    ev0 = events[results[0].event_num]
    s0  = next(
        s for s in ev0["swimmers"]
        if last.lower() in s["name"].lower()
        and first.lower() in s["name"].lower()
    )

    mf = MeetFile(
        swimmer_last  = last,
        swimmer_first = first,
        gender        = gender_val,
        age           = s0["age"],
        team          = s0["team"],
        config        = config,
    )

    for r in results:
        safe_key = r.event_key or f"event_{r.event_num}"
        mf.set_event(safe_key, EventState(
            event_key   = safe_key,
            event_num   = r.event_num,
            event_name  = r.event_name,
            seed_rank   = r.rank,
            seed_time   = r.seed_time,
            tier        = r.tier_css,
            format_type = r.format_type,
            goal        = {
                "comp_rank":   r.goal.comp_rank,
                "comp_name":   r.goal.comp_name,
                "comp_time":   r.goal.comp_time,
                "gap_str":     r.goal.gap_str,
                "target_time": r.goal.target_time,
                "reach_label": r.goal.reach_label,
            } if r.goal else None,
        ))

    # Parse schedule
    sched_map = {}
    if sched_file:
        with st.spinner("Parsing timeline…"):
            tmp = _tmp_pdf(sched_file)
            try:
                parsed_sched = parse_schedule(tmp)
            finally:
                os.unlink(tmp)
            for ev_key, ev_state in mf.events.items():
                p = get_prelim_schedule(parsed_sched, ev_state.event_num)
                f = get_finals_schedule(parsed_sched, ev_state.event_num)
                if p or f:
                    sched_map[ev_state.event_num] = {
                        "prelim": ScheduleInfo(
                            session=p["session_name"],
                            day=p["day"],
                            est_start=p["est_start"],
                        ) if p else None,
                        "finals": ScheduleInfo(
                            session=f["session_name"],
                            day=f["day"],
                            est_start=f["est_start"],
                        ) if f else None,
                    }
                    ev_state.schedule = sched_map[ev_state.event_num].get(
                        "prelim"
                    ) or sched_map[ev_state.event_num].get("finals")

    # Parse goals
    if goals_file:
        with st.spinner("Parsing goals PDF…"):
            tmp = _tmp_pdf(goals_file)
            try:
                goals_data = load_goals(tmp, api_key=_api_key())
            finally:
                os.unlink(tmp)
            for ev_key, gd in goals_data.items():
                if ev_key in mf.events:
                    mf.events[ev_key].goals_data = gd

    _set_mf(mf)
    _set("results",   results)
    _set("sched_map", sched_map)
    _set("screen",    "update")
    st.rerun()


# ── Update screen — daily loop ────────────────────────────────────────────────

def update_screen():
    mf        = _mf()
    results   = _get("results", [])
    sched_map = _get("sched_map", {})

    if mf.all_events_locked():
        meet_complete()
        return

    _header(mf)

    tab_heats, tab_results = st.tabs(["🏊 Heats", "🏁 Results"])

    # ── Heats tab ─────────────────────────────────────────────────────────────
    with tab_heats:
        st.markdown(
            "Upload a heat sheet to add heat and lane assignments. "
            "You can upload multiple heat sheets — one per session."
        )

        round_sel = st.radio(
            "Round", ["Prelims", "Finals"], horizontal=True, key="heat_round"
        )
        st.caption(
            "💡 Some events (400 IM, 500 Free, 1650 Free) run as Finals "
            "even during a prelim session. If an event is missing, "
            "switch to Finals and re-upload."
        )

        upload_type = st.radio(
            "File type", ["PDF heat sheet", "Screenshot"],
            horizontal=True, key="heat_type"
        )

        heat_file = st.file_uploader(
            "Upload heat sheet",
            type=["pdf"] if "PDF" in upload_type else ["png","jpg","jpeg"],
            key="heat_file",
        )

        if heat_file:
            if st.button("🔍 Extract Heats", use_container_width=True,
                         key="extract_heats"):
                if "PDF" in upload_type:
                    with st.spinner("Parsing heat sheet…"):
                        tmp = _tmp_pdf(heat_file)
                        try:
                            parsed = parse_heat_sheet(tmp)
                        finally:
                            os.unlink(tmp)
                        found = find_swimmer_heats(
                            parsed, mf.swimmer_last, mf.swimmer_first
                        )
                        # Filter to selected round
                        found = [h for h in found
                                 if h["round"] == round_sel]

                    if not found:
                        st.warning(
                            f"No {round_sel} assignments found for "
                            f"{mf.swimmer_first} {mf.swimmer_last}."
                        )
                    else:
                        _set("pending_heats", found)
                        st.markdown(
                            '<div class="confirm-box">'
                            '<b>Review before saving:</b></div>',
                            unsafe_allow_html=True
                        )
                        for h in found:
                            st.markdown(
                                f"• **Ev {h['event_num']} "
                                f"{h['event_name']}** — "
                                f"{h['round']} "
                                f"Heat {h['heat']} Lane {h['lane']}"
                            )

                else:
                    # Screenshot
                    api = _api_key()
                    if not api:
                        st.error("API key required for screenshots.")
                    else:
                        with st.spinner("Extracting from screenshot…"):
                            img = heat_file.read()
                            ext = heat_file.name.split(".")[-1].lower()
                            result = extract_heat_assignment(
                                img, mf.swimmer_last, mf.swimmer_first,
                                api_key=api,
                                media_type=f"image/{ext}",
                            )
                        if result.get("found"):
                            _set("pending_heats", [result])
                            st.markdown(
                                '<div class="confirm-box">'
                                '<b>Review before saving:</b></div>',
                                unsafe_allow_html=True
                            )
                            st.markdown(
                                f"• **{result.get('event_name')}** — "
                                f"{result.get('round')} "
                                f"Heat {result.get('heat')} "
                                f"Lane {result.get('lane')}"
                            )
                        else:
                            st.warning(result.get("reason", "Not found"))

        # Confirm / discard pending heats
        pending = _get("pending_heats")
        if pending:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Save Heat Assignments",
                             use_container_width=True, key="save_heats"):
                    count = _apply_heats(mf, pending)
                    _set_mf(mf)
                    _set("pending_heats", None)
                    st.success(f"Saved {count} heat assignment(s). "
                               f"Download updated meet file below.")
                    st.rerun()
            with c2:
                if st.button("❌ Discard", use_container_width=True,
                             key="discard_heats"):
                    _set("pending_heats", None)
                    st.rerun()

    # ── Results tab ───────────────────────────────────────────────────────────
    with tab_results:
        st.markdown(
            "Upload a results screenshot after each swim. "
            "Confirm each result before it saves."
        )

        result_file = st.file_uploader(
            "Upload results screenshot",
            type=["png","jpg","jpeg"],
            key="result_file",
        )

        if result_file:
            if st.button("🔍 Extract Result", use_container_width=True,
                         key="extract_result"):
                api = _api_key()
                if not api:
                    st.error("API key required for screenshots.")
                else:
                    with st.spinner("Extracting result…"):
                        img = result_file.read()
                        ext = result_file.name.split(".")[-1].lower()
                        extracted = extract_result(
                            img, mf.swimmer_last, mf.swimmer_first,
                            api_key=api,
                            media_type=f"image/{ext}",
                        )

                    if not extracted.get("found"):
                        st.warning(extracted.get("reason", "Not found"))
                    elif extracted.get("dq"):
                        st.warning("DQ — not saving.")
                    else:
                        ev_key    = normalize_event_key(
                            extracted.get("event_name","")
                        )
                        seed_time = ""
                        if ev_key and ev_key in mf.events:
                            seed_time = mf.events[ev_key].seed_time
                        elif ev_key not in (mf.events or {}):
                            # try event_num fallback
                            pass

                        drop_secs = calc_drop(
                            seed_time,
                            extracted.get("official_time","")
                        )
                        drop_str = format_drop(drop_secs) \
                            if drop_secs is not None else ""

                        _set("pending_result", extracted)
                        _set("pending_result_drop", drop_secs)

                        st.markdown(
                            '<div class="confirm-box">'
                            '<b>Review before saving:</b></div>',
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            f"**Event:** {extracted.get('event_name')}  \n"
                            f"**Round:** {extracted.get('round')}  \n"
                            f"**Time:** {extracted.get('official_time')} "
                            f"{drop_str}  \n"
                            f"**Place in heat:** "
                            f"{extracted.get('place_in_heat')}"
                        )

        # Confirm / discard pending result
        pending_r = _get("pending_result")
        if pending_r:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Confirm Result", use_container_width=True,
                             key="confirm_result"):
                    ev_key = normalize_event_key(
                        pending_r.get("event_name","")
                    )
                    # fallback by event_num if needed
                    if not ev_key or ev_key not in mf.events:
                        ev_key = next(
                            (k for k, v in mf.events.items()
                             if pending_r.get("event_name","").lower()
                             in v.event_name.lower()),
                            None
                        )

                    if ev_key and ev_key in mf.events:
                        ev_state  = mf.events[ev_key]
                        drop_secs = _get("pending_result_drop")
                        ri = ResultInfo(
                            time           = pending_r["official_time"],
                            place_in_heat  = pending_r.get("place_in_heat",0),
                            drop_from_seed = drop_secs or 0.0,
                            finals_qualified = pending_r.get(
                                "finals_qualified", False
                            ),
                            uploaded = datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        )
                        if pending_r.get("round") == "Finals":
                            ev_state.finals_result = ri
                            ev_state.locked        = True
                        else:
                            ev_state.prelim_result = ri

                        _set_mf(mf)
                        _set("pending_result", None)
                        _set("pending_result_drop", None)
                        st.success(
                            f"Result saved"
                            f"{' — event locked 🔒' if ev_state.locked else ''}."
                        )
                        st.rerun()
                    else:
                        st.error(
                            f"Could not match "
                            f"'{pending_r.get('event_name')}' "
                            f"to swimmer's events."
                        )
            with c2:
                if st.button("❌ Discard", use_container_width=True,
                             key="discard_result"):
                    _set("pending_result", None)
                    st.rerun()

    # ── Always visible at bottom ──────────────────────────────────────────────
    st.divider()
    _download(mf)

    with st.expander("📋 Preview card"):
        _preview(mf, results, sched_map)

    if st.button("🏠 Start Over", use_container_width=True, key="start_over"):
        _clear()
        st.rerun()


# ── Meet complete ─────────────────────────────────────────────────────────────

def meet_complete():
    mf        = _mf()
    results   = _get("results", [])
    sched_map = _get("sched_map", {})

    st.balloons()
    st.markdown("## 🎉 Meet Complete!")
    st.markdown(
        f"All events locked for "
        f"**{mf.swimmer_first} {mf.swimmer_last}**."
    )
    _download(mf, "📥 Download Final Meet File")
    st.markdown(
        '<div class="info-box">'
        '💡 Save as PDF: browser Print (Cmd+P) → Save as PDF.'
        '</div>',
        unsafe_allow_html=True
    )
    with st.expander("📋 Final card", expanded=True):
        _preview(mf, results, sched_map)
    st.divider()
    if st.button("🏊 Start New Meet", use_container_width=True):
        _clear()
        st.rerun()


# ── Continue meet ─────────────────────────────────────────────────────────────

def continue_meet():
    st.markdown("## 🏊 SwimCard · Continue Meet")
    uploaded = st.file_uploader(
        "Upload meet file (.json)", type=["json"], key="mf_upload"
    )
    if not uploaded:
        return

    try:
        mf = MeetFile.from_json_bytes(uploaded.read())
    except Exception as e:
        st.error(f"Could not load meet file: {e}")
        return

    _set_mf(mf)
    _set("results",   _rebuild_results(mf))
    _set("sched_map", {})
    _set("screen",    "update")

    st.success(
        f"Loaded: **{mf.swimmer_first} {mf.swimmer_last}** · "
        f"{mf.config.meet_name}"
    )
    if st.button("▶️ Continue", use_container_width=True):
        st.rerun()


# ── Home ──────────────────────────────────────────────────────────────────────

def home():
    st.markdown("## 🏊 SwimCard")
    st.markdown(
        "Progressive swim meet card. "
        "Build once from the psych sheet, update throughout the meet."
    )
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🆕 New Meet", use_container_width=True):
            _set("screen", "new")
            st.rerun()
    with c2:
        if st.button("📂 Continue Meet", use_container_width=True):
            _set("screen", "continue")
            st.rerun()


# ── Router ────────────────────────────────────────────────────────────────────

def main():
    if not check_password():
        return

    mf     = _mf()
    screen = _get("screen", "home")

    # If meet file loaded, go straight to update screen
    if mf and screen == "update":
        update_screen()
        return

    if screen == "new":
        if st.button("🏠 Home", key="h1"):
            _set("screen", "home"); st.rerun()
        phase_0()
    elif screen == "continue":
        if st.button("🏠 Home", key="h2"):
            _set("screen", "home"); st.rerun()
        continue_meet()
    else:
        home()


if __name__ == "__main__":
    main()