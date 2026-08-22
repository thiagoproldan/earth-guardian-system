"""The irrigation decision.

Everything upstream exists to answer two questions the farmer actually asks:
*do I irrigate, and how much?* The answer is a depletion compared against the
crop's allowable depletion, and a depth that refills the profile without
overfilling it.

The estimate that feeds this comes from GAIA, which recovered the soil's field
capacity from its own drainage behaviour where the season allowed and fell back
to a texture prior where it did not. Where the prior is in play the plan says
so, because a recommendation built on a lookup table deserves less confidence
than one built on the plot's own measurements - and the person deciding whether
to run a pump for four hours is entitled to know which they are getting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from earthguardian.config import Plot


@dataclass(slots=True)
class IrrigationPlan:
    """What to do, when, and how sure we are."""

    plot_id: str
    decision: str  # "irrigate_now" | "irrigate_soon" | "hold"
    depth_mm: float
    volume_m3: float
    #: Water actually pumped, after system losses.
    pumped_m3: float
    cost_brl: float
    due_on: date | None
    days_to_trigger: float
    depletion_mm: float
    raw_mm: float
    taw_mm: float
    confidence: str  # "measured" | "prior"
    rationale: str

    @property
    def is_actionable(self) -> bool:
        return self.decision != "hold"


def plan_irrigation(
    plot: Plot,
    depletion_mm: float,
    taw_mm: float,
    recent_etc_mm_day: float,
    *,
    today: date,
    field_capacity_is_prior: bool,
    refill_buffer_fraction: float = 0.15,
) -> IrrigationPlan:
    """Decide whether to irrigate this plot, and by how much."""
    raw_mm = taw_mm * plot.crop_spec.depletion_fraction
    trigger_mm = raw_mm * 0.9

    headroom = max(trigger_mm - depletion_mm, 0.0)
    days_to_trigger = headroom / max(recent_etc_mm_day, 1e-6)

    # Refill towards field capacity but deliberately short of it, so rain in the
    # next few days has room to infiltrate rather than drain past the roots.
    target_depletion = taw_mm * refill_buffer_fraction
    depth_mm = max(depletion_mm - target_depletion, 0.0)

    delivered_m3 = depth_mm / 1000.0 * plot.area_ha * 10_000
    pumped_m3 = delivered_m3 / max(plot.irrigation_efficiency, 1e-6)
    cost = pumped_m3 * plot.water_cost_brl_m3

    if depletion_mm >= trigger_mm:
        decision, due = "irrigate_now", today
    elif days_to_trigger <= 2.0:
        decision, due = "irrigate_soon", today + timedelta(days=int(np.ceil(days_to_trigger)))
    else:
        decision, due = "hold", today + timedelta(days=int(np.ceil(days_to_trigger)))
        depth_mm, delivered_m3, pumped_m3, cost = 0.0, 0.0, 0.0, 0.0

    confidence = "prior" if field_capacity_is_prior else "measured"
    caveat = (
        " Field capacity could not be recovered from this plot's drainage behaviour, so "
        "the depletion is computed against a texture-book value; treat the depth as "
        "indicative and check the soil by hand."
        if field_capacity_is_prior
        else ""
    )
    rationale = (
        f"Depletion {depletion_mm:.0f} mm against a {trigger_mm:.0f} mm trigger "
        f"({plot.crop_spec.depletion_fraction:.0%} of {taw_mm:.0f} mm available), with demand "
        f"at {recent_etc_mm_day:.1f} mm/day.{caveat}"
    )

    return IrrigationPlan(
        plot_id=plot.plot_id,
        decision=decision,
        depth_mm=depth_mm,
        volume_m3=delivered_m3,
        pumped_m3=pumped_m3,
        cost_brl=cost,
        due_on=due,
        days_to_trigger=days_to_trigger,
        depletion_mm=depletion_mm,
        raw_mm=raw_mm,
        taw_mm=taw_mm,
        confidence=confidence,
        rationale=rationale,
    )
