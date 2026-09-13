"""EarthGuardian - the dashboard from the 2024 thesis, running on the real pipeline.

Run with ``earthguardian dashboard``. Everything shown is computed locally from
the curated store; there is no backend and no network call on the page.

The design is Figura 14 of the thesis. Each of the six menu items it listed
keeps that screen's shape - cards along the top, an analysis panel beside an
insight panel, a metric strip, a column down the right - and fills it with what
that part of the system actually knows.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

if __package__ in (None, ""):  # `streamlit run` executes this as a loose script
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from earthguardian import __version__
from earthguardian.cloud.relational import CuratedStore
from earthguardian.config import PLOTS_BY_ID, Plot, get_settings
from earthguardian.dashboard import theme, ui
from earthguardian.decisions.impact import POLICIES, ImpactStudy, run_impact_study
from earthguardian.decisions.irrigation import TRIGGER_FRACTION_OF_RAW, IrrigationPlan
from earthguardian.edge.lorawan import (
    FAIR_USE_AIRTIME_S_DAY,
    PAYLOAD_BYTES,
    RadioBudget,
    plan_uplinks,
)
from earthguardian.edge.sensors import SENSOR_SUITE, SensorSpec
from earthguardian.gaia import advisories as adv
from earthguardian.gaia.soil_state import (
    WET_HALF_QUANTILE,
    detect_wetting_events,
    drainage_residuals,
)
from earthguardian.pipeline import AnalysisResult, PlotAnalysis, analyse, applied_irrigation

st.set_page_config(
    page_title="EarthGuardian",
    page_icon=":material/eco:",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.apply_theme()

SETTINGS = get_settings()

#: Main column to the column down the right, as the figure divides them.
LAYOUT = (2.4, 1.0)
#: Analysis panel to insight panel.
PANELS = (1.45, 1.0)
#: A day holding fewer uplinks than this share of the radio budget is the one
#: still arriving - the store ends a few hours into it - and shown as weather it
#: would read as a dark, cold day.
COMPLETE_DAY_SHARE = 0.5
#: How far back the predictive panel looks before it looks forward.
OUTLOOK_HISTORY_DAYS = 56

Page = Callable[[AnalysisResult, PlotAnalysis, DeltaGenerator, DeltaGenerator], None]


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------


@st.cache_resource(show_spinner="Running GAIA over the curated uplinks...")
def load_fleet() -> AnalysisResult:
    return analyse(persist=False)


@st.cache_data(show_spinner=False)
def load_table(table: str, plot_id: str) -> pd.DataFrame:
    return CuratedStore(SETTINGS.paths.database).read_frame(table, plot_id)


@st.cache_resource(show_spinner="Replaying the season under three irrigation policies...")
def load_impact(start: date, days: int) -> ImpactStudy:
    return run_impact_study(start, days)


# --------------------------------------------------------------------------------------
# What the pages derive
# --------------------------------------------------------------------------------------


def short_name(plot: Plot) -> str:
    """The municipality without its qualifier: "Venda Nova do Imigrante" does not fit a card."""
    return plot.municipality.split(" do ")[0]


def initials(farm: str) -> str:
    """ "Sitio Serra Verde" -> "SV" - the kind of farm is not part of its name."""
    return "".join(word[0] for word in farm.split()[1:3]).upper()


def device(dev_eui: str) -> str:
    return f"{dev_eui[:4]}…{dev_eui[-4:]}"


def radio(plot: Plot) -> RadioBudget:
    return plan_uplinks(
        plot.gateway_distance_km,
        PAYLOAD_BYTES,
        terrain_loss_db=plot.terrain_loss_db,
        max_uplinks_per_day=SETTINGS.samples_per_day,
    )


def complete_days(current: PlotAnalysis) -> pd.DataFrame:
    daily = current.daily
    return daily[daily["samples"] >= radio(current.plot).uplinks_per_day * COMPLETE_DAY_SHARE]


def growth(current: PlotAnalysis) -> str:
    crop = current.plot.crop_spec
    if crop.perennial:
        return "year-round crop"
    day = int(current.daily["days_after_planting"].iloc[-1]) + 1
    return f"{100 * day / crop.season_days:.0f}% growth"


def reserve_used(current: PlotAnalysis) -> pd.DataFrame:
    """Share of the crop's readily available water used, day by day.

    Water content cannot be compared across plots - a clay holds more than a
    sand at the same state of dryness - but the share of the crop's own reserve
    that is gone can.
    """
    soil, daily = current.soil, current.daily
    length = min(len(soil.daily), len(daily))
    root = daily["root_depth_m"].to_numpy()[:length]
    theta = soil.daily["theta_smooth"].to_numpy()[:length]
    depletion = np.clip((soil.theta_fc - theta) * root * 1000.0, 0.0, None)
    reserve = soil.taw_mm(1.0) * root * current.plot.crop_spec.depletion_fraction
    return pd.DataFrame(
        {"date": daily["date"].to_numpy()[:length], "used": 100.0 * depletion / reserve}
    )


def share_used(plan: IrrigationPlan) -> float:
    return 100.0 * plan.depletion_mm / max(plan.raw_mm, 1e-6)


def headline(plan: IrrigationPlan) -> str:
    """The insight panel's sentence - where the original said "It's the perfect day for spraying"."""
    if plan.decision == "irrigate_now":
        return "Irrigate today"
    if plan.decision == "irrigate_soon":
        return f"Irrigate by {plan.due_on:%A}, {plan.due_on:%d %b}"
    return f"No irrigation needed before {plan.due_on:%d %b}"


def decision(plan: IrrigationPlan) -> str:
    if plan.is_actionable:
        return f"{plan.depth_mm:.0f} mm by {plan.due_on:%d %b}"
    return f"hold until {plan.due_on:%d %b}"


SKY = (
    (0.85, "Sunny", "sunny"),
    (0.65, "Mostly sunny", "partly"),
    (0.45, "Partly cloudy", "partly"),
    (0.0, "Cloudy", "cloudy"),
)


def sky(lux: float, clear: float) -> tuple[str, str]:
    """Cloud cover, read off the light sensor against this plot's recent clear days."""
    ratio = lux / max(clear, 1.0)
    return next((label, art) for threshold, label, art in SKY if ratio >= threshold)


def outlook(fleet: AnalysisResult) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each plot's reserve over the last weeks, and where it is heading.

    The projection holds demand at the last week's rate and lets no rain fall -
    the assumption the plan's due date rests on - so each dotted line reaches
    the trigger on the day its plan names.
    """
    trigger = 100.0 * TRIGGER_FRACTION_OF_RAW
    observed, projected = [], []
    for current in fleet.per_plot.values():
        name = short_name(current.plot)
        history = reserve_used(current).tail(OUTLOOK_HISTORY_DAYS).assign(plot=name)
        observed.append(history)

        plan = current.irrigation
        now, span = share_used(plan), max(plan.days_to_trigger, 0.0)
        steps = np.linspace(0.0, span, int(np.ceil(span)) + 1) if span > 0 else np.zeros(1)
        rise = max(trigger - now, 0.0) / span if span > 0 else 0.0
        start = pd.Timestamp(history["date"].iloc[-1])
        projected.append(
            pd.DataFrame(
                {
                    "date": start + pd.to_timedelta(steps, unit="D"),
                    "used": now + rise * steps,
                    "plot": name,
                }
            )
        )
    return pd.concat(observed, ignore_index=True), pd.concat(projected, ignore_index=True)


def hinge_points(current: PlotAnalysis) -> pd.DataFrame:
    """The days GAIA fits the drainage hinge over, and which half the fit may use.

    Built exactly as :func:`earthguardian.gaia.soil_state.estimate_soil_state`
    builds them - wetting days and irrigated days excluded - so the dashed line
    on the chart is the breakpoint of the cloud the chart shows.
    """
    soil, daily, plot = current.soil, current.daily, current.plot
    theta = soil.daily["theta_smooth"].to_numpy(dtype=float)
    length = min(len(theta), len(daily))
    wetting = np.zeros(length, dtype=bool)
    events = detect_wetting_events(soil.daily["theta_smooth"])
    wetting[[event for event in events if event < length]] = True
    wetting[1:] |= wetting[:-1]
    applied = applied_irrigation(
        load_table("site_events", plot.plot_id), daily["date"], plot.irrigation_efficiency
    )
    points, residuals = drainage_residuals(
        theta[:length],
        daily["etc_mm"].to_numpy()[:length],
        daily["root_depth_m"].to_numpy()[:length],
        wetting,
        applied[:length],
    )
    cut = float(np.quantile(points, WET_HALF_QUANTILE)) if len(points) else 0.0
    return pd.DataFrame({"theta": points, "residual_mm": residuals, "in_fit": points >= cut})


def depletion_against_truth(current: PlotAnalysis, truth: pd.DataFrame) -> pd.DataFrame:
    soil = current.soil
    length = min(len(truth), len(soil.daily))
    theta = soil.daily["theta_smooth"].to_numpy()[:length]
    root = truth["root_depth_m"].to_numpy()[:length]
    return pd.DataFrame(
        {
            "date": truth["date"].to_numpy()[:length],
            "GAIA estimate": np.clip((soil.theta_fc - theta) * root * 1000.0, 0.0, None),
            "Hidden truth": truth["depletion_mm"].to_numpy()[:length].clip(min=0.0),
        }
    ).melt("date", var_name="series", value_name="mm")


def evidence(advisory: adv.Advisory) -> tuple[str, str]:
    """The one number behind an advisory."""
    found = advisory.evidence
    if advisory.axis == "water":
        return f"{found['fraction_of_raw']:.0%}", "of reserve used"
    if advisory.axis == "soil_ph":
        return f"{found['ph_median']:.1f}", "soil pH"
    if advisory.axis == "air_quality":
        return f"{found['events']:.0f}", "smoke events"
    if advisory.axis == "node":
        return f"{found['battery_pct']:.0f}%", "battery"
    return "-", advisory.axis.replace("_", " ")


def plot_cards(
    fleet: AnalysisResult,
    current: PlotAnalysis,
    *,
    art: Callable[[PlotAnalysis, str], str],
    name: Callable[[PlotAnalysis], str],
    value: Callable[[PlotAnalysis], str],
) -> None:
    """One card per plot, alternating dark and pale as the figure's do."""
    items = []
    for index, item in enumerate(fleet.per_plot.values()):
        dark = index % 2 == 0
        items.append(
            ui.Card(
                art=art(item, theme.LIME if dark else theme.FOREST),
                name=name(item),
                value=value(item),
                dark=dark,
                focus=item.plot.plot_id == current.plot.plot_id,
            )
        )
    ui.cards(items)


# --------------------------------------------------------------------------------------
# Overview - Figura 14 itself
# --------------------------------------------------------------------------------------


def page_overview(
    fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot, plan = current.plot, current.irrigation
    readings = load_table("readings", plot.plot_id)
    latest = readings.iloc[-1]
    used = share_used(plan)

    with main:
        ui.heading("Crop progress", chevron=True)
        plot_cards(
            fleet,
            current,
            art=lambda item, colour: theme.crop_mark(item.plot.crop_spec.name, colour),
            name=lambda item: f"{item.plot.crop_spec.name} · {short_name(item.plot)}",
            value=growth,
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title("Predictive analysis")
            observed, projected = outlook(fleet)
            ui.chart(
                ui.outlook_chart(
                    observed,
                    projected,
                    order=[short_name(item.plot) for item in fleet.per_plot.values()],
                    focus=short_name(plot),
                    callout=f"{short_name(plot)} · {plan.due_on:%d %b}",
                    trigger=100.0 * TRIGGER_FRACTION_OF_RAW,
                )
            )
        with insight:
            ui.insight(
                headline(plan),
                theme.plant_mark(1.0 - used / 100.0),
                [
                    (f"{latest['soil_moisture']:.1%}", "real-time soil water"),
                    (f"pH {latest['soil_ph']:.1f}", "real-time soil pH"),
                    (f"{used:.0f}%", "of the crop's reserve used"),
                ],
            )

        smoke = adv.detect_burning_events(readings["air_quality_ppm"], readings["timestamp"])
        smoky = bool(smoke) and latest["timestamp"] - smoke[-1][0] < pd.Timedelta(days=2)
        ph = readings.set_index("timestamp")["soil_ph"].resample("D").median().tail(30).median()
        crop = plot.crop_spec
        ui.strip(
            [
                (
                    "air",
                    "Air quality",
                    f"{'smoke now' if smoky else 'normal'} · {len(smoke)} smoke events",
                ),
                (
                    "drop",
                    "Soil moisture",
                    f"{latest['soil_moisture']:.1%} · {used:.0f}% of reserve used",
                ),
                (
                    "sprout",
                    "Soil pH",
                    f"{ph:.1f} · {crop.name.lower()} wants {crop.ph_min:.1f}–{crop.ph_max:.1f}",
                ),
            ]
        )

    with side:
        ui.heading("Last four days")
        days = complete_days(current)
        clear = float(days["lux_mean"].tail(30).quantile(0.9))
        items = []
        for index, row in enumerate(days.tail(4).iloc[::-1].itertuples()):
            label, art = sky(row.lux_mean, clear)
            dark = index == 0
            items.append(
                ui.SideItem(
                    big=f"{row.date:%d}",
                    small=f"{row.date:%B}",
                    title=f"{row.temp_max_c:.0f}°",
                    detail=label,
                    art=theme.weather_mark(art, theme.LIME if dark else theme.FOREST),
                    dark=dark,
                )
            )
        ui.side_list(items)


# --------------------------------------------------------------------------------------
# Fields - the water decision
# --------------------------------------------------------------------------------------


def page_fields(
    fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot, plan, soil = current.plot, current.irrigation, current.soil
    events = load_table("site_events", plot.plot_id)

    with main:
        ui.heading("Fields")
        plot_cards(
            fleet,
            current,
            art=lambda item, colour: theme.crop_mark(item.plot.crop_spec.name, colour),
            name=lambda item: f"{short_name(item.plot)} · {item.plot.area_ha:g} ha",
            value=lambda item: decision(item.irrigation),
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title(f"Soil water · {plot.municipality}")
            trigger = (
                soil.theta_fc
                - TRIGGER_FRACTION_OF_RAW
                * plot.crop_spec.depletion_fraction
                * (soil.theta_fc - soil.theta_wp)
            )
            ui.chart(ui.soil_water_chart(soil.daily, soil.theta_fc, trigger, soil.theta_wp, events))
        with insight:
            if plan.is_actionable:
                cost = (
                    f"{plan.pumped_m3:,.0f} m³ · {ui.brl(plan.cost_brl)}",
                    "pumped at the meter",
                )
            else:
                cost = (f"{plan.days_to_trigger:.0f} days", "to the trigger at this week's demand")
            measured = plan.confidence == "measured"
            ui.insight(
                headline(plan),
                theme.plant_mark(1.0 - share_used(plan) / 100.0),
                [
                    (
                        f"{plan.depletion_mm:.0f} of {plan.raw_mm:.0f} mm",
                        "of the crop's reserve used",
                    ),
                    cost,
                    (
                        "measured" if measured else "texture prior",
                        "field capacity, from this plot's drainage"
                        if measured
                        else "field capacity - check the soil by hand",
                    ),
                ],
            )

        root = float(current.daily["root_depth_m"].iloc[-1])
        ui.strip(
            [
                ("sprout", "Soil", f"{plot.soil_spec.name} · holds {plan.taw_mm:.0f} mm"),
                ("leaf", "Crop", f"{plot.crop_spec.name} · roots at {root:.1f} m"),
                ("drop", "Irrigation", f"{plot.irrigation_efficiency:.0%} reaches the roots"),
            ]
        )

    with side:
        ui.heading("Irrigation log")
        irrigation = events[events["kind"] == "irrigation"].tail(4).iloc[::-1]
        if irrigation.empty:
            ui.note("No irrigation was applied this season.")
            return
        ui.side_list(
            [
                ui.SideItem(
                    big=f"{row.date:%d}",
                    small=f"{row.date:%B}",
                    title=f"{row.magnitude:.0f} mm",
                    detail=f"{row.magnitude / 1000.0 * plot.area_ha * 10_000:,.0f} m³ pumped",
                    art=theme.icon("drop", theme.LIME if index == 0 else theme.FOREST, 58),
                    dark=index == 0,
                )
                for index, row in enumerate(irrigation.itertuples())
            ]
        )


# --------------------------------------------------------------------------------------
# Sensors - the node's channels
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Channel:
    """How one column of the uplink is shown."""

    label: str
    short: str
    unit: str
    icon: str
    precision: str
    caveat: str

    def show(self, value: float) -> str:
        return f"{value:{self.precision}}{self.spaced_unit}"

    @property
    def spaced_unit(self) -> str:
        return self.unit if self.unit in ("", "%") else f" {self.unit}"


CHANNELS: dict[str, Channel] = {
    "soil_moisture": Channel(
        "Soil water",
        "Soil",
        "m³/m³",
        "drop",
        ".3f",
        "Reports capacitance, not water. GAIA recovers the calibration from the plot's own "
        "drainage instead of trusting it.",
    ),
    "air_temp_c": Channel(
        "Air temperature",
        "Temp",
        "°C",
        "thermometer",
        ".1f",
        "One of the three channels GAIA computes FAO-56 evapotranspiration from.",
    ),
    "humidity_pct": Channel(
        "Humidity",
        "Humidity",
        "%",
        "humidity",
        ".0f",
        "Carries the DHT22's own bias of about a point low, as it would in the field.",
    ),
    "lux": Channel(
        "Light",
        "Light",
        "lx",
        "sun",
        ",.0f",
        "A light-dependent resistor: coarse, but its daily total is the solar radiation "
        "FAO-56 needs.",
    ),
    "soil_ph": Channel(
        "Soil pH",
        "pH",
        "",
        "flask",
        ".1f",
        "Electrodes drift, so the thirty-day median matters more than any single reading.",
    ),
    "air_quality_ppm": Channel(
        "Air quality",
        "Air",
        "ppm-eq",
        "air",
        ".0f",
        "Cannot tell one gas from another, so it is compared with its own baseline, never "
        "with a published limit.",
    ),
}


def noise(spec: SensorSpec, channel: Channel) -> str:
    parts = []
    if spec.relative_noise:
        parts.append(f"±{spec.relative_noise:.0%}")
    if spec.absolute_noise:
        parts.append(f"±{spec.absolute_noise:g}{channel.spaced_unit}")
    return " and ".join(parts) or "none modelled"


def page_sensors(
    _fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot = current.plot
    readings = load_table("readings", plot.plot_id)
    latest = readings.iloc[-1]
    budget = radio(plot)
    # The picker sits in the panel below the cards, but its value is already
    # known when the cards are drawn.
    chosen = st.session_state.get("channel") or "soil_moisture"

    with main:
        ui.heading(f"Sensors · node {device(latest['dev_eui'])}")
        ui.cards(
            [
                ui.Card(
                    art=theme.icon(
                        channel.icon, theme.LIME if index % 2 == 0 else theme.FOREST, 40
                    ),
                    name=channel.label,
                    value=channel.show(latest[column]),
                    dark=index % 2 == 0,
                    focus=column == chosen,
                )
                for index, (column, channel) in enumerate(CHANNELS.items())
            ],
            compact=True,
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title("Last fourteen days")
            chosen = (
                st.segmented_control(
                    "Channel",
                    list(CHANNELS),
                    default="soil_moisture",
                    format_func=lambda column: CHANNELS[column].short,
                    key="channel",
                    label_visibility="collapsed",
                )
                or "soil_moisture"
            )
            recent = readings[readings["timestamp"] > latest["timestamp"] - pd.Timedelta(days=14)]
            channel = CHANNELS[chosen]
            ui.chart(ui.series_chart(recent, "timestamp", chosen, channel.unit or channel.label))
        spec = SENSOR_SUITE[chosen]
        with insight:
            ui.insight(
                spec.part,
                theme.icon_block(channel.icon),
                [
                    (noise(spec, channel), "noise on every reading"),
                    (f"{spec.resolution:g}{channel.spaced_unit}", "resolution"),
                ],
                footnote=channel.caveat,
            )

        ui.strip(
            [
                ("report", "Payload", f"{PAYLOAD_BYTES} bytes · five sensors and the battery"),
                (
                    "antenna",
                    "Reporting",
                    f"every {budget.interval_minutes:.0f} min · {budget.uplinks_per_day} a day",
                ),
                ("battery", "Battery", f"{latest['battery_pct']:.0f}% · solar buffered"),
            ]
        )

    with side:
        ui.heading("Latest uplinks")
        ui.side_list(
            [
                ui.SideItem(
                    big=f"{row.timestamp:%H:%M}",
                    small=f"{row.timestamp:%d %B}",
                    title=CHANNELS["soil_moisture"].show(row.soil_moisture),
                    detail=f"{row.air_temp_c:.1f} °C · SF{row.spreading_factor} at "
                    f"{row.rssi_dbm:.0f} dBm",
                    dark=index == 0,
                )
                for index, row in enumerate(readings.tail(4).iloc[::-1].itertuples())
            ]
        )


# --------------------------------------------------------------------------------------
# Analytics - GAIA's self-calibration, and how close it got
# --------------------------------------------------------------------------------------


def page_analytics(
    fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot, soil, plan, score = current.plot, current.soil, current.irrigation, current.score

    with main:
        ui.heading("Self-calibration")
        plot_cards(
            fleet,
            current,
            art=lambda _item, colour: theme.icon("hinge", colour, 48),
            name=lambda item: f"{short_name(item.plot)} · field capacity",
            value=lambda item: (
                f"{item.soil.theta_fc:.3f} "
                + ("texture prior" if item.soil.field_capacity_is_prior else "measured")
            ),
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title(f"Drainage hinge · {plot.municipality}")
            ui.chart(ui.hinge_chart(hinge_points(current), soil.theta_fc))
            ui.note(
                "Each dot is a day the profile was neither wetted nor irrigated. Above field "
                "capacity gravity is still draining, so the loss nothing else explains starts to "
                "climb; only the wetter half, in green, is fitted."
            )
        with insight:
            if not score:
                readouts = [("no ground truth", "nothing to score against")]
            else:
                readouts = [
                    (f"{score['theta_fc_error']:+.3f}", "field capacity error")
                    if not soil.field_capacity_is_prior
                    else ("not scored", "field capacity is the texture prior"),
                    (f"{score['depletion_mae_mm']:.1f} mm", "mean depletion error"),
                    (f"r = {score['depletion_corr']:.3f}", "tracking the hidden state"),
                ]
            ui.insight(
                "Scored against the hidden soil",
                theme.icon_block("target"),
                readouts,
                footnote="GAIA never reads the simulator's ground truth. It is used here, and in "
                "the tests, only to say how far off the estimate is.",
            )

        truth = load_table("ground_truth", plot.plot_id)
        if not truth.empty:
            with st.container(key="eg-panel-truth"):
                ui.panel_title("GAIA's depletion against the state the simulator hid")
                ui.chart(
                    ui.multi_line(
                        depletion_against_truth(current, truth),
                        "date:T",
                        "mm:Q",
                        "series:N",
                        domain=["GAIA estimate", "Hidden truth"],
                        range_=[theme.FOREST, theme.SLATE],
                        y_title="root-zone depletion (mm)",
                        height=180,
                    )
                )

        ui.strip(
            [
                ("hinge", "Evidence", f"{soil.n_draining_days} days above field capacity"),
                (
                    "sprout",
                    "Wilting point",
                    f"{soil.theta_wp:.3f} · "
                    + ("texture prior" if soil.wilting_point_is_prior else "observed"),
                ),
                ("drop", "Reserve", f"{plan.raw_mm:.0f} mm usable of {plan.taw_mm:.0f} mm"),
            ]
        )

    with side:
        ui.heading("Advisories")
        items = []
        for index, advisory in enumerate(adv.rank(current.advisories)):
            big, small = evidence(advisory)
            items.append(
                ui.SideItem(
                    big=big,
                    small=small,
                    title=advisory.title,
                    detail=advisory.severity,
                    dark=index == 0,
                    pill_colour=theme.SEVERITY_COLOURS.get(advisory.severity, theme.MUTED),
                )
            )
        ui.side_list(items, tall=True)


# --------------------------------------------------------------------------------------
# Reports - what the season was worth
# --------------------------------------------------------------------------------------

POLICY_LABELS = {"rainfed": "Rainfed", "calendar": "Fixed calendar", "sensor": "Sensor-driven"}


def page_reports(
    _fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot = current.plot
    days = complete_days(current)
    first, last = pd.Timestamp(days["date"].iloc[0]), pd.Timestamp(days["date"].iloc[-1])
    study = load_impact(first.date(), (last - first).days + 1)
    frame = study.frame()
    frame["Policy"] = frame["policy"].map(POLICY_LABELS)
    frame["Plot"] = frame["plot_id"].map(lambda plot_id: short_name(PLOTS_BY_ID[plot_id]))
    comparison = study.compare().set_index("plot_id")
    outcomes = frame.set_index(["plot_id", "policy"])
    stress_avoided = sum(
        int(outcomes.loc[(plot_id, "calendar"), "stressed_days"])
        - int(outcomes.loc[(plot_id, "sensor"), "stressed_days"])
        for plot_id in comparison.index
    )

    with main:
        ui.heading(f"Season report · {first:%Y}")
        ui.cards(
            [
                ui.Card(
                    theme.icon("drop", theme.LIME, 44),
                    "Water saved",
                    f"{comparison['water_saved_m3'].sum():,.0f} m³ · "
                    f"{comparison['water_saved_pct'].mean():.0f}%",
                    dark=True,
                    focus=True,
                ),
                ui.Card(
                    theme.icon("coin", theme.FOREST, 44),
                    "Better off",
                    ui.brl(comparison["net_gain_brl"].sum()),
                    focus=True,
                ),
                ui.Card(
                    theme.icon("leaf", theme.LIME, 44),
                    "Yield loss avoided",
                    f"{100 * comparison['yield_loss_avoided'].mean():.1f} points a plot",
                    dark=True,
                    focus=True,
                ),
                ui.Card(
                    theme.icon("sun", theme.FOREST, 44),
                    "Stress days avoided",
                    f"{stress_avoided:,} days",
                    focus=True,
                ),
            ]
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title("Water pumped over the season")
            ui.chart(
                ui.policy_bars(
                    frame, "pumped_m3", "m³ pumped", ",.0f", list(POLICY_LABELS.values())
                )
            )
        with insight, st.container(key="eg-panel-brief"):
            ui.panel_title("Farmer briefing")
            briefing = adv.as_briefing(current.advisories, plot)
            ui.briefing(briefing)
            st.download_button(
                "Briefing",
                briefing,
                file_name=f"{plot.plot_id.lower()}-briefing.txt",
                mime="text/plain",
                icon=":material/download:",
                on_click="ignore",
                key="download-briefing",
            )
            st.download_button(
                "Season results",
                study.frame().to_csv(index=False),
                file_name="season-results.csv",
                mime="text/csv",
                icon=":material/download:",
                on_click="ignore",
                key="download-season",
            )

        with st.container(key="eg-panel-yield"):
            ui.panel_title("Yield lost to water stress")
            ui.chart(
                ui.policy_bars(
                    frame,
                    "yield_loss",
                    "yield lost",
                    ".0%",
                    list(POLICY_LABELS.values()),
                    height=170,
                )
            )
            ui.note(
                "The plots win for different reasons. Ibiuna's calendar over-watered a "
                "shallow-rooted crop on a heavy soil and the surplus drained past the roots, so "
                "its gain is water; Guaraciaba's under-watered a sand that dries in three days, so "
                "its gain is yield."
            )

        calendar, sensor = POLICIES["calendar"], POLICIES["sensor"]
        ui.strip(
            [
                ("sprout", "Rainfed", "no irrigation at all"),
                (
                    "calendar",
                    "Fixed calendar",
                    f"{calendar.depth_mm:.0f} mm every {calendar.interval_days} days",
                ),
                (
                    "drop",
                    "Sensor-driven",
                    f"refill at {sensor.trigger_fraction_of_raw:.0%} of the reserve",
                ),
            ]
        )

    with side:
        ui.heading("Sensor-driven, by plot")
        items = []
        for plot_id, row in comparison.iterrows():
            items.append(
                ui.SideItem(
                    big=f"{row['water_saved_pct']:.0f}%",
                    small="water saved",
                    title=short_name(PLOTS_BY_ID[plot_id]),
                    detail=f"{ui.brl(row['net_gain_brl'])} better off · yield "
                    f"{100 * row['yield_loss_avoided']:+.1f} points",
                    dark=plot_id == plot.plot_id,
                )
            )
        ui.side_list(items, tall=True)
        ui.note(
            "Against a fixed calendar, over the same weather with the same seed. Simulated under "
            "the assumptions in earthguardian/config.py - not a field trial."
        )


# --------------------------------------------------------------------------------------
# Devices - the node and its radio
# --------------------------------------------------------------------------------------


def page_devices(
    fleet: AnalysisResult, current: PlotAnalysis, main: DeltaGenerator, side: DeltaGenerator
) -> None:
    plot = current.plot
    budget = radio(plot)
    readings = load_table("readings", plot.plot_id)

    with main:
        ui.heading("Devices")
        plot_cards(
            fleet,
            current,
            art=lambda _item, colour: theme.icon("antenna", colour, 50),
            name=lambda item: (
                f"{short_name(item.plot)} · "
                + device(load_table("readings", item.plot.plot_id)["dev_eui"].iloc[-1])
            ),
            value=lambda item: (
                f"SF{radio(item.plot).data_rate.spreading_factor} · "
                f"{radio(item.plot).uplinks_per_day} uplinks a day"
            ),
        )

        lead, insight = st.columns(PANELS, gap="medium")
        with lead, st.container(key="eg-panel-lead"):
            ui.panel_title(f"Airtime a day, against the {FAIR_USE_AIRTIME_S_DAY:.0f} s allowance")
            airtime = pd.DataFrame(
                [
                    {"Plot": short_name(item.plot), "used": radio(item.plot).daily_airtime_s}
                    for item in fleet.per_plot.values()
                ]
            )
            ui.chart(ui.airtime_bars(airtime, FAIR_USE_AIRTIME_S_DAY))
            ui.note(
                "Time on air doubles with every step of spreading factor, and distance forces the "
                "step. Venda Nova spends nearly its whole allowance to report every 28 minutes."
            )
        with insight:
            ui.insight(
                f"Radio link · {short_name(plot)}",
                theme.icon_block("antenna"),
                [
                    (
                        f"{plot.gateway_distance_km:g} km",
                        f"to the gateway · {plot.terrain_loss_db:.0f} dB lost to terrain",
                    ),
                    (
                        f"SF{budget.data_rate.spreading_factor} at {budget.rssi_dbm:.0f} dBm",
                        f"DR{budget.data_rate.index}, {budget.margin_db:.0f} dB above sensitivity",
                    ),
                    (
                        f"{budget.airtime_s * 1000:.0f} ms per uplink",
                        f"{budget.daily_airtime_s:.1f} s of {FAIR_USE_AIRTIME_S_DAY:.0f} s a day",
                    ),
                ],
            )

        ui.strip(
            [
                ("report", "Payload", f"{PAYLOAD_BYTES} bytes · one codec, node to cloud"),
                ("antenna", "Region", f"AU915 · {FAIR_USE_AIRTIME_S_DAY:.0f} s of airtime a day"),
                ("battery", "Hardware", "Arduino Uno and Raspberry Pi"),
            ]
        )

    with side:
        ui.heading("Delivery by day")
        battery = readings.set_index("timestamp")["battery_pct"].resample("D").median()
        ui.side_list(
            [
                ui.SideItem(
                    big=f"{row.date:%d}",
                    small=f"{row.date:%B}",
                    title=f"{row.samples:.0f} of {budget.uplinks_per_day}",
                    detail=f"uplinks received · battery {battery.get(row.date, np.nan):.0f}%",
                    art=theme.icon("antenna", theme.LIME if index == 0 else theme.FOREST, 58),
                    dark=index == 0,
                )
                for index, row in enumerate(complete_days(current).tail(4).iloc[::-1].itertuples())
            ]
        )


# --------------------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------------------

PAGES: dict[str, Page] = {
    "Overview": page_overview,
    "Fields": page_fields,
    "Sensors": page_sensors,
    "Analytics": page_analytics,
    "Reports": page_reports,
    "Devices": page_devices,
}


def sidebar() -> str:
    with st.sidebar:
        ui.wordmark("EarthGuardian")
        page = st.radio("Menu", theme.NAV, key="page", label_visibility="collapsed")
        with st.container(key="eg-sidebar-foot"):
            ui.news(
                "News",
                "Rebuilt offline from the 2024 FIAP thesis. Every reading on these pages is "
                "simulated.",
            )
            ui.footer("FIAP thesis, 2024", f"v{__version__}")
    return page


def topbar(fleet: AnalysisResult) -> PlotAnalysis:
    """The figure's search bar, doing what a search bar there would: choosing the field."""
    ids = list(fleet.per_plot)
    urgent = min(ids, key=lambda plot_id: fleet.per_plot[plot_id].irrigation.days_to_trigger)
    main, side = st.columns(LAYOUT, gap="large", vertical_alignment="center")
    with main:
        tag, search = st.columns((1.0, 3.4), vertical_alignment="center")
        with tag:
            ui.tag(f"Simulated · {len(ids)} farms")
        with search:
            plot_id = st.selectbox(
                "Field",
                ids,
                index=ids.index(urgent),
                key="field",
                format_func=lambda plot_id: (
                    f"{PLOTS_BY_ID[plot_id].municipality}/"
                    f"{PLOTS_BY_ID[plot_id].state} · {PLOTS_BY_ID[plot_id].crop_spec.name}"
                ),
                label_visibility="collapsed",
            )
    current = fleet.per_plot[plot_id]
    with side:
        ui.user(
            initials(current.plot.farm),
            current.plot.farm,
            f"{current.plot.municipality}/{current.plot.state}",
        )
    return current


def main() -> None:
    page = sidebar()
    if not SETTINGS.paths.database.exists():
        st.warning("No curated data yet.")
        st.code("earthguardian simulate --days 365", language="bash")
        return
    fleet = load_fleet()
    if not fleet.per_plot:
        st.warning("The curated store is empty.")
        st.code("earthguardian simulate --days 365", language="bash")
        return

    current = topbar(fleet)
    body, side = st.columns(LAYOUT, gap="large")
    PAGES[page](fleet, current, body, side)


main()
