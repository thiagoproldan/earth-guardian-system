"""The root-zone water balance, following FAO-56 Chapter 8.

The soil is treated as one reservoir the depth of the crop's roots. Rain and
irrigation fill it, evapotranspiration empties it, and anything above field
capacity drains away. The state variable is **depletion** ``Dr`` - millimetres
of water missing from a full profile - because that is what irrigation has to
replace and what stress depends on.

.. math::

    D_{r,i} = D_{r,i-1} - (P - RO)_i - I_i + ET_{c,i} + DP_i

Two thresholds matter:

``TAW``
    Total available water: everything between field capacity and wilting point.
``RAW``
    Readily available water, ``p x TAW``. Up to this point the crop transpires
    freely; past it the stomata start closing and yield begins to suffer.

References
----------
FAO-56 Chapter 8 (soil water balance, eqs. 84-86, 94) and FAO-33 (yield
response to water).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from earthguardian.config import Crop, Plot, Soil

#: Depletion left immediately after an irrigation event targeting field capacity.
IRRIGATION_TARGET_DEPLETION_MM = 0.0


# --------------------------------------------------------------------------------------
# Conversions between what the probe reads and what the balance tracks
# --------------------------------------------------------------------------------------


def theta_to_depletion(
    theta: np.ndarray | float, soil: Soil, root_depth_m: np.ndarray | float
) -> np.ndarray:
    """Root-zone depletion (mm) implied by a volumetric water content.

    This is the bridge between the sensor and the model: the probe reports
    ``theta`` at one depth, and the balance needs millimetres over the profile.
    Treating the single reading as representative of the whole root zone is the
    assumption the whole reconstruction rests on, and
    :mod:`earthguardian.gaia.soil_state` is where it gets tested.
    """
    theta = np.asarray(theta, dtype=float)
    depth = np.asarray(root_depth_m, dtype=float)
    # Not clipped at zero: a freshly wetted profile is genuinely above field
    # capacity, and that excess is the drainage signal.
    return (soil.theta_fc - theta) * depth * 1000.0


def depletion_to_theta(
    depletion_mm: np.ndarray | float, soil: Soil, root_depth_m: np.ndarray | float
) -> np.ndarray:
    """Inverse of :func:`theta_to_depletion`."""
    depletion = np.asarray(depletion_mm, dtype=float)
    depth = np.maximum(np.asarray(root_depth_m, dtype=float), 1e-6)
    return soil.theta_fc - depletion / (depth * 1000.0)


def total_available_water(soil: Soil, root_depth_m: float) -> float:
    return soil.taw_mm_per_m * root_depth_m


def readily_available_water(soil: Soil, crop: Crop, root_depth_m: float) -> float:
    return total_available_water(soil, root_depth_m) * crop.depletion_fraction


# --------------------------------------------------------------------------------------
# Stress
# --------------------------------------------------------------------------------------


def stress_coefficient(
    depletion_mm: np.ndarray | float, taw_mm: np.ndarray | float, raw_mm: np.ndarray | float
) -> np.ndarray:
    """Water stress coefficient ``Ks`` in ``[0, 1]`` (FAO-56 eq. 84).

    One while there is readily available water left, then falling linearly to
    zero at the wilting point. Multiplying ``Kc`` by it is what turns potential
    demand into what the crop actually manages to take up.
    """
    depletion = np.asarray(depletion_mm, dtype=float)
    taw = np.asarray(taw_mm, dtype=float)
    raw = np.asarray(raw_mm, dtype=float)

    denominator = np.maximum(taw - raw, 1e-9)
    ks = np.where(depletion <= raw, 1.0, (taw - depletion) / denominator)
    return np.clip(ks, 0.0, 1.0)  # a profile above field capacity is not stressed


def seasonal_yield_loss(frame, crop: Crop) -> float:
    """Mean yield loss across the plantings in a record.

    FAO-33 defines its response function over a growing season, so a year
    holding four lettuce cycles has four deficits to answer for, not one. The
    harvests are averaged because that is what the farmer sells.
    """
    if "cycle" not in frame:
        return relative_yield_loss(frame["etc_actual_mm"], frame["etc_potential_mm"], crop)
    losses = [
        relative_yield_loss(group["etc_actual_mm"], group["etc_potential_mm"], crop)
        for _cycle, group in frame.groupby("cycle")
        # A partial cycle at the end of the record has not been harvested yet.
        if len(group) >= 0.6 * (365 if crop.perennial else crop.season_days)
    ]
    return float(np.mean(losses)) if losses else 0.0


def relative_yield_loss(
    et_actual: np.ndarray | float, et_potential: np.ndarray | float, crop: Crop
) -> float:
    """Seasonal yield loss fraction from the FAO-33 response function.

    .. math:: 1 - Y_a/Y_m = K_y \\left(1 - \\sum ET_a / \\sum ET_c\\right)

    Applied over the whole season rather than per stage - the stage-wise form
    needs per-stage ``Ky`` values that FAO-33 only tabulates for some crops.
    """
    actual = float(np.sum(et_actual))
    potential = float(np.sum(et_potential))
    if potential <= 0:
        return 0.0
    deficit = max(0.0, 1.0 - actual / potential)
    return float(np.clip(crop.yield_response_ky * deficit, 0.0, 1.0))


# --------------------------------------------------------------------------------------
# The balance itself
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class WaterBalanceDay:
    """Everything that happened to the reservoir on one day."""

    day: int
    depletion_mm: float
    taw_mm: float
    raw_mm: float
    root_depth_m: float
    kc: float
    et0_mm: float
    etc_potential_mm: float
    etc_actual_mm: float
    ks: float
    rain_mm: float
    runoff_mm: float
    irrigation_mm: float
    drainage_mm: float
    theta: float


@dataclass(slots=True)
class WaterBalance:
    """A season of daily balances, plus the summary numbers that price it."""

    plot_id: str
    days: list[WaterBalanceDay] = field(default_factory=list)

    def series(self, attribute: str) -> np.ndarray:
        return np.array([getattr(day, attribute) for day in self.days], dtype=float)

    @property
    def irrigation_total_mm(self) -> float:
        return float(self.series("irrigation_mm").sum())

    @property
    def stressed_days(self) -> int:
        return int((self.series("ks") < 1.0).sum())

    @property
    def mean_ks(self) -> float:
        return float(self.series("ks").mean()) if self.days else 1.0


#: Rain a dry soil absorbs before any of it runs off, millimetres. The SCS
#: curve-number method calls this the initial abstraction; it is why the first
#: storm after a drought produces almost no runoff and the third one floods.
INITIAL_ABSTRACTION_MM = 8.0


def runoff_mm(rain_mm: float, depletion_mm: float, taw_mm: float, soil: Soil) -> float:
    """Surface runoff, scaled by how much room the profile still has.

    A flat fraction of every rainfall event - the obvious model, and the first
    one tried here - charges a dry soil the same loss as a saturated one. On a
    clay plot in a dry season that removed a third of the rain the crop was
    counting on, and made rainfed coffee look unviable when it plainly is not.
    """
    if rain_mm <= INITIAL_ABSTRACTION_MM:
        return 0.0
    wetness = float(np.clip(1.0 - depletion_mm / max(taw_mm, 1e-6), 0.0, 1.0))
    return (rain_mm - INITIAL_ABSTRACTION_MM) * soil.runoff_fraction * wetness


def water_balance_step(
    depletion_mm: float,
    *,
    rain_mm: float,
    irrigation_mm: float,
    etc_potential_mm: float,
    taw_mm: float,
    raw_mm: float,
    soil: Soil,
    root_depth_m: float = 1.0,
) -> tuple[float, dict[str, float]]:
    """Advance the reservoir by one day.

    Returns the new depletion and the fluxes, in the order they physically
    happen: rain arrives and some runs off, irrigation is applied, the crop
    transpires what it can, and any excess drains past the roots.
    """
    runoff = runoff_mm(rain_mm, depletion_mm, taw_mm, soil)
    infiltration = rain_mm - runoff + irrigation_mm

    # Stress is evaluated on the depletion the crop wakes up to.
    ks = float(stress_coefficient(depletion_mm, taw_mm, raw_mm))
    etc_actual = etc_potential_mm * ks

    depletion = depletion_mm - infiltration + etc_actual

    # Water beyond field capacity is not held, but neither does it disappear at
    # midnight: it drains under gravity with a texture-dependent time constant.
    # Treating drainage as instantaneous - the classic tipping-bucket
    # simplification - makes field capacity a hard ceiling the water content
    # can never exceed, which removes the very drainage curve a sensor would
    # use to calibrate itself.
    # The profile can hold water above field capacity, up to saturation.
    max_excess = (soil.theta_sat - soil.theta_fc) * root_depth_m * 1000.0

    drainage = 0.0
    if depletion < 0.0:
        excess = min(-depletion, max_excess)
        # Unsaturated hydraulic conductivity collapses as the large pores empty.
        # A saturated profile drains at close to Ksat and is done in a day; the
        # last few millimetres above field capacity take weeks. A single
        # exponential relaxation cannot express both, and choosing its time
        # constant to match the slow tail leaves a clay loam permanently
        # waterlogged - which is what happened before this was a power law.
        relative = excess / max(max_excess, 1e-9)
        drainage = float(min(soil.ksat_mm_day * relative**soil.drainage_exponent, excess))
        depletion += drainage

    depletion = float(np.clip(depletion, -max_excess, taw_mm))

    return depletion, {
        "runoff_mm": runoff,
        "drainage_mm": drainage,
        "etc_actual_mm": etc_actual,
        "ks": ks,
    }


def initial_depletion(plot: Plot, fraction_of_raw: float = 0.5) -> float:
    """A plausible starting point: partway into the readily available water."""
    return plot.raw_mm * fraction_of_raw
