"""The sensor error budget - the difference between truth and what a probe says.

These are the parts the thesis's bill of materials actually names, with the
accuracy their datasheets actually claim. Cheap sensors are the whole point of
the product, so pretending they are laboratory instruments would hide the
problem the analytics exists to solve.

Two of them deserve particular suspicion:

``soil_moisture``
    A capacitive probe reports capacitance, not water. Turning that into
    volumetric water content needs a per-soil calibration, and an uncalibrated
    probe is confidently wrong - which is why
    :mod:`earthguardian.gaia.soil_state` recovers the calibration from the data
    instead of trusting it.
``air_quality``
    An MQ-class sensor's resistance depends on temperature and humidity almost
    as much as on the gas it is meant to detect. Reported without compensation
    it tracks the weather.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from earthguardian.edge.weather import plot_rng


@dataclass(frozen=True, slots=True)
class SensorSpec:
    """One channel's error budget."""

    name: str
    part: str
    relative_noise: float = 0.0
    absolute_noise: float = 0.0
    resolution: float = 0.0
    bias: float = 0.0


SENSOR_SUITE: dict[str, SensorSpec] = {
    "soil_moisture": SensorSpec(
        "Volumetric water content",
        "Capacitive probe v1.2",
        relative_noise=0.02,
        absolute_noise=0.012,
        resolution=0.001,
    ),
    "air_temp_c": SensorSpec("Air temperature", "DHT22", absolute_noise=0.5, resolution=0.1),
    "humidity_pct": SensorSpec(
        "Relative humidity", "DHT22", absolute_noise=2.5, resolution=0.1, bias=-1.0
    ),
    "lux": SensorSpec("Illuminance", "LDR divider", relative_noise=0.15, resolution=1.0),
    "soil_ph": SensorSpec("Soil pH", "Analog pH probe", absolute_noise=0.15, resolution=0.1),
    "air_quality_ppm": SensorSpec(
        "Air quality", "MQ-135", relative_noise=0.20, absolute_noise=15.0, resolution=1.0
    ),
    "battery_pct": SensorSpec(
        "Battery state of charge", "Divider on 18650", absolute_noise=1.5, resolution=1.0
    ),
}


class SensorArray:
    """Applies the error budget to true values."""

    def __init__(self, plot_id: str, seed: int) -> None:
        self._rng = plot_rng(plot_id, seed, "sensors")

    def measure(self, channel: str, truth: np.ndarray, *, non_negative: bool = True) -> np.ndarray:
        spec = SENSOR_SUITE.get(channel)
        if spec is None:
            return np.asarray(truth, dtype=float)

        truth = np.asarray(truth, dtype=float)
        reading = truth + spec.bias
        if spec.relative_noise:
            reading = reading * self._rng.normal(1.0, spec.relative_noise, size=truth.shape)
        if spec.absolute_noise:
            reading = reading + self._rng.normal(0.0, spec.absolute_noise, size=truth.shape)
        if spec.resolution:
            reading = np.round(reading / spec.resolution) * spec.resolution
        if non_negative:
            reading = np.maximum(reading, 0.0)
        return reading

    def calibration_drift(
        self, n_samples: int, total_drift: float, start_fraction: float = 0.0
    ) -> np.ndarray:
        """A ramp for a probe losing calibration, as pH electrodes reliably do."""
        progress = np.linspace(0.0, 1.0, n_samples)
        ramp = np.clip((progress - start_fraction) / max(1e-6, 1.0 - start_fraction), 0.0, 1.0)
        return total_drift * ramp


def battery_state_of_charge(
    lux: np.ndarray,
    samples_per_day: int,
    rng: np.random.Generator,
    panel_efficiency: float = 0.85,
) -> np.ndarray:
    """State of charge of a solar-buffered node, in percent.

    The node draws a steady current and recharges only in daylight, so the
    battery sags through a run of overcast days and recovers on the first clear
    one. It is the channel a farmer notices first when something is wrong.
    """
    interval_h = 24.0 / samples_per_day
    # Harvest is proportional to illuminance, saturating well below full sun.
    harvest = np.clip(lux / 25_000.0, 0.0, 1.0) * panel_efficiency * 3.2 * interval_h
    draw = 0.55 * interval_h  # radio, MCU and sensors

    charge = np.empty_like(harvest)
    level = 92.0
    for index in range(len(harvest)):
        level = float(np.clip(level + harvest[index] - draw, 8.0, 100.0))
        charge[index] = level
    return charge + rng.normal(0.0, 0.4, size=len(harvest))


def soil_ph_series(
    n_samples: int, base_ph: float, moisture: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Soil pH: slow, real drift plus a wetting response.

    pH is not constant - it falls as fertiliser acidifies the soil over a season
    and rises briefly when rain flushes the profile. Both are small compared to
    the probe's own drift, which is the point.
    """
    seasonal_acidification = np.linspace(0.0, -0.35, n_samples)
    wetting = 0.4 * (moisture - float(np.mean(moisture)))
    return base_ph + seasonal_acidification + wetting + rng.normal(0.0, 0.03, size=n_samples)


def air_quality_series(
    n_samples: int,
    samples_per_day: int,
    rng: np.random.Generator,
    burn_days: tuple[int, ...] = (),
) -> np.ndarray:
    """Air quality in ppm-equivalent, with agricultural burning events.

    Baseline rural air sits near 400 ppm CO2-equivalent on an MQ-135. Burning
    crop residue - still common, and exactly what an environmental guardian
    ought to catch - drives it several times higher for a day or two.
    """
    base = 400.0 + rng.normal(0.0, 12.0, size=n_samples)
    diurnal = 25.0 * np.sin(np.linspace(0, 2 * np.pi * n_samples / samples_per_day, n_samples))
    series = base + diurnal

    for day in burn_days:
        start = day * samples_per_day
        end = min(start + int(1.6 * samples_per_day), n_samples)
        if start >= n_samples:
            continue
        span = end - start
        # A plume rises fast and disperses slowly.
        profile = np.exp(-np.linspace(0.0, 3.0, span)) * rng.uniform(900.0, 2200.0)
        series[start:end] += profile
    return np.maximum(series, 0.0)
