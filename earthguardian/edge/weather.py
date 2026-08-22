"""Synthetic agrometeorology.

The water balance is only as believable as the weather driving it. Independent
daily draws would produce a climate where a drought never lasts a week, and a
drought that never lasts a week is a climate where irrigation scheduling does
not matter. So the generator is autocorrelated and strongly seasonal, and it is
calibrated to each plot's annual rainfall and wet-season timing.

Nothing here is fitted to observations. It is built to be *structurally* right -
dry spells cluster, clear days are hot and swing wide between night and
afternoon, humid days do not - which is what the estimators downstream depend
on. It is not a forecast for any real municipality.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from math import erf as _erf

import numpy as np

from earthguardian.config import Plot
from earthguardian.edge import agromet


def plot_rng(plot_id: str, seed: int, stream: str = "") -> np.random.Generator:
    """Deterministic, independent random stream per (plot, purpose)."""
    tag = zlib.crc32(f"{plot_id}|{stream}".encode())
    return np.random.default_rng(np.random.SeedSequence([seed, tag]))


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    """First-order autoregressive noise - today resembles yesterday."""
    noise = rng.normal(0.0, sigma, size=n)
    out = np.empty(n)
    out[0] = noise[0] / np.sqrt(max(1.0 - phi * phi, 1e-6))
    for i in range(1, n):
        out[i] = phi * out[i - 1] + noise[i]
    return out


def _seasonal(day_of_year: np.ndarray, peak_month: int) -> np.ndarray:
    """Cosine in ``[-1, 1]`` peaking mid-way through ``peak_month``."""
    peak_day = (peak_month - 1) * 30.44 + 15.0
    return np.cos(2.0 * np.pi * (day_of_year - peak_day) / 365.25)


@dataclass(slots=True)
class DailyWeather:
    """One row per day - the scale the water balance runs at."""

    day_of_year: np.ndarray
    temp_mean_c: np.ndarray
    temp_max_c: np.ndarray
    temp_min_c: np.ndarray
    humidity_pct: np.ndarray
    wind_ms: np.ndarray
    rain_mm: np.ndarray
    #: Fraction of clear-sky radiation that reached the ground.
    clearness: np.ndarray
    solar_mj: np.ndarray
    et0_mm: np.ndarray

    def __len__(self) -> int:
        return len(self.day_of_year)


def generate_daily_weather(plot: Plot, day_of_year: np.ndarray, seed: int) -> DailyWeather:
    """Draw a year of weather for one plot and evaluate FAO-56 ET0 on it."""
    rng = plot_rng(plot.plot_id, seed, "weather")
    n = len(day_of_year)
    wet = _seasonal(day_of_year, plot.wet_season_peak_month)

    # --- rain: seasonal, clustered, calibrated to the plot's annual total ----
    # A single global threshold on a strongly seasonal score - the obvious
    # construction - produces five consecutive months of *exactly zero* rain,
    # because dry-season days never clear the bar. Real dry seasons are thin,
    # not empty, and a plot that never sees an off-season shower is a plot
    # where the soil buffer never gets a partial refill. So the rain
    # probability itself varies with the season and is floored well above zero.
    drive = _ar1(rng, n, phi=0.42, sigma=1.0)
    uniform = 0.5 * (1.0 + np.vectorize(_erf)(drive / (np.std(drive) * np.sqrt(2.0))))

    base_probability = float(np.clip(plot.annual_rainfall_mm / 3600.0, 0.06, 0.50))
    seasonal_multiplier = np.clip(1.0 + 1.15 * wet, 0.22, 2.6)
    rain_probability = np.clip(base_probability * seasonal_multiplier, 0.02, 0.92)
    rains = uniform < rain_probability

    # Wet-season storms are heavier, not just more frequent.
    depth = rng.gamma(shape=1.8, scale=1.0, size=n) * np.clip(1.0 + 0.7 * wet, 0.4, 2.0)
    rain = np.where(rains, depth, 0.0)
    if rain.sum() > 0:  # scale to the target annual depth
        rain *= plot.annual_rainfall_mm / rain.sum() * (n / 365.0)

    # --- cloudiness -----------------------------------------------------------
    clearness = 0.72 - 0.10 * wet + _ar1(rng, n, phi=0.5, sigma=0.30) * 0.16
    clearness = np.where(rains, clearness - 0.24, clearness)
    clearness = np.clip(clearness, 0.18, 0.92)

    # --- temperature ----------------------------------------------------------
    equatorial = 1.0 - min(abs(plot.latitude) / 30.0, 1.0)
    swing = 3.0 + 5.5 * (1.0 - equatorial)
    summer = _seasonal(day_of_year, peak_month=1)
    lapse = plot.altitude_m * 0.0065  # 6.5 degC per km of elevation
    temp_mean = (
        27.5
        + 2.0 * equatorial
        - lapse
        + swing * summer
        - 3.0 * (0.72 - clearness)
        + rng.normal(0.0, 1.0, size=n)
    )
    # Clear dry air swings hard between night and afternoon; humid cloudy air does not.
    daily_range = np.clip(9.0 + 8.0 * (clearness - 0.5) - 4.0 * rain_probability, 3.5, 17.0)
    temp_max = temp_mean + daily_range / 2.0
    temp_min = temp_mean - daily_range / 2.0

    humidity = np.clip(
        44.0
        + 40.0 * rain_probability
        + 14.0 * wet
        + 20.0 * (0.72 - clearness)
        + np.where(rains, 14.0, 0.0)
        + rng.normal(0.0, 4.0, size=n),
        15.0,
        99.0,
    )
    wind = np.clip(rng.gamma(shape=3.2, scale=0.75, size=n) + 0.4 * (1.0 - wet), 0.4, 12.0)

    # --- radiation and reference evapotranspiration ---------------------------
    ra = agromet.extraterrestrial_radiation(day_of_year, plot.latitude)
    rso = agromet.clear_sky_radiation(ra, plot.altitude_m)
    solar = rso * clearness

    et0 = agromet.reference_et0(
        temp_mean,
        temp_max,
        temp_min,
        humidity,
        solar,
        wind,
        day_of_year,
        plot.latitude,
        plot.altitude_m,
    )

    return DailyWeather(
        day_of_year=day_of_year,
        temp_mean_c=temp_mean,
        temp_max_c=temp_max,
        temp_min_c=temp_min,
        humidity_pct=humidity,
        wind_ms=wind,
        rain_mm=rain,
        clearness=clearness,
        solar_mj=solar,
        et0_mm=et0,
    )


def expand_intraday(
    daily: DailyWeather, hours: np.ndarray, samples_per_day: int, plot: Plot, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate the daily state onto the sampling grid.

    Returns ``(air_temp_c, humidity_pct, lux)`` at sensor resolution. Luminosity
    follows the solar geometry rather than a generic bell curve, so sunrise and
    sunset land where the latitude and the date say they should.
    """
    rng = plot_rng(plot.plot_id, seed, "intraday")
    total = len(daily) * samples_per_day

    temp_mean = np.repeat(daily.temp_mean_c, samples_per_day)
    daily_range = np.repeat(daily.temp_max_c - daily.temp_min_c, samples_per_day)
    humid_base = np.repeat(daily.humidity_pct, samples_per_day)
    solar_mj = np.repeat(daily.solar_mj, samples_per_day)
    doy = np.repeat(daily.day_of_year, samples_per_day)

    # Air temperature lags the sun: minimum near 05:00, maximum near 15:00.
    diurnal = np.sin(2.0 * np.pi * (hours - 9.0) / 24.0)
    air_temp = temp_mean + 0.5 * daily_range * diurnal + rng.normal(0.0, 0.35, size=total)
    humidity = np.clip(humid_base - 0.5 * daily_range * diurnal * 1.7, 8.0, 100.0)

    # Distribute the day's radiation over the daylight hours by solar elevation.
    declination = 0.409 * np.sin(2.0 * np.pi * doy / 365.0 - 1.39)
    phi = np.radians(plot.latitude)
    omega = np.radians(15.0 * (hours - 12.0))
    cos_zenith = np.clip(
        np.sin(phi) * np.sin(declination) + np.cos(phi) * np.cos(declination) * np.cos(omega),
        0.0,
        None,
    )
    # Normalise per day so the intraday profile integrates back to solar_mj.
    shaped = cos_zenith.reshape(-1, samples_per_day)
    day_sum = shaped.sum(axis=1, keepdims=True)
    weights = np.divide(shaped, day_sum, out=np.zeros_like(shaped), where=day_sum > 0).ravel()

    interval_h = 24.0 / samples_per_day
    irradiance_w_m2 = np.where(weights > 0, solar_mj * 1e6 * weights / (interval_h * 3600.0), 0.0)
    lux = irradiance_w_m2 * agromet.DAYLIGHT_LUMINOUS_EFFICACY
    return air_temp, humidity, np.maximum(lux, 0.0)
