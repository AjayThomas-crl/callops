"""
CallOps dashboard: the analysis in sql/ as a client-facing view.

Run:  streamlit run app/dashboard.py --server.port 8502 --server.address localhost

Module A tabs run the SAME .sql files as the analysis, so the dashboard cannot drift from them.
Every rate is shown with its n and a caveat. Model-vs-human audit results stay hidden until the
hand labels are finished, so the labeller is never anchored by the models.
"""
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from checks import collect as collect_checks  # noqa: E402
from db import connect  # noqa: E402

# Validated categorical order (blue, orange, aqua, yellow); single-measure charts use slot 1 only.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GRAY = "#8a8985"
HUMAN_TARGET = 100

st.set_page_config(page_title="CallOps", layout="wide")


@st.cache_data(ttl=600)
def q(sql: str) -> pd.DataFrame:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def sql_file(name: str, statement: int = 0) -> pd.DataFrame:
    """Run one statement from a file in sql/. Comments are stripped first (they can contain ';')."""
    text = re.sub(r"--[^\n]*", "", (ROOT / "sql" / name).read_text())
    statements = [s for s in text.split(";") if s.strip()]
    return q(statements[statement])


def num(df: pd.DataFrame) -> pd.DataFrame:
    """Postgres numerics arrive as Decimal; plotly wants floats."""
    for c in df.columns:
        if df[c].dtype == object:
            try:
                df[c] = pd.to_numeric(df[c])
            except (ValueError, TypeError):
                pass
    return df


def style(fig, height=360, pct_axis=None):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), legend_title_text="",
                      hoverlabel=dict(namelength=-1))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    if pct_axis:
        fig.update_yaxes(title=pct_axis, ticksuffix="%")
    return fig


LABELS = {
    "conv_per_client_pct": "converted clients (%)", "clients": "clients", "conversions": "conversions",
    "calls_per_conversion": "calls per conversion", "year": "year", "attempt": "call attempt",
    "conv_rate_of_call_n_pct": "converted on this call (%)", "clients_called_at_n": "clients called at N",
    "conversions_at_n": "conversions on call N", "calls_per_conversion_at_n": "calls per conversion at N",
    "pct": "%", "measure": "", "contact_channel": "channel", "conv_pct": "converted (%)",
    "prev_outcome": "previous campaign", "lift_vs_overall": "lift vs overall", "pct_from_2010": "% from 2010",
    "margin_95_pct": "95% margin", "domain": "domain", "kind": "", "pct_calls_with_failure": "calls with failure (%)",
    "calls": "calls", "asr_pipeline": "ASR pipeline", "pct_calls_repair_phrase": "calls with 'can you repeat' (%)",
    "ending_type": "ending",
}


def caveat(text: str):
    st.caption("⚠️ " + text)


st.title("CallOps · call campaign analytics")
st.caption("Real data: UCI Bank Marketing (41,188 clients, 2008–2010) and 8,079 AIxBlock call-center "
           "transcripts. The two are separate modules: no public dataset links leads to transcripts.")

tabs = st.tabs(["Funnel", "Redial", "Channel & timing", "Silences", "ASR", "Call endings", "Outcome audit",
                "Data quality"])

# ------------------------------------------------------------------ A1 funnel
with tabs[0]:
    df = num(sql_file("a1_funnel.sql"))
    total = df[df["year"] == "ALL"].iloc[0]
    c = st.columns(4)
    c[0].metric("Clients called", f"{int(total.clients):,}")
    c[0].caption("May 2008 – Nov 2010")
    c[1].metric("Calls made", f"{int(total.calls):,}")
    c[1].caption(f"{total.calls_per_client:.2f} calls per client")
    c[2].metric("Conversion per client", f"{total.conv_per_client_pct:.1f}%")
    c[2].caption(f"{int(total.conversions):,} clients subscribed")
    c[3].metric("Conversion per call", f"{total.conv_per_100_calls:.2f}%")
    c[3].caption(f"{total.calls_per_conversion:.0f} calls per conversion")
    years = df[df["year"] != "ALL"]
    fig = px.bar(years, labels=LABELS, x="year", y="conv_per_client_pct", text="conv_per_client_pct",
                 hover_data={"clients": ":,", "conversions": ":,", "calls_per_conversion": True},
                 color_discrete_sequence=[BLUE])
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside", marker_cornerradius=4)
    st.subheader("Conversion per client, by year")
    st.plotly_chart(style(fig, pct_axis="converted clients"), width="stretch")
    caveat("Same campaign, two valid rates: per client answers 'what share of the list did we win', per call "
           "answers 'what does each dial buy'. Call volume fell 13× from 2008 to 2010 while conversion rose 10×: "
           "the bank called fewer, warmer leads after the 2008 crisis. Years are not comparable populations. "
           "Year is inferred from file order and validated against CPI and Euribor.")
    st.dataframe(years, hide_index=True, width="stretch")

# ------------------------------------------------------------------ A2 redial
with tabs[1]:
    df = num(sql_file("a2_redial.sql"))
    st.subheader("Conversion rate of the Nth call")
    fig = px.bar(df, labels=LABELS, x="attempt", y="conv_rate_of_call_n_pct",
                 hover_data={"clients_called_at_n": ":,", "conversions_at_n": True, "calls_per_conversion_at_n": True},
                 color_discrete_sequence=[BLUE])
    fig.update_traces(marker_cornerradius=4)
    fig.update_xaxes(dtick=1, title="call attempt")
    st.plotly_chart(style(fig, pct_axis="converted on this call"), width="stretch")

    st.subheader("What a redial cap keeps vs what it costs")
    long = df.melt(id_vars="attempt", value_vars=["pct_conversions_kept_if_cap", "pct_calls_spent_if_cap"],
                   var_name="measure", value_name="pct")
    long["measure"] = long["measure"].map({"pct_conversions_kept_if_cap": "conversions kept",
                                           "pct_calls_spent_if_cap": "calls spent"})
    fig = px.line(long, labels=LABELS, x="attempt", y="pct", color="measure", markers=True,
                  color_discrete_sequence=[BLUE, ORANGE])
    fig.update_traces(line_width=2, marker_size=8)
    fig.update_xaxes(dtick=1, title="cap at N calls")
    st.plotly_chart(style(fig, pct_axis="share of total"), width="stretch")
    row5 = df[df["attempt"] == 5].iloc[0]
    st.info(f"**Cap at 5 calls:** keeps {row5.pct_conversions_kept_if_cap:.0f}% of conversions while spending "
            f"{row5.pct_calls_spent_if_cap:.0f}% of the calls. Call 8+ costs over 100 calls per conversion.")
    caveat("Rate of call N = conversions on call N / clients still being called at N (everyone with ≥ N calls). "
           "Assumes calling stops once a client converts. Not causal: agents choose whom to redial; an A/B test "
           "with a cap would settle it. 2008 dominates and redialled hardest.")

# ------------------------------------------------------------------ A3/A4 channel, timing, history
with tabs[2]:
    ch = num(sql_file("a3_channel_timing.sql", 0))
    st.subheader("Cellular vs telephone, within each year")
    yr = ch[ch["year"] != "ALL"]
    fig = px.bar(yr, labels=LABELS, x="year", y="conv_pct", color="contact_channel", barmode="group",
                 hover_data={"clients": ":,"}, color_discrete_sequence=[BLUE, ORANGE])
    fig.update_traces(marker_cornerradius=4)
    st.plotly_chart(style(fig, pct_axis="converted clients"), width="stretch")
    caveat("Overall, cellular converts 2.8× better (14.7% vs 5.2%), but much of that is the year mix. Within each "
           "year the gap is smaller. In May–June 2008 every call was a landline, so channel and month are "
           "confounded in 2008.")

    ym = num(sql_file("a3_channel_timing.sql", 1))
    ym["month"] = pd.to_datetime(ym["month"])
    st.subheader("Conversion by month (with 95% margin)")
    ok = ym[ym["flag"].isna()]
    small = ym[ym["flag"].notna()]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ok["month"], y=ok["conv_pct"], mode="markers", name="n ≥ 100",
                             marker=dict(color=BLUE, size=8),
                             error_y=dict(type="data", array=ok["margin_95_pct"], color=BLUE, thickness=1),
                             customdata=ok[["clients"]], hovertemplate="%{x|%b %Y}: %{y:.1f}%<br>n = %{customdata[0]:,}"))
    fig.add_trace(go.Scatter(x=small["month"], y=small["conv_pct"], mode="markers", name="n < 100 (too small)",
                             marker=dict(color=GRAY, size=8, symbol="circle-open"),
                             customdata=small[["clients"]], hovertemplate="%{x|%b %Y}: %{y:.1f}%<br>n = %{customdata[0]:,}"))
    st.plotly_chart(style(fig, pct_axis="converted clients"), width="stretch")
    caveat("No calls in Jan/Feb of any year or in Sep 2008: those are gaps, not zeros. May 2008 and May 2009 are "
           "mass-campaign months with low conversion.")

    hist = num(sql_file("a4_history.sql"))
    st.subheader("Previous campaign outcome")
    fig = px.bar(hist, labels=LABELS, x="prev_outcome", y="conv_pct", error_y="margin_95_pct",
                 hover_data={"clients": ":,", "lift_vs_overall": True, "pct_from_2010": True},
                 color_discrete_sequence=[BLUE])
    fig.update_traces(marker_cornerradius=4)
    st.plotly_chart(style(fig, height=320, pct_axis="converted clients"), width="stretch")
    caveat("Past converters convert at 5.8× the average, but 47% of them are from 2010 (the high-conversion year). "
           "Correlation, not causation.")

# ------------------------------------------------------------------ B2 silences
with tabs[3]:
    sil = num(sql_file("b2_silence_impact.sql"))
    allrow = sil[sil["domain"] == "ALL"].iloc[0]
    c = st.columns(3)
    c[0].metric("Calls with severe silence", f"{allrow.any_severe_pct:.1f}%")
    c[0].caption(f"of {int(allrow.calls):,} calls: either of the two below")
    c[1].metric("Someone asked 'are you there?'", f"{allrow.noticed_pct:.1f}%")
    c[2].metric("Unannounced silence ≥ 30 s", f"{allrow.long_unannounced_pct:.1f}%")
    dom = sil[sil["domain"] != "ALL"].sort_values("any_severe_pct")
    long = dom.melt(id_vars="domain", value_vars=["noticed_pct", "long_unannounced_pct"],
                    var_name="kind", value_name="pct")
    long["kind"] = long["kind"].map({"noticed_pct": "noticed dead air", "long_unannounced_pct": "unannounced ≥ 30 s"})
    fig = px.bar(long, labels=LABELS, y="domain", x="pct", color="kind", barmode="group", orientation="h",
                 color_discrete_sequence=[BLUE, ORANGE])
    fig.update_traces(marker_cornerradius=4)
    fig.update_xaxes(title="% of calls", ticksuffix="%")
    st.subheader("Share of calls affected, by domain")
    st.plotly_chart(style(fig, height=320), width="stretch")
    st.dataframe(dom, hide_index=True, width="stretch")
    caveat("Silence = gap ≥ 5 s between transcribed words, classified from the surrounding words (keyword rules, "
           "not yet validated by hand). Most 'unannounced' silence is agents typing quote-form answers. Check-ins "
           "in the first 30 s are ignored (that is people answering the phone). No speaker labels: we cannot say "
           "who went silent.")

# ------------------------------------------------------------------ B3 ASR
with tabs[4]:
    asr = num(q("""
        WITH per_call AS (SELECT call_id, count(*) runs FROM asr_low_runs GROUP BY call_id)
        SELECT c.domain,
               CASE WHEN c.domain = 'auto_health_insurance' THEN 'B' ELSE 'A' END AS asr_pipeline,
               count(*) AS calls,
               round(100.0 * count(p.call_id) / count(*), 1) AS pct_calls_with_failure,
               round(avg(c.asr_confidence)::numeric, 3) AS raw_avg_confidence,
               round(100.0 * avg((c.transcript ~* '(can you repeat|say that again|come again|didn.t (catch|hear) (that|you)|breaking up|bad (line|connection)|can.t hear you|cannot hear you|hard to hear)')::int), 1) AS pct_calls_repair_phrase
        FROM calls c LEFT JOIN per_call p USING (call_id) GROUP BY 1, 2 ORDER BY 4 DESC"""))
    st.subheader("Calls with an ASR failure stretch (3+ consecutive bottom-1% words)")
    fig = px.bar(asr, labels=LABELS, x="domain", y="pct_calls_with_failure", text="pct_calls_with_failure",
                 hover_data={"calls": ":,", "asr_pipeline": True, "pct_calls_repair_phrase": True},
                 color_discrete_sequence=[BLUE])
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside", marker_cornerradius=4)
    st.plotly_chart(style(fig, height=320, pct_axis="% of calls"), width="stretch")
    st.dataframe(asr, hide_index=True, width="stretch")
    caveat("auto_health_insurance comes from a different ASR export (pipeline B, 5-decimal confidences) and its raw "
           "confidence runs much lower, yet humans in those calls ask each other to repeat the LEAST. Raw "
           "confidence is not comparable across pipelines, so 'low' is defined as the bottom 1% within each "
           "pipeline. The two independent signals (failure stretches, repair phrases) rank the domains alike.")

# ------------------------------------------------------------------ B4 endings
with tabs[5]:
    end = num(q("""
        SELECT c.domain, e.ending_type, count(*) AS calls,
               round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY c.domain), 1) AS pct
        FROM call_endings e JOIN calls c USING (call_id) GROUP BY 1, 2"""))
    order = ["polite_close", "transfer", "refused", "no_closure"]
    st.subheader("How calls end, by domain")
    fig = px.bar(end, labels=LABELS, y="domain", x="pct", color="ending_type", orientation="h",
                 category_orders={"ending_type": order}, hover_data={"calls": ":,"},
                 color_discrete_sequence=[BLUE, ORANGE, AQUA, YELLOW])
    fig.update_traces(marker_line_width=1, marker_line_color="rgba(128,128,128,0.5)")
    fig.update_xaxes(title="% of calls", ticksuffix="%")
    st.plotly_chart(style(fig, height=320), width="stretch")

    steps = num(sql_file("b4_dead_ends.sql", 5))
    st.subheader("No-closure rate by the last step discussed")
    st.dataframe(steps, hide_index=True, width="stretch")
    caveat("'No closure' means the recording stops without a goodbye. It is NOT the same as a customer dropping "
           "off: in insurance outbound (Medicare lead-gen: qualify, then hand to a licensed agent) many recordings "
           "end at the handoff, so the step with the most no-closure endings may be the success point. "
           "Separating the two needs the dialler's call disposition.")

# ------------------------------------------------------------------ C audit
with tabs[6]:
    cov = num(q("SELECT labeler, count(*) AS labelled FROM outcome_labels GROUP BY 1 ORDER BY 1"))
    n_human = int(cov.loc[cov["labeler"] == "human", "labelled"].sum())
    st.subheader("Recorded outcome (models) vs ground truth (human)")
    st.progress(min(n_human / HUMAN_TARGET, 1.0), text=f"Human labels: {n_human} / {HUMAN_TARGET}")
    pairs = num(sql_file("c2_audit_results.sql", 1))
    models = pairs[~pairs["labeler_a"].eq("human") & ~pairs["labeler_b"].eq("human")]
    st.markdown("**Model vs model agreement** (200 calls each)")
    st.dataframe(models, hide_index=True, width="stretch")
    if n_human < HUMAN_TARGET:
        st.info(f"Human-vs-model results are hidden until {HUMAN_TARGET} calls are hand-labelled, so the labeller "
                "is not anchored by the models.")
    elif st.toggle("Show human vs model results"):
        st.dataframe(pairs[pairs["labeler_a"].eq("human") | pairs["labeler_b"].eq("human")],
                     hide_index=True, width="stretch")
        st.markdown("**Confusion matrix** (rows = human)")
        st.dataframe(num(sql_file("c2_audit_results.sql", 2)), hide_index=True, width="stretch")
        st.markdown("**Per-outcome precision / recall**")
        st.dataframe(num(sql_file("c2_audit_results.sql", 3)), hide_index=True, width="stretch")
        st.markdown("**Where disagreement concentrates**")
        st.dataframe(num(sql_file("c2_audit_results.sql", 4)), hide_index=True, width="stretch")
    caveat("Models stand in for a production system's recorded outcome. Kappa corrects agreement for chance. "
           "One human labeller; the human saw the last 3 minutes first, the models the full transcript.")

# ------------------------------------------------------------------ data quality
with tabs[7]:
    results = collect_checks()
    dq = pd.DataFrame(results, columns=["check", "severity", "ok", "observed", "expected"])
    dq["status"] = dq.apply(lambda r: "✅ PASS" if r.ok else ("❌ FAIL" if r.severity == "FAIL" else "⚠️ WARN"), axis=1)
    dq["observed"] = dq["observed"].astype(str)
    fails, warns = (dq["status"] == "❌ FAIL").sum(), (dq["status"] == "⚠️ WARN").sum()
    c = st.columns(3)
    c[0].metric("Checks", len(dq))
    c[1].metric("Failures", int(fails))
    c[2].metric("Warnings", int(warns))
    st.dataframe(dq[["status", "check", "observed", "expected"]], hide_index=True, width="stretch")
    caveat("Same checks as `python checks.py`, which exits non-zero on any FAIL so a scheduler can alert.")
