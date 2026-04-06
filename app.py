from __future__ import annotations
"""
app.py
======
SwimCard — Pre-meet card generator.
Upload psych sheet + SwimCloud goals PDF → get a card.
No heat sheets. No results. Just the pre-meet analysis.
"""

import os
import tempfile

import streamlit as st

from meet_config      import MeetConfig, Course, MeetType
from meet_file        import EventState, ScheduleInfo
from psych_parser     import parse_pdf
from goal_logic       import analyze_swimmer, get_tier, GoalInfo, EventResult
from card_template    import build_card_html
from vision_extractor import load_goals


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
  .info-box {
    background: #f0f4ff;
    border-radius: 8px;
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
    if st.button("Enter", use_container_width=True):
        if pwd == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")


def _tmp_pdf(uploaded_file):
    data = uploaded_file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        return f.name


# ── Main app ──────────────────────────────────────────────────────────────────

def main():
    if not check_password():
        return

    st.markdown("## 🏊 SwimCard")
    st.markdown("Upload a psych sheet to generate your pre-meet card.")

    # ── Inputs ────────────────────────────────────────────────────────────────
    with st.form("card_form"):
        st.markdown("**Swimmer**")
        c1, c2 = st.columns(2)
        with c1:
            first = st.text_input("First name", placeholder="Brayleigh")
        with c2:
            last = st.text_input("Last name", placeholder="Hoisington")

        gender_sel = st.radio(
            "Gender", ["Girls / Women", "Boys / Men"], horizontal=True
        )
        gender_val = "Women" if gender_sel.startswith("Girls") else "Men"

        st.markdown("**Meet**")
        meet_name  = st.text_input("Meet name",
                                   placeholder="2026 NW Spring Sectionals")
        meet_dates = st.text_input("Dates", placeholder="Mar 12–15, 2026")

        c3, c4 = st.columns(2)
        with c3:
            course_str = st.selectbox("Course", ["SCY", "LCM", "SCM"])
        with c4:
            type_str = st.selectbox("Meet type",
                                    ["Age Group", "Senior / Open"])

        consolation = 0
        if type_str == "Senior / Open":
            consolation = st.selectbox(
                "Consolation tiers", [0, 1, 2, 3],
                format_func=lambda x: {
                    0: "None",
                    1: "B finals (9–16)",
                    2: "B + C finals (9–24)",
                    3: "B + C + D finals (9–32)",
                }[x],
            )

        st.markdown("**Psych Sheet PDF** *(required)*")
        psych_file = st.file_uploader(
            "Upload psych sheet", type=["pdf"], key="psych_up"
        )

        st.markdown("**SwimCloud Goals PDF** *(optional)*")
        goals_file = st.file_uploader(
            "Upload goals PDF", type=["pdf"], key="goals_up"
        )

        submitted = st.form_submit_button(
            "🏗️ Generate Card", use_container_width=True
        )

    if not submitted:
        return

    # ── Validate ──────────────────────────────────────────────────────────────
    if not first or not last:
        st.error("Enter swimmer name.")
        return
    if not psych_file:
        st.error("Upload psych sheet PDF.")
        return
    if not meet_name:
        st.error("Enter meet name.")
        return

    # ── Parse psych sheet ─────────────────────────────────────────────────────
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
            f"Check spelling — names must match exactly."
        )
        return

    # Get age and team
    ev0 = events[results[0].event_num]
    s0  = next(
        s for s in ev0["swimmers"]
        if last.lower() in s["name"].lower()
        and first.lower() in s["name"].lower()
    )
    age  = s0["age"]
    team = s0["team"]

    # ── Parse goals PDF ───────────────────────────────────────────────────────
    goals_data = {}
    if goals_file:
        with st.spinner("Parsing goals PDF…"):
            tmp = _tmp_pdf(goals_file)
            try:
                goals_data = load_goals(tmp, api_key=_api_key())
            except Exception as e:
                st.warning(f"Could not parse goals PDF: {e}")
            finally:
                os.unlink(tmp)

    # ── Build event states with goals data ────────────────────────────────────
    states = {}
    for r in results:
        safe_key = r.event_key or f"event_{r.event_num}"
        states[safe_key] = EventState(
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
            goals_data = goals_data.get(r.event_key),
        )

    # ── Render card ───────────────────────────────────────────────────────────
    html = build_card_html(
        last_name  = last,
        first_name = first,
        age        = age,
        team       = team,
        gender     = gender_val,
        meet_name  = meet_name,
        meet_dates = meet_dates,
        results    = results,
        states     = states,
        sched_map  = {},
    )

    st.success(
        f"Card generated for **{first} {last}** — "
        f"{len(results)} event{'s' if len(results) != 1 else ''}."
    )

# ── Download card as HTML ─────────────────────────────────────────────────
    st.download_button(
        label        = "🖨️ Download Card (open in browser to print)",
        data         = html.encode("utf-8"),
        file_name    = f"{first}_{last}_SwimCard.html",
        mime         = "text/html",
        use_container_width = True,
    )
    st.markdown(
        '<div class="info-box">'
        '💡 Download the card → open the file in your browser → '
        '<b>Cmd+P</b> to save as PDF (Mac) or '
        '<b>Share → Print</b> on iPhone.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Card preview ──────────────────────────────────────────────────────────
    st.components.v1.html(
        html,
        height = len(results) * 160 + 200,
        scrolling = True,
    )

    # ── Start over ────────────────────────────────────────────────────────────
    st.divider()
    if st.button("🔄 Generate Another Card", use_container_width=True):
        for key in [k for k in st.session_state
                    if k != "authenticated"]:
            del st.session_state[key]
        st.rerun()


if __name__ == "__main__":
    main()