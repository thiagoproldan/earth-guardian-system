"""What the platform is worth, measured rather than asserted.

Three ways to decide when to irrigate, run over the same year of weather with
the same seed, so the only thing that differs is the decision:

``rainfed``
    No irrigation system at all - the starting point for a farm the product is
    meant to reach.
``calendar``
    A fixed interval and depth. This is what irrigating without instruments
    looks like, and it is the honest comparison: the alternative to this
    platform is not chaos, it is a reasonable habit.
``sensor``
    The platform's own recommendation, driven by the estimated depletion.

The comparison is deliberately unkind to the platform in one respect: the
calendar policy is given a sensible interval and depth rather than a bad one.
Beating a straw man would prove nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd

from earthguardian.config import PLOTS, Plot, get_settings
from earthguardian.edge.simulator import FieldSimulator, IrrigationPolicy
from earthguardian.edge.soil import seasonal_yield_loss


@dataclass(slots=True)
class PolicyOutcome:
    """One policy on one plot, in water, yield and money."""

    plot_id: str
    policy: str
    irrigation_mm: float
    pumped_m3: float
    water_cost_brl: float
    yield_loss: float
    revenue_brl: float
    net_brl: float
    stressed_days: int
    drainage_mm: float
    mean_ks: float


@dataclass(slots=True)
class ImpactStudy:
    start: date
    days: int
    outcomes: list[PolicyOutcome] = field(default_factory=list)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(outcome) for outcome in self.outcomes])

    def compare(self, baseline: str = "calendar", managed: str = "sensor") -> pd.DataFrame:
        """Per-plot difference between two policies."""
        table = self.frame().set_index(["plot_id", "policy"])
        rows = []
        for plot_id in table.index.get_level_values(0).unique():
            base, smart = table.loc[(plot_id, baseline)], table.loc[(plot_id, managed)]
            rows.append(
                {
                    "plot_id": plot_id,
                    "water_saved_m3": base.pumped_m3 - smart.pumped_m3,
                    "water_saved_pct": 100.0
                    * (base.pumped_m3 - smart.pumped_m3)
                    / max(base.pumped_m3, 1e-9),
                    "yield_loss_avoided": base.yield_loss - smart.yield_loss,
                    "net_gain_brl": smart.net_brl - base.net_brl,
                }
            )
        return pd.DataFrame(rows)


def _evaluate(
    plot: Plot, policy_name: str, policy: IrrigationPolicy, start: date, days: int, seed: int
) -> PolicyOutcome:
    _uplinks, truth, _events = FieldSimulator(plot, seed=seed, policy=policy).run(start, days)

    irrigation_mm = float(truth["irrigation_mm"].sum())
    pumped_m3 = irrigation_mm / 1000.0 * plot.area_ha * 10_000
    water_cost = pumped_m3 * plot.water_cost_brl_m3
    loss = seasonal_yield_loss(truth, plot.crop_spec)
    revenue = plot.expected_revenue_brl_ha * plot.area_ha * (1.0 - loss)

    return PolicyOutcome(
        plot_id=plot.plot_id,
        policy=policy_name,
        irrigation_mm=irrigation_mm,
        pumped_m3=pumped_m3,
        water_cost_brl=water_cost,
        yield_loss=loss,
        revenue_brl=revenue,
        net_brl=revenue - water_cost,
        stressed_days=int((truth["ks"] < 1.0).sum()),
        drainage_mm=float(truth["drainage_mm"].sum()),
        mean_ks=float(truth["ks"].mean()),
    )


def run_impact_study(
    start: date, days: int = 365, plots: tuple[Plot, ...] = PLOTS, *, seed: int | None = None
) -> ImpactStudy:
    """Run every policy on every plot over the same year."""
    settings = get_settings()
    seed = settings.seed if seed is None else seed

    policies = {
        "rainfed": IrrigationPolicy.rainfed(),
        "calendar": IrrigationPolicy.calendar(7, 20),
        "sensor": IrrigationPolicy.sensor_driven(0.9),
    }
    study = ImpactStudy(start=start, days=days)
    for plot in plots:
        for name, policy in policies.items():
            study.outcomes.append(_evaluate(plot, name, policy, start, days, seed))
    return study
