"""The field simulator - the synthetic farm the rest of the system observes.

It produces three aligned tables:

``uplinks``
    What the gateway actually received: quantised, noisy, and *sparse*, because
    the radio budget decided how often the node could speak and shadow fading
    decided which of those attempts landed. This is the only thing GAIA reads.
``ground_truth``
    The daily water balance that really happened - depletion, stress, yield
    impact, and the irrigation that was applied. Never read by the analytics.
``events``
    Irrigation, rain and burning events, for scoring the detectors.

Keeping the first two apart is what makes the accuracy numbers in the
test-suite mean something rather than decorate a demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from earthguardian.config import Plot, Settings, get_settings
from earthguardian.edge import agromet, lorawan, sensors, soil
from earthguardian.edge.weather import expand_intraday, generate_daily_weather, plot_rng


@dataclass(frozen=True, slots=True)
class IrrigationPolicy:
    """How the farmer decides to irrigate.

    ``rainfed``
        Never irrigate - the baseline for a plot with no system at all.
    ``calendar``
        A fixed interval and depth, which is what a farmer without instruments
        does: irrigate every Tuesday whether the soil needs it or not.
    ``sensor``
        Refill when depletion reaches a trigger, which is what the platform
        recommends. The trigger is expressed as a fraction of readily available
        water so it transfers across soils and crops.
    """

    kind: str = "rainfed"
    interval_days: int = 7
    depth_mm: float = 20.0
    trigger_fraction_of_raw: float = 0.9
    #: Depletion deliberately left unfilled, as a fraction of total available
    #: water. Refilling all the way to field capacity leaves the profile with
    #: nowhere to put the next rainfall, so a shower two days later runs
    #: straight past the roots and the water is bought twice. Extension
    #: guidance puts the useful buffer at 10-20% of the profile.
    refill_buffer_fraction_of_taw: float = 0.15

    @staticmethod
    def rainfed() -> IrrigationPolicy:
        return IrrigationPolicy("rainfed")

    @staticmethod
    def calendar(interval_days: int, depth_mm: float) -> IrrigationPolicy:
        return IrrigationPolicy("calendar", interval_days=interval_days, depth_mm=depth_mm)

    @staticmethod
    def sensor_driven(trigger_fraction_of_raw: float = 0.9) -> IrrigationPolicy:
        return IrrigationPolicy("sensor", trigger_fraction_of_raw=trigger_fraction_of_raw)


#: Days on which crop residue is burned nearby, per plot. The air-quality
#: channel has to notice these without being told.
DEFAULT_BURN_DAYS: dict[str, tuple[int, ...]] = {
    "SP-IBIUNA-01": (204,),
    "ES-VENDANOVA-02": (156, 288),
    "CE-GUARACIABA-03": (233, 240),
}


class FieldSimulator:
    """Simulates one instrumented plot for a season."""

    def __init__(
        self,
        plot: Plot,
        *,
        seed: int,
        settings: Settings | None = None,
        policy: IrrigationPolicy | None = None,
        burn_days: tuple[int, ...] | None = None,
        ph_drift: float = 0.0,
    ) -> None:
        self.plot = plot
        self.seed = seed
        self.settings = settings or get_settings()
        self.policy = policy or IrrigationPolicy.rainfed()
        self.burn_days = DEFAULT_BURN_DAYS.get(plot.plot_id, ()) if burn_days is None else burn_days
        self.ph_drift = ph_drift
        self.samples_per_day = self.settings.samples_per_day

    # -- the daily agronomy --------------------------------------------------

    def _run_water_balance(self, weather, days: int) -> soil.WaterBalance:
        plot, crop, ground = self.plot, self.plot.crop_spec, self.plot.soil_spec
        balance = soil.WaterBalance(plot_id=plot.plot_id)

        depletion = soil.initial_depletion(plot)
        days_since_irrigation = 0

        for day in range(days):
            # Annual crops are replanted: a 75-day lettuce fits four cycles into
            # a year, and each one runs its own Kc curve from seedling to
            # harvest. Letting the counter run to 365 would leave the crop stuck
            # at its end-of-season coefficient for nine months.
            days_after_planting = day % 365 if crop.perennial else day % crop.season_days
            root = float(agromet.root_depth(crop, days_after_planting))
            taw = soil.total_available_water(ground, root)
            raw = taw * crop.depletion_fraction
            kc = float(agromet.crop_coefficient(crop, days_after_planting))
            etc_potential = float(weather.et0_mm[day]) * kc

            # --- the irrigation decision -----------------------------------
            irrigation = 0.0
            if self.policy.kind == "calendar":
                if days_since_irrigation >= self.policy.interval_days:
                    irrigation = self.policy.depth_mm
                    days_since_irrigation = 0
            elif self.policy.kind == "sensor":
                trigger = raw * self.policy.trigger_fraction_of_raw
                if depletion >= trigger:
                    # Refill to *near* field capacity, deliberately short, so
                    # the profile keeps room for rain that has not fallen yet.
                    target = taw * self.policy.refill_buffer_fraction_of_taw
                    irrigation = max(0.0, depletion - target)
                    days_since_irrigation = 0
            days_since_irrigation += 1

            # Applied water is larger than delivered water - the system loses some.
            applied = irrigation / max(plot.irrigation_efficiency, 1e-6)

            depletion, fluxes = soil.water_balance_step(
                depletion,
                rain_mm=float(weather.rain_mm[day]),
                irrigation_mm=irrigation,
                etc_potential_mm=etc_potential,
                taw_mm=taw,
                raw_mm=raw,
                soil=ground,
                root_depth_m=root,
            )

            balance.days.append(
                soil.WaterBalanceDay(
                    day=day,
                    depletion_mm=depletion,
                    taw_mm=taw,
                    raw_mm=raw,
                    root_depth_m=root,
                    kc=kc,
                    et0_mm=float(weather.et0_mm[day]),
                    etc_potential_mm=etc_potential,
                    etc_actual_mm=fluxes["etc_actual_mm"],
                    ks=fluxes["ks"],
                    rain_mm=float(weather.rain_mm[day]),
                    runoff_mm=fluxes["runoff_mm"],
                    irrigation_mm=applied,
                    drainage_mm=fluxes["drainage_mm"],
                    theta=float(soil.depletion_to_theta(depletion, ground, root)),
                )
            )
        return balance

    # -- public API ----------------------------------------------------------

    def run(self, start: date, days: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Simulate ``days`` days. Returns ``(uplinks, ground_truth, events)``."""
        plot = self.plot
        n = days * self.samples_per_day
        step = pd.Timedelta(minutes=self.settings.sample_interval_min)
        timestamps = pd.date_range(
            datetime.combine(start, datetime.min.time()), periods=n, freq=step
        )
        hours = timestamps.hour.to_numpy() + timestamps.minute.to_numpy() / 60.0
        doy = timestamps.dayofyear.to_numpy().astype(float)

        weather = generate_daily_weather(plot, doy[:: self.samples_per_day], self.seed)
        balance = self._run_water_balance(weather, days)

        air_temp, humidity, lux = expand_intraday(
            weather, hours, self.samples_per_day, plot, self.seed
        )

        rng = plot_rng(plot.plot_id, self.seed, "channels")
        theta_true = np.repeat(balance.series("theta"), self.samples_per_day)
        ph_true = sensors.soil_ph_series(n, base_ph=6.4, moisture=theta_true, rng=rng)
        air_quality_true = sensors.air_quality_series(
            n, self.samples_per_day, rng, burn_days=self.burn_days
        )
        battery_true = sensors.battery_state_of_charge(lux, self.samples_per_day, rng)

        # --- what the probes report ------------------------------------------
        array = sensors.SensorArray(plot.plot_id, self.seed)
        if self.ph_drift:
            ph_true = ph_true + array.calibration_drift(n, self.ph_drift, start_fraction=0.25)

        readings = pd.DataFrame(
            {
                "timestamp": timestamps,
                "plot_id": plot.plot_id,
                "soil_moisture": array.measure("soil_moisture", theta_true),
                "air_temp_c": array.measure("air_temp_c", air_temp, non_negative=False),
                "humidity_pct": np.clip(array.measure("humidity_pct", humidity), 0.0, 100.0),
                "lux": array.measure("lux", lux),
                "soil_ph": np.clip(array.measure("soil_ph", ph_true), 3.0, 10.0),
                "air_quality_ppm": array.measure("air_quality_ppm", air_quality_true),
                "battery_pct": np.clip(array.measure("battery_pct", battery_true), 0.0, 100.0),
            }
        )

        uplinks = self._transmit(readings, days)

        truth = pd.DataFrame(
            {
                "plot_id": plot.plot_id,
                "date": pd.date_range(start, periods=days, freq="D"),
                "depletion_mm": balance.series("depletion_mm"),
                "theta_true": balance.series("theta"),
                "taw_mm": balance.series("taw_mm"),
                "raw_mm": balance.series("raw_mm"),
                "root_depth_m": balance.series("root_depth_m"),
                "kc": balance.series("kc"),
                "et0_mm": balance.series("et0_mm"),
                "etc_potential_mm": balance.series("etc_potential_mm"),
                "etc_actual_mm": balance.series("etc_actual_mm"),
                "ks": balance.series("ks"),
                "rain_mm": balance.series("rain_mm"),
                "irrigation_mm": balance.series("irrigation_mm"),
                "drainage_mm": balance.series("drainage_mm"),
                "runoff_mm": balance.series("runoff_mm"),
                "cycle": self._cycle_index(days),
            }
        )

        events = self._event_log(start, balance)
        return uplinks, truth, events

    def _cycle_index(self, days: int) -> np.ndarray:
        """Which planting each day belongs to - the unit FAO-33 is defined over."""
        crop = self.plot.crop_spec
        period = 365 if crop.perennial else crop.season_days
        return np.arange(days) // period

    def _transmit(self, readings: pd.DataFrame, days: int) -> pd.DataFrame:
        """Decide which readings the radio could carry, and deliver some of them."""
        plot = self.plot
        budget = lorawan.plan_uplinks(
            plot.gateway_distance_km,
            lorawan.PAYLOAD_BYTES,
            terrain_loss_db=plot.terrain_loss_db,
            max_uplinks_per_day=self.samples_per_day,
        )

        # Space the allowed uplinks evenly through each day.
        per_day = budget.uplinks_per_day
        offsets = np.linspace(0, self.samples_per_day, per_day, endpoint=False).astype(int)
        indices = np.concatenate([offsets + day * self.samples_per_day for day in range(days)])
        indices = indices[indices < len(readings)]

        link = lorawan.LoRaWANLink(plot.gateway_distance_km, self.seed)
        delivered = link.transmit(len(indices), budget.margin_db)

        uplinks = readings.iloc[indices[delivered]].copy()
        uplinks["data_rate"] = f"DR{budget.data_rate.index}"
        uplinks["spreading_factor"] = budget.data_rate.spreading_factor
        uplinks["rssi_dbm"] = np.round(
            budget.rssi_dbm + np.random.default_rng(self.seed).normal(0, 2.0, len(uplinks)), 1
        )
        uplinks["payload_bytes"] = lorawan.PAYLOAD_BYTES
        uplinks["airtime_s"] = budget.airtime_s
        return uplinks.reset_index(drop=True)

    def _event_log(self, start: date, balance: soil.WaterBalance) -> pd.DataFrame:
        rows: list[dict] = []
        for entry in balance.days:
            when = pd.Timestamp(start) + pd.Timedelta(days=entry.day)
            if entry.irrigation_mm > 0:
                rows.append(
                    {
                        "plot_id": self.plot.plot_id,
                        "date": when,
                        "kind": "irrigation",
                        "magnitude": entry.irrigation_mm,
                    }
                )
            if entry.rain_mm >= 5.0:
                rows.append(
                    {
                        "plot_id": self.plot.plot_id,
                        "date": when,
                        "kind": "rain",
                        "magnitude": entry.rain_mm,
                    }
                )
            if entry.ks < 1.0:
                rows.append(
                    {
                        "plot_id": self.plot.plot_id,
                        "date": when,
                        "kind": "water_stress",
                        "magnitude": 1.0 - entry.ks,
                    }
                )
        for day in self.burn_days:
            rows.append(
                {
                    "plot_id": self.plot.plot_id,
                    "date": pd.Timestamp(start) + pd.Timedelta(days=int(day)),
                    "kind": "burning",
                    "magnitude": 1.0,
                }
            )
        return pd.DataFrame(rows)


def simulate_fleet(
    start: date,
    days: int,
    plots: tuple[Plot, ...] | None = None,
    *,
    seed: int | None = None,
    policy: IrrigationPolicy | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Simulate every plot and concatenate."""
    from earthguardian.config import PLOTS

    settings = get_settings()
    plots = plots or PLOTS
    seed = settings.seed if seed is None else seed

    uplink_parts, truth_parts, event_parts = [], [], []
    for plot in plots:
        simulator = FieldSimulator(plot, seed=seed, settings=settings, policy=policy)
        uplinks, truth, events = simulator.run(start, days)
        uplink_parts.append(uplinks)
        truth_parts.append(truth)
        event_parts.append(events)

    return (
        pd.concat(uplink_parts, ignore_index=True),
        pd.concat(truth_parts, ignore_index=True),
        pd.concat(event_parts, ignore_index=True),
    )
