"""FAO-56 has published worked values; the implementation has to reproduce them."""

from __future__ import annotations

import numpy as np
import pytest

from earthguardian.config import CROPS
from earthguardian.edge import agromet


def test_saturation_vapour_pressure_matches_fao56():
    """FAO-56 Table 2.3 gives e0(20 C) = 2.338 kPa."""
    assert float(agromet.saturation_vapour_pressure(20.0)) == pytest.approx(2.338, abs=0.001)


def test_saturation_slope_matches_fao56():
    """FAO-56 Annex 2 Table 2.4 gives Delta(20 C) = 0.145 kPa/degC."""
    assert float(agromet.saturation_slope(20.0)) == pytest.approx(0.145, abs=0.001)


def test_atmospheric_pressure_falls_with_altitude():
    assert agromet.atmospheric_pressure(0.0) == pytest.approx(101.3, abs=0.1)
    assert agromet.atmospheric_pressure(2000.0) < agromet.atmospheric_pressure(0.0)


def test_extraterrestrial_radiation_peaks_at_the_equator_near_the_equinox():
    equator = float(agromet.extraterrestrial_radiation(80, 0.0))  # around 21 March
    assert 34.0 < equator < 38.0, f"Ra at the equator should be ~36 MJ/m2/day, got {equator:.1f}"


def test_southern_latitudes_swing_between_seasons():
    summer = float(agromet.extraterrestrial_radiation(15, -23.5))  # January
    winter = float(agromet.extraterrestrial_radiation(172, -23.5))  # June
    assert summer > 1.7 * winter


def test_et0_lands_in_the_physically_sensible_band():
    humid_cool = float(agromet.reference_et0(16, 23, 10, 80, 12.0, 1.5, 190, -23.66, 920))
    hot_dry_windy = float(agromet.reference_et0(28, 35, 21, 35, 24.0, 3.5, 250, -4.17, 910))
    assert 1.0 < humid_cool < 3.5, f"humid winter day: {humid_cool:.2f} mm"
    assert 5.0 < hot_dry_windy < 9.0, f"hot dry day: {hot_dry_windy:.2f} mm"
    assert hot_dry_windy > humid_cool


def test_et0_responds_to_each_driver_in_the_right_direction():
    base = dict(
        temp_mean_c=25,
        temp_max_c=31,
        temp_min_c=19,
        humidity_pct=60,
        solar_mj=20.0,
        wind_ms=2.0,
        day_of_year=200,
        latitude_deg=-15.0,
        altitude_m=800.0,
    )
    reference = float(agromet.reference_et0(**base))
    assert float(agromet.reference_et0(**{**base, "temp_mean_c": 30, "temp_max_c": 36})) > reference
    assert float(agromet.reference_et0(**{**base, "humidity_pct": 90})) < reference
    assert float(agromet.reference_et0(**{**base, "wind_ms": 5.0})) > reference
    assert float(agromet.reference_et0(**{**base, "solar_mj": 10.0})) < reference


def test_et0_is_never_negative():
    values = agromet.reference_et0(
        np.full(12, 5.0),
        np.full(12, 7.0),
        np.full(12, 3.0),
        np.full(12, 98.0),
        np.full(12, 0.5),
        np.full(12, 0.5),
        np.arange(1, 13) * 30.0,
        -30.0,
        0.0,
    )
    assert (values >= 0).all()


def test_crop_coefficient_follows_the_four_stage_curve():
    lettuce = CROPS["lettuce"]
    kc = agromet.crop_coefficient(lettuce, np.array([0, 10, 35, 55, 65, 74]))
    assert kc[0] == pytest.approx(lettuce.kc_ini)
    assert kc[3] == pytest.approx(lettuce.kc_mid, abs=0.01)
    assert kc[-1] == pytest.approx(lettuce.kc_end, abs=0.05)
    assert kc.max() <= lettuce.kc_mid + 1e-9


def test_roots_grow_for_annuals_and_stay_put_for_perennials():
    lettuce = agromet.root_depth(CROPS["lettuce"], np.array([0, 25, 60]))
    assert lettuce[0] < lettuce[1] < lettuce[2]
    coffee = agromet.root_depth(CROPS["coffee"], np.array([0, 100, 300]))
    assert np.allclose(coffee, CROPS["coffee"].root_depth_m)


def test_lux_conversion_is_monotonic_and_zero_at_night():
    assert float(agromet.solar_radiation_from_lux(0.0, 15)) == 0.0
    assert float(agromet.solar_radiation_from_lux(80_000, 15)) > float(
        agromet.solar_radiation_from_lux(20_000, 15)
    )
