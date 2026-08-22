"""Agrometeorology, following FAO-56.

Reference evapotranspiration is the quantity the whole system turns on: it is
how much water the atmosphere will pull out of a well-watered reference crop
today, and therefore how fast the soil the sensor is sitting in will dry.

The implementation is the standard FAO-56 Penman-Monteith combination equation.
Every symbol keeps the name the paper gives it, so the code can be read next to
the reference:

.. math::

    ET_0 = \\frac{0.408\\,\\Delta (R_n - G) + \\gamma \\frac{900}{T+273} u_2 (e_s - e_a)}
                 {\\Delta + \\gamma (1 + 0.34 u_2)}

Reference
---------
Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). *Crop
evapotranspiration - Guidelines for computing crop water requirements*. FAO
Irrigation and Drainage Paper 56.
"""

from __future__ import annotations

import numpy as np

from earthguardian.config import Crop

#: Solar constant, MJ m^-2 min^-1 (FAO-56 eq. 28).
SOLAR_CONSTANT = 0.0820
#: Stefan-Boltzmann constant, MJ K^-4 m^-2 day^-1 (FAO-56 eq. 39).
STEFAN_BOLTZMANN = 4.903e-9
#: Albedo of the hypothetical grass reference surface (FAO-56 eq. 38).
REFERENCE_ALBEDO = 0.23

ArrayLike = np.ndarray | float


# --------------------------------------------------------------------------------------
# Radiation
# --------------------------------------------------------------------------------------


def extraterrestrial_radiation(day_of_year: ArrayLike, latitude_deg: float) -> np.ndarray:
    """Daily extraterrestrial radiation ``Ra`` in MJ/m2/day (FAO-56 eq. 21).

    The energy arriving at the top of the atmosphere. Everything measured at the
    ground is a fraction of this, which is what makes it the natural yardstick
    for deciding whether a day was clear or cloudy.
    """
    doy = np.asarray(day_of_year, dtype=float)
    phi = np.radians(latitude_deg)

    dr = 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)  # inverse relative distance
    delta = 0.409 * np.sin(2.0 * np.pi * doy / 365.0 - 1.39)  # solar declination
    # Sunset hour angle, clipped so polar day and polar night stay finite.
    omega_s = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))

    return (
        24.0
        * 60.0
        / np.pi
        * SOLAR_CONSTANT
        * dr
        * (omega_s * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(omega_s))
    )


def clear_sky_radiation(ra: ArrayLike, altitude_m: float) -> np.ndarray:
    """Clear-sky solar radiation ``Rso`` in MJ/m2/day (FAO-56 eq. 37)."""
    return (0.75 + 2e-5 * altitude_m) * np.asarray(ra, dtype=float)


#: Luminous efficacy of daylight, lumens per watt. A photometric sensor measures
#: what the eye sees, not what a crop receives, and the conversion is a single
#: coefficient that varies with solar elevation and cloud - roughly 105-125 for
#: daylight. This is the largest approximation in the pipeline and the reason
#: the thesis's luminosity channel is a proxy for a pyranometer, not a
#: replacement for one.
DAYLIGHT_LUMINOUS_EFFICACY = 112.0


def solar_radiation_from_lux(
    lux: ArrayLike, interval_minutes: float, efficacy: float = DAYLIGHT_LUMINOUS_EFFICACY
) -> np.ndarray:
    """Convert a photometric reading to radiant energy over the interval, MJ/m2.

    ``lux / efficacy`` gives W/m2; multiplying by the interval and converting to
    megajoules gives the energy the interval delivered.
    """
    irradiance_w_m2 = np.asarray(lux, dtype=float) / efficacy
    return irradiance_w_m2 * (interval_minutes * 60.0) / 1e6


# --------------------------------------------------------------------------------------
# Psychrometrics
# --------------------------------------------------------------------------------------


def atmospheric_pressure(altitude_m: float) -> float:
    """Atmospheric pressure in kPa from elevation (FAO-56 eq. 7)."""
    return 101.3 * ((293.0 - 0.0065 * altitude_m) / 293.0) ** 5.26


def psychrometric_constant(altitude_m: float) -> float:
    """Psychrometric constant in kPa/degC (FAO-56 eq. 8)."""
    return 0.000665 * atmospheric_pressure(altitude_m)


def saturation_vapour_pressure(temp_c: ArrayLike) -> np.ndarray:
    """Saturation vapour pressure in kPa (FAO-56 eq. 11)."""
    t = np.asarray(temp_c, dtype=float)
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def saturation_slope(temp_c: ArrayLike) -> np.ndarray:
    """Slope of the vapour pressure curve, kPa/degC (FAO-56 eq. 13)."""
    t = np.asarray(temp_c, dtype=float)
    return 4098.0 * saturation_vapour_pressure(t) / np.power(t + 237.3, 2)


# --------------------------------------------------------------------------------------
# Reference evapotranspiration
# --------------------------------------------------------------------------------------


def net_radiation(
    solar_mj: ArrayLike,
    ra: ArrayLike,
    temp_max_c: ArrayLike,
    temp_min_c: ArrayLike,
    actual_vapour_kpa: ArrayLike,
    altitude_m: float,
) -> np.ndarray:
    """Net radiation ``Rn`` in MJ/m2/day (FAO-56 eqs. 38-40)."""
    solar_mj = np.asarray(solar_mj, dtype=float)
    net_shortwave = (1.0 - REFERENCE_ALBEDO) * solar_mj

    rso = np.maximum(clear_sky_radiation(ra, altitude_m), 1e-6)
    # Cloudiness factor, bounded: on a heavily overcast day the ratio collapses
    # and the uncorrected formula would emit net longwave of the wrong sign.
    cloudiness = np.clip(1.35 * np.clip(solar_mj / rso, 0.0, 1.0) - 0.35, 0.05, 1.0)

    tmax_k4 = np.power(np.asarray(temp_max_c, dtype=float) + 273.16, 4)
    tmin_k4 = np.power(np.asarray(temp_min_c, dtype=float) + 273.16, 4)
    emissivity = 0.34 - 0.14 * np.sqrt(np.maximum(actual_vapour_kpa, 0.0))

    net_longwave = STEFAN_BOLTZMANN * (tmax_k4 + tmin_k4) / 2.0 * emissivity * cloudiness
    return net_shortwave - net_longwave


def reference_et0(
    temp_mean_c: ArrayLike,
    temp_max_c: ArrayLike,
    temp_min_c: ArrayLike,
    humidity_pct: ArrayLike,
    solar_mj: ArrayLike,
    wind_ms: ArrayLike,
    day_of_year: ArrayLike,
    latitude_deg: float,
    altitude_m: float,
) -> np.ndarray:
    """FAO-56 Penman-Monteith reference evapotranspiration, mm/day (eq. 6).

    Returns the depth of water a well-watered short grass would transpire in a
    day - typically 1-3 mm in a humid winter and 5-8 mm in a hot, dry, windy
    summer.
    """
    temp_mean = np.asarray(temp_mean_c, dtype=float)
    delta = saturation_slope(temp_mean)
    gamma = psychrometric_constant(altitude_m)

    # Vapour pressure deficit, from the daily extremes as FAO-56 prefers.
    es = (saturation_vapour_pressure(temp_max_c) + saturation_vapour_pressure(temp_min_c)) / 2.0
    ea = es * np.clip(np.asarray(humidity_pct, dtype=float), 0.0, 100.0) / 100.0
    vpd = np.maximum(es - ea, 0.0)

    ra = extraterrestrial_radiation(day_of_year, latitude_deg)
    rn = net_radiation(solar_mj, ra, temp_max_c, temp_min_c, ea, altitude_m)

    # Soil heat flux is negligible at daily resolution (FAO-56 eq. 42).
    soil_heat = 0.0
    # The reference is defined at 2 m; below 0.5 m/s the equation misbehaves.
    u2 = np.clip(np.asarray(wind_ms, dtype=float), 0.5, None)

    numerator = 0.408 * delta * (rn - soil_heat) + gamma * (900.0 / (temp_mean + 273.0)) * u2 * vpd
    denominator = delta + gamma * (1.0 + 0.34 * u2)
    return np.maximum(numerator / denominator, 0.0)


# --------------------------------------------------------------------------------------
# Crop demand
# --------------------------------------------------------------------------------------


def crop_coefficient(crop: Crop, days_after_planting: ArrayLike) -> np.ndarray:
    """The FAO-56 ``Kc`` curve: four stages, linear ramps between them.

    A seedling shades little soil and transpires little; a closed canopy at peak
    growth transpires more than the reference grass. ``Kc`` is that ratio.
    """
    day = np.asarray(days_after_planting, dtype=float)
    ini, dev, mid, late = crop.stage_days

    kc = np.full(day.shape, crop.kc_ini, dtype=float)
    # Development: ramp from initial to mid-season.
    in_dev = (day >= ini) & (day < ini + dev)
    kc = np.where(in_dev, crop.kc_ini + (crop.kc_mid - crop.kc_ini) * (day - ini) / max(dev, 1), kc)
    # Mid-season plateau.
    kc = np.where((day >= ini + dev) & (day < ini + dev + mid), crop.kc_mid, kc)
    # Late season: ramp down to harvest.
    in_late = day >= ini + dev + mid
    kc = np.where(
        in_late,
        crop.kc_mid
        + (crop.kc_end - crop.kc_mid) * np.clip((day - ini - dev - mid) / max(late, 1), 0.0, 1.0),
        kc,
    )
    return kc


def root_depth(crop: Crop, days_after_planting: ArrayLike) -> np.ndarray:
    """Effective rooting depth in metres, growing to its maximum by mid-season.

    A crop cannot drink from soil it has not reached yet, so the available water
    a seedling actually has is a fraction of what the mature plant will have.
    """
    day = np.asarray(days_after_planting, dtype=float)
    if crop.perennial:
        return np.full(day.shape, crop.root_depth_m, dtype=float)
    ini, dev, _mid, _late = crop.stage_days
    fraction = np.clip(0.25 + 0.75 * day / max(ini + dev, 1), 0.25, 1.0)
    return crop.root_depth_m * fraction
