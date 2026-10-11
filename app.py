import altair as alt
import streamlit as st

import agent

LABELED_COLOR = "#2a78d6"    # one series -> one hue
UNLABELED_COLOR = "#898781"  # muted gray: missing level, visually distinct from real levels

st.set_page_config(page_title="Course Completion Analyst", layout="wide")
st.title("Course Completion Analyst")
st.caption("Enrollments joined to the course catalog · " + agent.COMPLETED_DEFINITION)


@st.cache_data(show_spinner="Loading data from PostgreSQL...")
def get_data():
    return agent.load_data()


try:
    df = get_data()
except Exception as exc:
    # Show only the error type: driver messages can include connection details.
    st.error(f"Could not load data ({type(exc).__name__}). Check DATABASE_URL in .env.")
    st.stop()

# ---------- KPIs ----------
k = agent.kpis(df)
c1, c2, c3 = st.columns(3)
c1.metric("Total enrollments", f"{k['total_enrollments']:,}")
c2.metric("Average progress", f"{k['avg_progress_pct']:.1f}%")
c3.metric("Completion rate", f"{k['completion_rate_pct']:.2f}%")
c3.caption(f"{k['completed_enrollments']:,} completed enrollments")

# ---------- chart + table ----------
left, right = st.columns([2, 3])

with left:
    st.subheader("Completion rate by course level")
    levels = agent.completion_by_level(df)
    base = alt.Chart(levels).encode(
        x=alt.X("level:N", sort=agent.LEVEL_ORDER, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("completion_rate_pct:Q", title="Completion rate, %",
                axis=alt.Axis(grid=True, gridOpacity=0.3)),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=48).encode(
        color=alt.condition(alt.datum.level == "unlabeled",
                            alt.value(UNLABELED_COLOR), alt.value(LABELED_COLOR)),
        tooltip=[
            alt.Tooltip("level:N", title="Level"),
            alt.Tooltip("completion_rate_pct:Q", title="Completion rate, %", format=".2f"),
            alt.Tooltip("completed:Q", title="Completed", format=","),
            alt.Tooltip("enrollments:Q", title="Enrollments", format=","),
        ],
    )
    labels = base.mark_text(dy=-8).encode(text=alt.Text("completion_rate_pct:Q", format=".1f"))
    st.altair_chart(bars + labels, width="stretch")
    st.caption("'unlabeled' = level is missing in the source data ('Unknown'); it is not imputed.")

with right:
    st.subheader("10 courses with the lowest completion rate")
    st.dataframe(
        agent.rank_courses(df, "lowest"),
        hide_index=True,
        width="stretch",
        column_config={"completion_rate_pct": st.column_config.NumberColumn("completion, %", format="%.2f")},
    )
    z = agent.zero_completion_summary(df)
    st.caption(
        f"Courses grouped by course_slug, at least {agent.MIN_ENROLLMENTS} enrollments. "
        f"{z['of_them_with_0pct_completion']} of {z['courses_with_30plus_enrollments']} such courses "
        "have 0% completion; ties are ordered by enrollments (largest first)."
    )

# ---------- agent ----------
st.divider()
st.subheader("Ask the agent")
st.caption('Examples: "Which courses have the lowest completion rate?" · '
           '"Does course level affect completion?" · "Do longer courses have lower completion?"')

with st.form("ask"):
    question = st.text_input("Your question (any language)")
    submitted = st.form_submit_button("Ask")

if submitted and question.strip():
    with st.spinner("Analyzing..."):
        result = agent.answer(question.strip(), df)
    if result["error"]:
        st.warning(result["error"] + " Showing the computed results only.")
    if result["text"]:
        st.markdown(result["text"])
    with st.expander(f"Data behind the answer (analysis: {result['analysis']})"):
        for title, table in result["tables"].items():
            st.markdown(f"**{title}**")
            st.dataframe(table, hide_index=True, width="stretch")
