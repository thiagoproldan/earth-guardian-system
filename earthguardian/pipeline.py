"""End-to-end orchestration.

The CLI and the console both need "simulate, ingest, curate, analyse" to mean
the same thing. Keeping it here rather than in the CLI stops the two from
drifting, and makes the whole run importable from a notebook or a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from earthguardian.cloud.documents import DocumentStore
from earthguardian.cloud.pipeline import IngestReport, curate, ingest_fleet
from earthguardian.cloud.relational import CuratedStore
from earthguardian.config import PLOTS, Plot, get_settings
from earthguardian.decisions.irrigation import IrrigationPlan, plan_irrigation
from earthguardian.edge.lorawan import PAYLOAD_BYTES, plan_uplinks
from earthguardian.edge.simulator import IrrigationPolicy, simulate_fleet
from earthguardian.gaia import advisories as adv
from earthguardian.gaia.soil_state import SoilStateEstimate, estimate_soil_state
from earthguardian.gaia.weather import crop_demand, daily_weather_from_uplinks


@dataclass(slots=True)
class PlotAnalysis:
    """Everything GAIA concluded about one plot."""

    plot: Plot
    daily: pd.DataFrame
    soil: SoilStateEstimate
    advisories: list[adv.Advisory]
    irrigation: IrrigationPlan
    #: Populated only when ground truth is available - never used to decide.
    score: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisResult:
    per_plot: dict[str, PlotAnalysis]

    @property
    def advisories(self) -> list[adv.Advisory]:
        found = [a for analysis in self.per_plot.values() for a in analysis.advisories]
        return adv.rank(found)

    def advisories_frame(self) -> pd.DataFrame:
        return pd.DataFrame([a.as_dict() for a in self.advisories])

    def plans_frame(self) -> pd.DataFrame:
        from dataclasses import asdict

        return pd.DataFrame([asdict(a.irrigation) for a in self.per_plot.values()])

    def summary(self) -> pd.DataFrame:
        rows = []
        for plot_id, analysis in self.per_plot.items():
            plot, soil = analysis.plot, analysis.soil
            rows.append(
                {
                    "plot_id": plot_id,
                    "farm": plot.farm,
                    "crop": plot.crop_spec.name,
                    "soil": plot.soil_spec.name,
                    "theta_fc_estimated": soil.theta_fc,
                    "theta_fc_source": "prior" if soil.field_capacity_is_prior else "measured",
                    "draining_days": soil.n_draining_days,
                    "decision": analysis.irrigation.decision,
                    "depth_mm": analysis.irrigation.depth_mm,
                    "cost_brl": analysis.irrigation.cost_brl,
                    "soil_fc_error": analysis.score.get("theta_fc_error", float("nan")),
                }
            )
        return pd.DataFrame(rows)


def applied_irrigation(events: pd.DataFrame, dates: pd.Series, efficiency: float) -> np.ndarray:
    """Daily depth that actually reached the soil, from the irrigation log.

    The log records what was pumped; the crop receives that times the system's
    efficiency, the rest going to evaporation and leaks on the way.
    """
    index = pd.to_datetime(dates).dt.floor("D")
    series = pd.Series(0.0, index=index.to_numpy())
    if events.empty:
        return series.to_numpy()
    irrigation = events[events["kind"] == "irrigation"]
    if irrigation.empty:
        return series.to_numpy()
    delivered = (
        irrigation.assign(day=pd.to_datetime(irrigation["date"]).dt.floor("D"))
        .groupby("day")["magnitude"]
        .sum()
        * efficiency
    )
    series.update(delivered.reindex(series.index).dropna())
    return series.to_numpy()


def generate_and_ingest(
    start: date,
    days: int,
    *,
    plots: tuple[Plot, ...] = PLOTS,
    policy: IrrigationPolicy | None = None,
) -> tuple[IngestReport, dict[str, int]]:
    """Simulate the fleet, land it in the raw zone, curate it."""
    settings = get_settings()
    settings.paths.ensure()

    policy = policy or IrrigationPolicy.sensor_driven(0.9)
    uplinks, truth, events = simulate_fleet(start, days, plots, policy=policy)

    store = DocumentStore(settings.paths.raw)
    curated = CuratedStore(settings.paths.database)
    report = ingest_fleet(uplinks, store)
    written = curate(store, curated, plots, truth, events)
    return report, written


def analyse(
    *, plots: tuple[Plot, ...] = PLOTS, score_against_truth: bool = True, persist: bool = True
) -> AnalysisResult:
    """Run GAIA over whatever is in the curated store."""
    settings = get_settings()
    curated = CuratedStore(settings.paths.database)
    per_plot: dict[str, PlotAnalysis] = {}

    for plot in plots:
        readings = curated.read_frame("readings", plot.plot_id)
        if readings.empty:
            continue

        daily = crop_demand(daily_weather_from_uplinks(readings, plot), plot)

        # What the platform itself put on the field. It operates the valves, so
        # this is its own command log - the curated events table - and not the
        # simulator's hidden state.
        events = curated.read_frame("site_events", plot.plot_id)
        applied = applied_irrigation(events, daily["date"], plot.irrigation_efficiency)

        soil = estimate_soil_state(
            readings,
            plot.plot_id,
            plot.soil_spec.theta_fc,
            plot.soil_spec.theta_wp,
            crop_demand_mm=daily["etc_mm"].to_numpy(),
            root_depth_m=daily["root_depth_m"].to_numpy(),
            irrigation_mm=applied,
        )

        root = float(daily["root_depth_m"].iloc[-1])
        taw = soil.taw_mm(root)
        theta_now = float(soil.daily["theta_smooth"].iloc[-1])
        depletion = max((soil.theta_fc - theta_now) * root * 1000.0, 0.0)
        recent_etc = float(daily["etc_mm"].tail(7).mean())
        today = pd.Timestamp(daily["date"].iloc[-1]).date()

        found = [
            adv.water_advisory(
                plot, depletion, taw * plot.crop_spec.depletion_fraction, taw, recent_etc, today
            )
        ]
        ph = adv.ph_advisory(
            plot, readings.set_index("timestamp")["soil_ph"].resample("D").median(), today
        )
        if ph:
            found.append(ph)
        burning = adv.burning_advisory(
            plot,
            adv.detect_burning_events(readings["air_quality_ppm"], readings["timestamp"]),
            today,
        )
        if burning:
            found.append(burning)
        budget = plan_uplinks(
            plot.gateway_distance_km,
            PAYLOAD_BYTES,
            terrain_loss_db=plot.terrain_loss_db,
            max_uplinks_per_day=settings.samples_per_day,
        )
        node = adv.node_advisory(plot, readings, budget.uplinks_per_day, today)
        if node:
            found.append(node)

        plan = plan_irrigation(
            plot,
            depletion,
            taw,
            recent_etc,
            today=today,
            field_capacity_is_prior=soil.field_capacity_is_prior,
        )

        score: dict[str, float] = {}
        if score_against_truth:
            truth = curated.read_frame("ground_truth", plot.plot_id)
            if not truth.empty:
                score["theta_fc_error"] = soil.theta_fc - plot.soil_spec.theta_fc
                length = min(len(truth), len(soil.daily))
                estimated = (
                    (soil.theta_fc - soil.daily["theta_smooth"].to_numpy()[:length])
                    * truth["root_depth_m"].to_numpy()[:length]
                    * 1000.0
                )
                actual = truth["depletion_mm"].to_numpy()[:length].clip(min=0.0)
                estimated = estimated.clip(min=0.0)
                score["depletion_mae_mm"] = float(np.mean(np.abs(estimated - actual)))
                score["depletion_corr"] = float(np.corrcoef(estimated, actual)[0, 1])

        per_plot[plot.plot_id] = PlotAnalysis(
            plot=plot, daily=daily, soil=soil, advisories=found, irrigation=plan, score=score
        )

    result = AnalysisResult(per_plot=per_plot)
    if persist and per_plot:
        frame = result.advisories_frame()
        if not frame.empty:
            curated.write_frame("advisories", frame)
        plans = result.plans_frame()
        if not plans.empty:
            curated.write_frame("irrigation_plans", plans)
    return result
