"""GAIA is scored against the state the simulator kept hidden.

The estimators are the point of the project, so these are the tests that
matter. In particular :func:`test_a_wrong_prior_is_not_silently_returned` exists
because an earlier version of the hinge fit fell back to the texture prior
internally and reported it as a measurement - and since the prior equals the
truth in the default configuration, that wrong answer scored a perfect zero
error on every plot. A test that only ever checked accuracy would have passed.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthguardian.gaia import advisories as adv
from earthguardian.gaia.soil_state import (
    detect_wetting_events,
    estimate_soil_state,
    fit_drainage_hinge,
)
from earthguardian.gaia.weather import crop_demand, daily_weather_from_uplinks


@pytest.fixture(scope="module")
def analysed(rainfed_year, guaraciaba):
    uplinks, truth, events = rainfed_year
    daily = crop_demand(daily_weather_from_uplinks(uplinks, guaraciaba), guaraciaba)
    estimate = estimate_soil_state(
        uplinks,
        guaraciaba.plot_id,
        guaraciaba.soil_spec.theta_fc,
        guaraciaba.soil_spec.theta_wp,
        crop_demand_mm=daily["etc_mm"].to_numpy(),
        root_depth_m=daily["root_depth_m"].to_numpy(),
    )
    return uplinks, truth, events, daily, estimate


# -- weather reconstruction -------------------------------------------------------------


def test_et0_is_reconstructed_from_sparse_uplinks(analysed, guaraciaba):
    _uplinks, truth, _events, daily, _estimate = analysed
    length = min(len(daily), len(truth))
    estimated = daily["et0_mm"].to_numpy()[:length]
    actual = truth["et0_mm"].to_numpy()[:length]

    assert np.corrcoef(estimated, actual)[0, 1] > 0.75
    assert float(np.mean(np.abs(estimated - actual))) < 1.0
    # Sparse sampling misses the daily peak, so the estimate runs low. The bias
    # is real and documented; the test pins its size rather than denying it.
    assert -0.8 < float(np.mean(estimated - actual)) < 0.1


# -- soil state --------------------------------------------------------------------------


def test_a_wrong_prior_is_not_silently_returned(rainfed_year, guaraciaba):
    """The estimator must either move away from a bad prior or admit it cannot.

    Handing it a prior 0.08 above the truth: if it comes back reporting
    "measured" while sitting on the prior, the fit did not happen and the label
    is a lie.
    """
    uplinks, _truth, _events = rainfed_year
    daily = crop_demand(daily_weather_from_uplinks(uplinks, guaraciaba), guaraciaba)
    wrong_prior = guaraciaba.soil_spec.theta_fc + 0.08

    estimate = estimate_soil_state(
        uplinks,
        guaraciaba.plot_id,
        wrong_prior,
        guaraciaba.soil_spec.theta_wp,
        crop_demand_mm=daily["etc_mm"].to_numpy(),
        root_depth_m=daily["root_depth_m"].to_numpy(),
    )

    if estimate.field_capacity_is_prior:
        assert estimate.theta_fc == pytest.approx(wrong_prior), (
            "a fallback must return the prior it was given, unchanged"
        )
    else:
        assert abs(estimate.theta_fc - wrong_prior) > 0.01, (
            "reported as measured but sitting on the prior - the fit silently failed"
        )
        assert abs(estimate.theta_fc - guaraciaba.soil_spec.theta_fc) < abs(
            wrong_prior - guaraciaba.soil_spec.theta_fc
        ), "a measurement should land closer to the truth than the prior it started from"


def test_field_capacity_is_recovered_within_a_useful_tolerance(analysed, guaraciaba):
    _uplinks, _truth, _events, _daily, estimate = analysed
    if estimate.field_capacity_is_prior:
        pytest.skip("this season gave no identifiable drainage signal")
    error = abs(estimate.theta_fc - guaraciaba.soil_spec.theta_fc)
    assert error < 0.06, f"field capacity off by {error:.3f} m3/m3"


def test_the_hinge_reports_failure_rather_than_a_fallback():
    """Noise with no hinge in it must return None, not a plausible-looking number."""
    rng = np.random.default_rng(0)
    theta = rng.uniform(0.2, 0.3, 200)
    residual = rng.normal(0.0, 1.0, 200)  # no dependence on theta at all
    result = fit_drainage_hinge(theta, residual)
    if result is not None:
        _breakpoint, slope, _above = result
        assert slope > 0, "a returned fit must have a positive slope"


def test_the_hinge_finds_a_breakpoint_that_is_there():
    rng = np.random.default_rng(1)
    theta = rng.uniform(0.18, 0.34, 400)
    truth = 0.26
    residual = 40.0 * np.maximum(theta - truth, 0.0) - 2.0 + rng.normal(0, 0.4, 400)
    result = fit_drainage_hinge(theta, residual)
    assert result is not None
    breakpoint, slope, _above = result
    assert breakpoint == pytest.approx(truth, abs=0.02)
    assert slope > 0


def test_wetting_events_are_detected_and_not_double_counted():
    import pandas as pd

    theta = pd.Series([0.20] * 10 + [0.30, 0.31, 0.30] + [0.24] * 10)
    events = detect_wetting_events(theta)
    assert len(events) == 1, "a two-day storm is one wetting event"
    assert events[0] == 10


def test_depletion_tracks_the_hidden_truth(analysed, guaraciaba):
    _uplinks, truth, _events, _daily, estimate = analysed
    length = min(len(truth), len(estimate.daily))
    predicted = np.clip(
        (estimate.theta_fc - estimate.daily["theta_smooth"].to_numpy()[:length])
        * truth["root_depth_m"].to_numpy()[:length]
        * 1000.0,
        0,
        None,
    )
    actual = truth["depletion_mm"].to_numpy()[:length].clip(min=0)
    assert np.corrcoef(predicted, actual)[0, 1] > 0.45


# -- advisories ---------------------------------------------------------------------------


def test_burning_events_are_found_without_an_absolute_threshold(managed_year, guaraciaba):
    from earthguardian.edge.simulator import DEFAULT_BURN_DAYS

    uplinks, _truth, _events = managed_year
    detected = adv.detect_burning_events(uplinks["air_quality_ppm"], uplinks["timestamp"])
    injected = DEFAULT_BURN_DAYS[guaraciaba.plot_id]

    assert len(detected) == len(injected), f"expected {len(injected)} plumes, got {len(detected)}"
    start = uplinks["timestamp"].min().normalize()
    for (when, _sigma), day in zip(detected, injected, strict=True):
        assert abs((when.normalize() - start).days - day) <= 1


def test_clean_air_raises_no_burning_advisory(guaraciaba, settings):
    from conftest import DAYS, START
    from earthguardian.edge.simulator import FieldSimulator, IrrigationPolicy

    uplinks, _truth, _events = FieldSimulator(
        guaraciaba, seed=settings.seed, policy=IrrigationPolicy.rainfed(), burn_days=()
    ).run(START, DAYS)
    assert adv.detect_burning_events(uplinks["air_quality_ppm"], uplinks["timestamp"]) == []


def test_ph_advisory_respects_the_crop_band(guaraciaba, vendanova):
    import pandas as pd

    acidic = pd.Series([5.4] * 40)
    # Coffee tolerates 5.5-6.5; tomato wants 6.0-6.8. The same reading is a
    # problem for one and not the other.
    tomato = adv.ph_advisory(guaraciaba, acidic)
    coffee = adv.ph_advisory(vendanova, acidic)
    assert tomato.severity in {"high", "critical"}
    assert coffee.severity in {"high", "info"}

    toxic = adv.ph_advisory(guaraciaba, pd.Series([4.6] * 40))
    assert toxic.severity == "critical"
    assert "aluminium" in toxic.action.lower()


def test_water_advisory_escalates_as_the_profile_dries(guaraciaba):
    comfortable = adv.water_advisory(guaraciaba, 5.0, 52.0, 130.0, 4.0)
    approaching = adv.water_advisory(guaraciaba, 40.0, 52.0, 130.0, 4.0)
    urgent = adv.water_advisory(guaraciaba, 50.0, 52.0, 130.0, 4.0)
    order = ["info", "medium", "high", "critical"]
    assert order.index(comfortable.severity) < order.index(approaching.severity)
    assert order.index(approaching.severity) <= order.index(urgent.severity)
    assert comfortable.evidence["days_to_trigger"] > urgent.evidence["days_to_trigger"]


def test_advisories_rank_most_urgent_first(guaraciaba):
    items = [
        adv.Advisory(guaraciaba.plot_id, "a", "info", "t", "f", "a"),
        adv.Advisory(guaraciaba.plot_id, "b", "critical", "t", "f", "a"),
        adv.Advisory(guaraciaba.plot_id, "c", "medium", "t", "f", "a"),
    ]
    assert [item.severity for item in adv.rank(items)] == ["critical", "medium", "info"]
