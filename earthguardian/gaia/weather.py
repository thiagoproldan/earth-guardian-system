"""Rebuilding the daily weather from what the uplinks carried.

GAIA receives temperature, humidity and illuminance on the same sparse schedule
as everything else. FAO-56 wants daily maxima, minima and integrated radiation,
so those have to be reconstructed from however many samples the radio allowed -
96 a day on a well-linked plot, 51 on one that is not, and fewer still after
packet loss.

That reconstruction is lossy in a specific and predictable way: a daily maximum
estimated from sparse samples is biased *low*, because the true peak probably
fell between two uplinks. The bias is small for temperature, which changes
slowly, and larger for illuminance, which does not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from earthguardian.config import Plot
from earthguardian.edge import agromet


def daily_weather_from_uplinks(uplinks: pd.DataFrame, plot: Plot) -> pd.DataFrame:
    """Aggregate uplinks into the daily table FAO-56 needs, and evaluate ET0."""
    frame = uplinks.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["date"] = frame["timestamp"].dt.floor("D")

    grouped = frame.groupby("date")
    daily = pd.DataFrame(
        {
            "temp_mean_c": grouped["air_temp_c"].mean(),
            "temp_max_c": grouped["air_temp_c"].max(),
            "temp_min_c": grouped["air_temp_c"].min(),
            "humidity_pct": grouped["humidity_pct"].mean(),
            "lux_mean": grouped["lux"].mean(),
            "samples": grouped["air_temp_c"].size(),
        }
    )
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
    daily = daily.interpolate(limit_direction="both")
    daily.index.name = "date"

    # Illuminance averaged over the whole day, converted to a daily radiation
    # total. The mean already includes the night-time zeros, so multiplying by
    # 24 hours recovers the integral without needing the samples to be evenly
    # spaced.
    daily["solar_mj"] = agromet.solar_radiation_from_lux(
        daily["lux_mean"], interval_minutes=24 * 60
    )

    doy = daily.index.dayofyear.to_numpy().astype(float)
    daily["et0_mm"] = agromet.reference_et0(
        daily["temp_mean_c"].to_numpy(),
        daily["temp_max_c"].to_numpy(),
        daily["temp_min_c"].to_numpy(),
        daily["humidity_pct"].to_numpy(),
        daily["solar_mj"].to_numpy(),
        # Wind is not on the bill of materials. FAO-56 recommends 2 m/s where no
        # measurement exists, and notes the error this introduces is modest in
        # humid climates and larger in dry, windy ones.
        np.full(len(daily), 2.0),
        doy,
        plot.latitude,
        plot.altitude_m,
    )
    return daily.reset_index()


def crop_demand(daily: pd.DataFrame, plot: Plot, start_day: int = 0) -> pd.DataFrame:
    """Attach the crop coefficient, rooting depth and ETc to the daily table.

    The planting calendar is registry data - the farmer told the app what went
    in the ground and when - not something inferred from the sensors.
    """
    crop = plot.crop_spec
    period = 365 if crop.perennial else crop.season_days
    days_after_planting = (np.arange(len(daily)) + start_day) % period

    daily = daily.copy()
    daily["days_after_planting"] = days_after_planting
    daily["kc"] = agromet.crop_coefficient(crop, days_after_planting)
    daily["root_depth_m"] = agromet.root_depth(crop, days_after_planting)
    daily["etc_mm"] = daily["kc"] * daily["et0_mm"]
    return daily
