"""Earth Guardian operations console.

Run with ``earthguardian dashboard``. Everything shown is computed locally from
the curated store; there is no backend and no network call on the page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

if __package__ in (None, ""):  # `streamlit run` executes this as a loose script
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from earthguardian import __version__
from earthguardian.cloud.relational import CuratedStore
from earthguardian.config import PLOTS, PLOTS_BY_ID, get_settings
from earthguardian.dashboard import ui
from earthguardian.edge.lorawan import PAYLOAD_BYTES, plan_uplinks

st.set_page_config(
    page_title="Earth Guardian", page_icon="[]", layout="wide", initial_sidebar_state="expanded"
)
ui.apply_theme()

SETTINGS = get_settings()


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------


@st.cache_resource(show_spinner="Running GAIA over the curated uplinks...")
def load_analysis():
    from earthguardian.pipeline import analyse

    return analyse(persist=False)


@st.cache_data(show_spinner=False)
def load_truth(plot_id: str) -> pd.DataFrame:
    return CuratedStore(SETTINGS.paths.database).read_frame("ground_truth", plot_id)


@st.cache_data(show_spinner=False)
def load_events(plot_id: str) -> pd.DataFrame:
    return CuratedStore(SETTINGS.paths.database).read_frame("site_events", plot_id)


@st.cache_data(show_spinner=False)
def load_readings(plot_id: str) -> pd.DataFrame:
    return CuratedStore(SETTINGS.paths.database).read_frame("readings", plot_id)


@st.cache_resource(show_spinner="Running the counterfactual...")
def load_impact(days: int):
    from datetime import date

    from earthguardian.decisions import run_impact_study

    return run_impact_study(date(2025, 1, 1), days)


def require_data() -> bool:
    if not SETTINGS.paths.database.exists():
        st.warning("No curated data yet.")
        st.code("earthguardian simulate --days 365", language="bash")
        return False
    return True


# --------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------


def page_fleet(analysis) -> None:
    st.title("Fleet")
    ui.note(
        "Three smallholder plots across Brazil, chosen so that soil, crop and radio link each "
        "pull the decision in a different direction."
    )
    st.write("")

    total_area = sum(plot.area_ha for plot in PLOTS)
    actionable = [a for a in analysis.per_plot.values() if a.irrigation.is_actionable]
    measured = [a for a in analysis.per_plot.values() if not a.soil.field_capacity_is_prior]

    columns = st.columns(4)
    columns[0].metric("Plots monitored", f"{len(PLOTS)}", f"{total_area:.1f} ha")
    columns[1].metric("Needing irrigation", f"{len(actionable)}")
    columns[2].metric(
        "Self-calibrated",
        f"{len(measured)}/{len(analysis.per_plot)}",
        help="Plots where field capacity was recovered from the plot's own "
        "drainage rather than a texture-book value.",
    )
    columns[3].metric(
        "Open advisories", f"{len([a for a in analysis.advisories if a.severity != 'info'])}"
    )

    st.write("")
    left, right = st.columns([3, 2], gap="large")

    with left:
        frames = []
        for plot_id, plot_analysis in analysis.per_plot.items():
            daily = plot_analysis.soil.daily[["date", "theta_smooth"]].copy()
            daily["plot"] = PLOTS_BY_ID[plot_id].municipality
            frames.append(daily)
        trace = pd.concat(frames, ignore_index=True)
        st.altair_chart(
            ui.multi_line(
                trace,
                "date:T",
                "theta_smooth:Q",
                "plot:N",
                title="Soil water content",
                y_title="m3/m3",
                height=300,
            ),
            width="stretch",
        )
        ui.note(
            "The three plots sit at different absolute water contents because their soils hold "
            "different amounts, not because one is wetter than another. Comparing them needs the "
            "depletion, not the raw reading - which is the entire reason field capacity has to be "
            "recovered per plot."
        )

    with right:
        st.markdown("##### Advisories")
        for advisory in analysis.advisories[:6]:
            colour = ui.SEVERITY_COLOURS.get(advisory.severity, ui.PALETTE["muted"])
            ui.card(
                f"{PLOTS_BY_ID[advisory.plot_id].municipality} - {advisory.title}",
                f"{advisory.finding}<br><b>{advisory.action}</b>",
                pill=advisory.severity.upper(),
                pill_colour=colour,
            )

    st.write("")
    st.markdown("##### Plots")
    rows = []
    for plot_id, plot_analysis in analysis.per_plot.items():
        plot, plan = plot_analysis.plot, plot_analysis.irrigation
        rows.append(
            {
                "Plot": plot_id,
                "Farm": plot.farm,
                "Where": f"{plot.municipality}/{plot.state}",
                "Crop": plot.crop_spec.name,
                "Soil": plot.soil_spec.name,
                "ha": plot.area_ha,
                "TAW (mm)": plot.taw_mm,
                "Decision": plan.decision.replace("_", " "),
                "Depth (mm)": plan.depth_mm,
                "Cost (R$)": plan.cost_brl,
                "Calibration": plan.confidence,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def page_water(analysis, plot_id: str) -> None:
    plot = PLOTS_BY_ID[plot_id]
    plot_analysis = analysis.per_plot[plot_id]
    soil, plan = plot_analysis.soil, plot_analysis.irrigation

    st.title(f"Soil water - {plot.municipality}/{plot.state}")
    ui.note(plot.profile)
    st.write("")

    metrics = st.columns(5)
    metrics[0].metric("Depletion", f"{plan.depletion_mm:.0f} mm", f"of {plan.raw_mm:.0f} mm usable")
    metrics[1].metric("Decision", plan.decision.replace("_", " "))
    metrics[2].metric("Depth", f"{plan.depth_mm:.0f} mm", f"{plan.pumped_m3:.0f} m3")
    metrics[3].metric("Due", str(plan.due_on))
    score = plot_analysis.score
    if score:
        metrics[4].metric(
            "Depletion vs truth",
            f"{score.get('depletion_mae_mm', float('nan')):.1f} mm MAE",
            f"r = {score.get('depletion_corr', float('nan')):.3f}",
        )

    theta_trigger = soil.theta_fc - plan.raw_mm * 0.9 / max(plan.taw_mm, 1e-6) * (
        soil.theta_fc - soil.theta_wp
    )
    st.altair_chart(
        ui.soil_water_chart(
            soil.daily, soil.theta_fc, theta_trigger, soil.theta_wp, load_events(plot_id)
        ),
        width="stretch",
    )
    ui.note(
        "Ticks along the bottom mark irrigation events. The dashed lines are the recovered field "
        "capacity, the refill point at the crop's allowable depletion, and the wilting point."
    )

    truth = load_truth(plot_id)
    if not truth.empty:
        st.write("")
        st.markdown("##### Scoring against the state the simulator hid")
        length = min(len(truth), len(soil.daily))
        comparison = pd.DataFrame(
            {
                "date": truth["date"].to_numpy()[:length],
                "GAIA estimate": np.clip(
                    (soil.theta_fc - soil.daily["theta_smooth"].to_numpy()[:length])
                    * truth["root_depth_m"].to_numpy()[:length]
                    * 1000.0,
                    0,
                    None,
                ),
                "True depletion": truth["depletion_mm"].to_numpy()[:length].clip(min=0),
            }
        ).melt("date", var_name="series", value_name="mm")
        st.altair_chart(
            ui.multi_line(
                comparison,
                "date:T",
                "mm:Q",
                "series:N",
                domain=["GAIA estimate", "True depletion"],
                range_=[ui.PALETTE["water"], ui.PALETTE["truth"]],
                y_title="root-zone depletion (mm)",
                height=260,
            ),
            width="stretch",
        )
        ui.note(
            "GAIA never reads the grey line. It is written by the simulator and used only here, "
            "and in the test-suite, to say how far off the estimate is."
        )


def page_calibration(analysis, plot_id: str) -> None:
    plot_analysis = analysis.per_plot[plot_id]
    soil = plot_analysis.soil
    plot = PLOTS_BY_ID[plot_id]

    st.title(f"Self-calibration - {plot.municipality}")
    ui.note(
        "A farmer does not know their field capacity, and the textbook range for a single "
        "texture class is wider than the decision can tolerate: published values for clay loam "
        "run from 0.29 through 0.31 to 0.344 measured in situ. So it is recovered from the plot."
    )
    st.write("")

    metrics = st.columns(4)
    metrics[0].metric(
        "Field capacity",
        f"{soil.theta_fc:.3f}",
        "measured" if not soil.field_capacity_is_prior else "texture prior",
    )
    metrics[1].metric(
        "Wilting point",
        f"{soil.theta_wp:.3f}",
        "prior" if soil.wilting_point_is_prior else "observed",
    )
    metrics[2].metric("Evidence", f"{soil.n_draining_days} days", "profile above field capacity")
    if plot_analysis.score:
        metrics[3].metric("Error vs truth", f"{plot_analysis.score.get('theta_fc_error', 0):+.4f}")

    daily = plot_analysis.daily
    theta = soil.daily["theta_smooth"].to_numpy()
    length = min(len(theta), len(daily))
    extraction = daily["etc_mm"].to_numpy()[:length] / (
        daily["root_depth_m"].to_numpy()[:length] * 1000.0
    )
    from earthguardian.gaia.soil_state import detect_wetting_events, drainage_residuals

    wetting = np.zeros(length, dtype=bool)
    wetting[[e for e in detect_wetting_events(soil.daily["theta_smooth"]) if e < length]] = True
    wetting[1:] |= wetting[:-1]
    points, residuals = drainage_residuals(
        theta[:length],
        daily["etc_mm"].to_numpy()[:length],
        daily["root_depth_m"].to_numpy()[:length],
        wetting,
    )

    st.altair_chart(
        ui.hinge_chart(pd.Series(points), pd.Series(residuals), soil.theta_fc), width="stretch"
    )
    ui.note(
        "Each dot is one day the profile was not being wetted: how wet the soil was, against how "
        "much water left it that evapotranspiration cannot account for. Below field capacity that "
        "residual sits on zero; above it, gravity is still draining. The green line is the fitted "
        "elbow - and it is field capacity, recovered without a rain gauge or a laboratory."
    )
    _ = extraction


def page_radio() -> None:
    st.title("Radio")
    ui.note(
        "LoRaWAN fair use allows each node thirty seconds of uplink airtime a day. How far that "
        "goes depends on how far the node is from the gateway, because time-on-air grows "
        "exponentially with spreading factor. On one of these plots the radio, not the agronomy, "
        "decides how often the soil gets measured."
    )
    st.write("")

    rows = []
    for plot in PLOTS:
        budget = plan_uplinks(
            plot.gateway_distance_km,
            PAYLOAD_BYTES,
            terrain_loss_db=plot.terrain_loss_db,
            max_uplinks_per_day=SETTINGS.samples_per_day,
        )
        readings = load_readings(plot.plot_id)
        days = max(readings["timestamp"].dt.floor("D").nunique(), 1) if not readings.empty else 1
        rows.append(
            {
                "Plot": plot.plot_id,
                "Distance (km)": plot.gateway_distance_km,
                "Terrain (dB)": plot.terrain_loss_db,
                "RSSI (dBm)": round(budget.rssi_dbm, 1),
                "Data rate": f"DR{budget.data_rate.index}/SF{budget.data_rate.spreading_factor}",
                "Airtime (ms)": round(budget.airtime_s * 1000, 1),
                "Budgeted/day": budget.uplinks_per_day,
                "Received/day": round(len(readings) / days, 1),
                "Daily airtime (s)": round(budget.daily_airtime_s, 1),
                "Limited by": "radio"
                if budget.uplinks_per_day < SETTINGS.samples_per_day
                else "sampling",
            }
        )
    table = pd.DataFrame(rows)
    st.dataframe(table, hide_index=True, width="stretch")

    st.write("")
    airtime = pd.DataFrame(
        {
            "Plot": table["Plot"],
            "Used": table["Daily airtime (s)"],
            "Remaining": (30.0 - table["Daily airtime (s)"]).clip(lower=0),
        }
    ).melt("Plot", var_name="part", value_name="seconds")
    chart = (
        alt.Chart(airtime)
        .mark_bar(cornerRadius=3)
        .encode(
            x=alt.X("seconds:Q", title="airtime per day (s), 30 s allowance"),
            y=alt.Y("Plot:N", title=None),
            color=alt.Color(
                "part:N",
                scale=alt.Scale(
                    domain=["Used", "Remaining"], range=[ui.PALETTE["radio"], ui.PALETTE["grid"]]
                ),
                legend=alt.Legend(title=None),
            ),
            tooltip=["Plot", "part", alt.Tooltip("seconds:Q", format=".1f")],
        )
    )
    st.altair_chart(ui.style(chart, 180), width="stretch")
    ui.note(
        "Venda Nova spends 29.5 of its 30 seconds to report every 28 minutes at SF11. Its "
        "water balance is reconstructed from roughly half the samples the other plots provide - "
        "and the accuracy on the water page is what that costs."
    )


def page_impact() -> None:
    st.title("Impact")
    ui.note(
        "Three ways to decide when to irrigate, over the same year of weather with the same seed. "
        "The calendar policy is given a sensible interval rather than a bad one: the alternative "
        "to this platform is not chaos, it is a reasonable habit."
    )
    st.write("")

    days = st.slider("Horizon (days)", 90, 365, 365, step=30)
    study = load_impact(days)
    frame = study.frame()
    comparison = study.compare()

    metrics = st.columns(3)
    metrics[0].metric(
        "Water saved",
        f"{comparison['water_saved_m3'].sum():,.0f} m3",
        f"{comparison['water_saved_pct'].mean():.0f}% on average",
    )
    metrics[1].metric("Better off", f"R$ {comparison['net_gain_brl'].sum():,.0f}")
    metrics[2].metric(
        "Yield loss avoided", f"{comparison['yield_loss_avoided'].mean():.1%}", "mean across plots"
    )

    labels = {"rainfed": "Rainfed", "calendar": "Fixed calendar", "sensor": "Sensor-driven"}
    frame["Policy"] = frame["policy"].map(labels)
    frame["Plot"] = frame["plot_id"].map(lambda p: PLOTS_BY_ID[p].municipality)

    left, right = st.columns(2, gap="large")
    with left:
        chart = (
            alt.Chart(frame)
            .mark_bar(cornerRadius=3)
            .encode(
                x=alt.X("pumped_m3:Q", title="water pumped (m3/year)"),
                y=alt.Y("Plot:N", title=None),
                color=alt.Color(
                    "Policy:N",
                    scale=alt.Scale(
                        domain=list(labels.values()),
                        range=[ui.PALETTE["muted"], ui.PALETTE["trigger"], ui.PALETTE["water"]],
                    ),
                    legend=alt.Legend(title=None),
                ),
                yOffset="Policy:N",
                tooltip=["Plot", "Policy", alt.Tooltip("pumped_m3:Q", format=",.0f")],
            )
        )
        st.altair_chart(ui.style(chart, 260), width="stretch")
    with right:
        chart = (
            alt.Chart(frame)
            .mark_bar(cornerRadius=3)
            .encode(
                x=alt.X(
                    "yield_loss:Q", title="yield lost to water stress", axis=alt.Axis(format="%")
                ),
                y=alt.Y("Plot:N", title=None),
                color=alt.Color(
                    "Policy:N",
                    scale=alt.Scale(
                        domain=list(labels.values()),
                        range=[ui.PALETTE["muted"], ui.PALETTE["trigger"], ui.PALETTE["water"]],
                    ),
                    legend=alt.Legend(title=None),
                ),
                yOffset="Policy:N",
                tooltip=["Plot", "Policy", alt.Tooltip("yield_loss:Q", format=".1%")],
            )
        )
        st.altair_chart(ui.style(chart, 260), width="stretch")

    ui.note(
        "The three plots win for different reasons. Ibiuna's calendar was over-watering a "
        "shallow-rooted crop on a heavy soil and the surplus drained away; Guaraciaba's was "
        "under-watering a sandy soil that dries in three days, so its gain is yield rather than "
        "water. A single headline percentage would have hidden both."
    )
    st.caption("Simulated under the assumptions in earthguardian.config - not a field trial.")


def page_method(analysis) -> None:
    st.title("How this works")
    st.markdown(
        """
This is an offline reconstruction of **Earth Guardian System**, the author's 2024
undergraduate thesis at FIAP. The thesis specified a five-sensor LoRaWAN node feeding an
AWS pipeline and an AI advisor called GAIA; the bench prototype that accompanied it was an
Arduino with a single soil-moisture probe. Everything here implements the *specification*,
with no hardware, no cloud account and no network access.
        """
    )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### The chain")
        for name, detail in {
            "Field": "FAO-56 Penman-Monteith evapotranspiration, a root-zone water balance with "
            "texture-dependent drainage, and the error budget of the real bill of materials.",
            "Radio": "AU915 link budget, time-on-air, adaptive data rate and a fair-use airtime "
            "allowance that genuinely limits one of the three plots.",
            "Cloud": "Uplinks wrapped in the AWS IoT Core for LoRaWAN envelope, landed as "
            "partitioned documents, curated into relations - the thesis's own two-tier design.",
            "GAIA": "Recovers field capacity from the plot's own drainage, tracks depletion, and "
            "turns it into ranked advisories across water, pH and air quality.",
            "Decisions": "An irrigation depth and date, priced, with the counterfactual that says "
            "what it is worth against a fixed calendar.",
        }.items():
            ui.card(name, detail)

    with right:
        st.markdown("##### Scored against hidden truth")
        rows = []
        for plot_id, plot_analysis in analysis.per_plot.items():
            score = plot_analysis.score
            if score:
                rows.append(
                    {
                        "plot": PLOTS_BY_ID[plot_id].municipality,
                        "field capacity error": score.get("theta_fc_error", float("nan")),
                        "depletion MAE (mm)": score.get("depletion_mae_mm", float("nan")),
                        "correlation": score.get("depletion_corr", float("nan")),
                    }
                )
        if rows:
            st.dataframe(pd.DataFrame(rows).round(4), hide_index=True, width="stretch")
        ui.note(
            "The simulator writes a ground-truth table GAIA is never allowed to read. These "
            "numbers compare the two afterwards."
        )
        st.write("")
        ui.card(
            "What this is not",
            "Every number here comes from a simulation. The municipalities are real, the "
            "agronomy is FAO-56 and the radio model follows the Semtech datasheets, but no soil "
            "was measured and no crop was grown. Treat it as a well-documented model, not as "
            "evidence.",
            pill="READ ME",
            pill_colour=ui.PALETTE["stress"],
        )


# --------------------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------------------


def main() -> None:
    with st.sidebar:
        st.markdown(
            f"### Earth Guardian\n<span class='eg-note'>console v{__version__}</span>",
            unsafe_allow_html=True,
        )
        st.write("")
        page = st.radio(
            "Page",
            ["Fleet", "Soil water", "Self-calibration", "Radio", "Impact", "How this works"],
            label_visibility="collapsed",
        )
        st.write("")
        plot_id = st.selectbox(
            "Plot",
            [plot.plot_id for plot in PLOTS],
            format_func=lambda p: f"{PLOTS_BY_ID[p].municipality}/{PLOTS_BY_ID[p].state}",
        )
        st.write("")
        st.caption("FIAP undergraduate thesis, 2024. Rebuilt offline as a portfolio project.")

    if not require_data():
        return
    analysis = load_analysis()
    if not analysis.per_plot:
        st.warning("The curated store is empty.")
        st.code("earthguardian simulate --days 365", language="bash")
        return

    if page == "Fleet":
        page_fleet(analysis)
    elif page == "Soil water":
        page_water(analysis, plot_id)
    elif page == "Self-calibration":
        page_calibration(analysis, plot_id)
    elif page == "Radio":
        page_radio()
    elif page == "Impact":
        page_impact()
    elif page == "How this works":
        page_method(analysis)


main()
