"""The water balance has to obey the physics it claims to implement."""

from __future__ import annotations

import numpy as np
import pytest

from earthguardian.config import CROPS, SOILS
from earthguardian.edge import soil


def _step(depletion, soil_spec, **kwargs):
    defaults = dict(
        rain_mm=0.0,
        irrigation_mm=0.0,
        etc_potential_mm=5.0,
        taw_mm=130.0,
        raw_mm=52.0,
        soil=soil_spec,
        root_depth_m=1.0,
    )
    return soil.water_balance_step(depletion, **{**defaults, **kwargs})


def test_available_water_matches_published_ranges():
    """Virginia Cooperative Extension Table 1, in volumetric water content."""
    assert 0.10 <= SOILS["sandy_loam"].taw_mm_per_m / 1000 <= 0.15
    assert 0.10 <= SOILS["clay_loam"].taw_mm_per_m / 1000 <= 0.16
    for spec in SOILS.values():
        assert spec.theta_wp < spec.theta_fc < spec.theta_sat


def test_stress_is_one_until_the_refill_point():
    assert float(soil.stress_coefficient(0.0, 130, 52)) == pytest.approx(1.0)
    assert float(soil.stress_coefficient(52.0, 130, 52)) == pytest.approx(1.0)
    assert float(soil.stress_coefficient(91.0, 130, 52)) == pytest.approx(0.5, abs=0.01)
    assert float(soil.stress_coefficient(130.0, 130, 52)) == pytest.approx(0.0)


def test_stress_never_leaves_the_unit_interval():
    values = soil.stress_coefficient(np.linspace(-50, 300, 100), 130, 52)
    assert (values >= 0).all() and (values <= 1).all()


def test_a_drying_profile_transpires_less_once_stressed():
    spec = SOILS["sandy_loam"]
    depletion, free, stressed = 0.0, [], []
    for _day in range(20):
        depletion, fluxes = _step(depletion, spec)
        (free if fluxes["ks"] == 1.0 else stressed).append(fluxes["etc_actual_mm"])
    assert free and stressed
    assert min(free) > max(stressed)


def test_rain_refills_and_runoff_only_bites_a_wet_soil():
    spec = SOILS["clay"]
    dry = soil.runoff_mm(40.0, depletion_mm=120.0, taw_mm=130.0, soil=spec)
    wet = soil.runoff_mm(40.0, depletion_mm=5.0, taw_mm=130.0, soil=spec)
    assert dry < wet, "a dry profile should absorb more of the same storm"
    assert soil.runoff_mm(5.0, 60.0, 130.0, spec) == 0.0, "light rain does not run off"


def test_drainage_is_fast_in_sand_and_slow_in_clay():
    """Jabro et al. measured negligible drainage at ~50 h in sandy loam and
    ~450 h in clay loam. The shapes must differ in that direction."""
    drained = {}
    for name in ("sandy_loam", "clay_loam", "clay"):
        spec = SOILS[name]
        start = -(spec.theta_sat - spec.theta_fc) * 1000.0  # saturated profile
        depletion = start
        for _day in range(2):
            depletion, _fluxes = _step(depletion, spec, etc_potential_mm=0.0)
        drained[name] = 1.0 - depletion / start

    # Jabro et al: drainage negligible after ~50 h in sandy loam, ~450 h in
    # clay loam. Two days in, the sand should be essentially done and the
    # heavier soils should not be.
    assert drained["sandy_loam"] > 0.9, f"sandy loam only shed {drained['sandy_loam']:.0%}"
    assert drained["clay_loam"] < drained["sandy_loam"]
    assert drained["clay"] < drained["clay_loam"]


def test_the_profile_can_hold_water_above_field_capacity():
    """Without this there is no drainage curve for the estimator to read."""
    spec = SOILS["clay_loam"]
    depletion, _fluxes = _step(0.0, spec, rain_mm=45.0, etc_potential_mm=0.0, root_depth_m=0.4)
    assert depletion < 0.0


def test_depletion_never_exceeds_available_water():
    spec = SOILS["sandy_loam"]
    depletion = 0.0
    for _day in range(400):
        depletion, _fluxes = _step(depletion, spec, etc_potential_mm=8.0)
    assert depletion <= 130.0 + 1e-6


def test_theta_and_depletion_round_trip():
    spec = SOILS["clay_loam"]
    for depletion in (-20.0, 0.0, 35.0, 56.0):
        theta = soil.depletion_to_theta(depletion, spec, 0.4)
        assert float(soil.theta_to_depletion(theta, spec, 0.4)) == pytest.approx(
            depletion, abs=1e-9
        )


def test_yield_loss_follows_fao33():
    crop = CROPS["tomato"]
    assert soil.relative_yield_loss([100.0], [100.0], crop) == pytest.approx(0.0)
    # A 20% evapotranspiration deficit with Ky = 1.05 costs 21% of the harvest.
    assert soil.relative_yield_loss([80.0], [100.0], crop) == pytest.approx(0.21, abs=0.001)
    assert soil.relative_yield_loss([0.0], [100.0], crop) == pytest.approx(1.0)
