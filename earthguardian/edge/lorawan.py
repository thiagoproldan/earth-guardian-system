"""The radio, modelled honestly.

The thesis put LoRaWAN in the architecture diagram. It is the constraint that
shapes the whole product, because a LoRaWAN node cannot simply sample often and
send everything: **airtime is the scarce resource**, and how much of it a packet
costs depends on how far the node is from the gateway.

Three facts drive every design decision downstream:

1. **Time-on-air grows exponentially with spreading factor.** The same 20-byte
   payload takes 56 ms at SF7 and 1.4 s at SF12 - twenty-five times the airtime
   for one hop further out.
2. **Distance forces the spreading factor up.** A node that cannot close the
   link at SF7 must slow down, which costs airtime, which costs uplinks.
3. **The airtime is capped.** The Things Network's fair use policy allows
   30 seconds of uplink per device per day. That is a hard budget: at SF12 it
   buys roughly twenty messages a day.

So the coffee plot 6.4 km out cannot report every fifteen minutes no matter how
much the agronomy would like it to, and the reconstruction in
:mod:`earthguardian.gaia` has to work from what the radio could actually
deliver. That constraint is the interesting part of the problem, not an excuse.

References
----------
Semtech SX1276 datasheet (time-on-air, receiver sensitivity); LoRaWAN Regional
Parameters, AU915-928 - the band Brazil uses; The Things Network fair use
policy.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

import numpy as np

#: The Things Network fair use policy: uplink airtime per device per day.
FAIR_USE_AIRTIME_S_DAY = 30.0


#: AU915 data rates. ``max_payload`` is the application payload in bytes, from
#: the LoRaWAN Regional Parameters table for AU915-928.
@dataclass(frozen=True, slots=True)
class DataRate:
    index: int
    spreading_factor: int
    bandwidth_hz: int
    max_payload: int
    #: Receiver sensitivity in dBm, Semtech SX1276 at this SF and bandwidth.
    sensitivity_dbm: float


AU915_DATA_RATES: tuple[DataRate, ...] = (
    DataRate(0, 12, 125_000, 51, -137.0),
    DataRate(1, 11, 125_000, 51, -134.5),
    DataRate(2, 10, 125_000, 51, -132.0),
    DataRate(3, 9, 125_000, 115, -129.0),
    DataRate(4, 8, 125_000, 242, -126.0),
    DataRate(5, 7, 125_000, 242, -123.0),
)

#: Typical node transmit power in AU915, dBm.
TX_POWER_DBM = 20.0
ANTENNA_GAIN_DBI = 2.0
#: Path-loss exponent for rural/agricultural terrain at 915 MHz. Free space is
#: 2.0; farmland with vegetation and low antennas measures nearer 3.5.
PATH_LOSS_EXPONENT = 3.5
#: Clutter loss, dB. A gateway on a barn roof and a node on a post in a crop
#: canopy are nothing like the ideal isotropic radiators of the free-space
#: formula. Field trials of LoRa in agriculture consistently land 15-25 dB
#: below what free space predicts, and omitting it produces a model where every
#: node closes the link at the fastest data rate - which is exactly the mistake
#: that makes an airtime budget look like a non-problem.
CLUTTER_LOSS_DB = 20.0
#: Shadow fading standard deviation, dB.
SHADOW_FADING_DB = 4.0
#: Link margin required before the adaptive rate will trust a data rate.
REQUIRED_MARGIN_DB = 8.0


def free_space_loss_db(distance_km: float, frequency_mhz: float = 915.0) -> float:
    """Free-space path loss at one kilometre reference, dB."""
    return 32.44 + 20.0 * math.log10(max(distance_km, 1e-3)) + 20.0 * math.log10(frequency_mhz)


def path_loss_db(
    distance_km: float,
    exponent: float = PATH_LOSS_EXPONENT,
    terrain_loss_db: float = 0.0,
) -> float:
    """Log-distance path loss with clutter and terrain, dB.

    ``terrain_loss_db`` is the site-specific penalty for what sits between the
    node and the gateway - a ridge, a valley wall, a windbreak. It is the term
    that separates two plots at the same distance with very different links.
    """
    loss = free_space_loss_db(distance_km) + CLUTTER_LOSS_DB + terrain_loss_db
    if distance_km > 1.0:
        loss += 10.0 * (exponent - 2.0) * math.log10(distance_km)
    return loss


def time_on_air_s(
    payload_bytes: int,
    data_rate: DataRate,
    coding_rate: int = 1,
    preamble_symbols: int = 8,
    explicit_header: bool = True,
    crc: bool = True,
) -> float:
    """LoRa time-on-air in seconds, per the SX1276 datasheet.

    The symbol period doubles with every step of the spreading factor, which is
    where the exponential airtime cost comes from.
    """
    sf = data_rate.spreading_factor
    symbol_time = (2**sf) / data_rate.bandwidth_hz
    preamble_time = (preamble_symbols + 4.25) * symbol_time

    # Low data rate optimisation is mandatory when a symbol lasts over 16 ms.
    low_rate_optimise = 1 if symbol_time > 0.016 else 0

    numerator = (
        8 * payload_bytes
        - 4 * sf
        + 28
        + 16 * (1 if crc else 0)
        - 20 * (0 if explicit_header else 1)
    )
    denominator = 4 * (sf - 2 * low_rate_optimise)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * (coding_rate + 4), 0)

    return preamble_time + payload_symbols * symbol_time


@dataclass(frozen=True, slots=True)
class RadioBudget:
    """What the airtime allowance buys at a given distance and payload."""

    data_rate: DataRate
    rssi_dbm: float
    margin_db: float
    airtime_s: float
    uplinks_per_day: int
    payload_bytes: int

    @property
    def interval_minutes(self) -> float:
        return 24 * 60 / max(self.uplinks_per_day, 1)

    @property
    def daily_airtime_s(self) -> float:
        return self.airtime_s * self.uplinks_per_day


def select_data_rate(
    distance_km: float, payload_bytes: int, terrain_loss_db: float = 0.0
) -> tuple[DataRate, float, float]:
    """Adaptive data rate: the fastest rate that closes the link with margin.

    Returns ``(data_rate, rssi_dbm, margin_db)``. Falls back to the slowest rate
    when nothing closes, which is what a real node does before it gives up.
    """
    loss = path_loss_db(distance_km, terrain_loss_db=terrain_loss_db)
    rssi = TX_POWER_DBM + 2 * ANTENNA_GAIN_DBI - loss

    for rate in sorted(AU915_DATA_RATES, key=lambda r: -r.index):
        if payload_bytes > rate.max_payload:
            continue
        margin = rssi - rate.sensitivity_dbm
        if margin >= REQUIRED_MARGIN_DB:
            return rate, rssi, margin

    slowest = AU915_DATA_RATES[0]
    return slowest, rssi, rssi - slowest.sensitivity_dbm


def plan_uplinks(
    distance_km: float,
    payload_bytes: int,
    airtime_budget_s: float = FAIR_USE_AIRTIME_S_DAY,
    terrain_loss_db: float = 0.0,
    max_uplinks_per_day: int | None = None,
) -> RadioBudget:
    """How often this node can report without exceeding its airtime allowance.

    ``max_uplinks_per_day`` caps the answer at the sampling rate: a node cannot
    transmit readings it never took, so a well-linked plot ends up limited by
    agronomy rather than by radio - which is the point of measuring both.
    """
    rate, rssi, margin = select_data_rate(distance_km, payload_bytes, terrain_loss_db)
    airtime = time_on_air_s(payload_bytes, rate)
    uplinks = max(1, int(airtime_budget_s // airtime))
    if max_uplinks_per_day is not None:
        uplinks = min(uplinks, max_uplinks_per_day)
    return RadioBudget(rate, rssi, margin, airtime, uplinks, payload_bytes)


class LoRaWANLink:
    """A stochastic link: packets are lost more often the thinner the margin."""

    def __init__(self, distance_km: float, seed: int, exponent: float = PATH_LOSS_EXPONENT) -> None:
        self.distance_km = distance_km
        self.exponent = exponent
        self._rng = np.random.default_rng(
            np.random.SeedSequence([seed, zlib.crc32(f"link|{distance_km}".encode())])
        )

    def delivery_probability(self, margin_db: float) -> float:
        """Probability a packet arrives, given the link margin.

        Shadow fading is log-normal, so the chance the instantaneous margin goes
        negative is the normal tail - which is why a nominally healthy 8 dB
        margin still drops a few percent of packets.
        """
        if margin_db <= 0:
            return 0.02
        # P(fade < margin) for a zero-mean normal with SHADOW_FADING_DB sigma.
        z = margin_db / SHADOW_FADING_DB
        probability = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        return float(np.clip(probability, 0.0, 0.995))

    def transmit(self, count: int, margin_db: float) -> np.ndarray:
        """Return a boolean mask of which of ``count`` uplinks were received."""
        probability = self.delivery_probability(margin_db)
        return self._rng.random(count) < probability


# --------------------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------------------

#: Uplink payload layout. Every field is a scaled integer, because floating
#: point in a 51-byte budget is a luxury: 12 bytes carries the whole five-sensor
#: reading with room left for the header.
PAYLOAD_STRUCT = ">BHhHHBHB"
PAYLOAD_VERSION = 1


def encode_payload(
    *,
    soil_moisture: float,
    air_temp_c: float,
    humidity_pct: float,
    lux: float,
    soil_ph: float,
    air_quality_ppm: float,
    battery_pct: float,
) -> bytes:
    """Pack one reading into the uplink payload.

    Scaling choices follow the precision each sensor can actually justify:
    volumetric water content to 0.0001 m3/m3, temperature to 0.01 degC, pH to
    0.1, and luminosity logarithmically because sunlight spans four decades.
    """
    return struct.pack(
        PAYLOAD_STRUCT,
        PAYLOAD_VERSION,
        int(np.clip(round(soil_moisture * 10_000), 0, 65_535)),
        int(np.clip(round(air_temp_c * 100), -32_768, 32_767)),
        int(np.clip(round(humidity_pct * 100), 0, 65_535)),
        _encode_lux(lux),
        int(np.clip(round(soil_ph * 10), 0, 255)),
        int(np.clip(round(air_quality_ppm), 0, 65_535)),
        int(np.clip(round(battery_pct), 0, 255)),
    )


def _encode_lux(lux: float) -> int:
    """Log-scaled luminosity: full sun is 100,000 lux and dusk is 10.

    Four decades will not fit in two bytes linearly at any useful resolution, so
    the field carries ``log10(lux)`` scaled by 10,000 - constant *relative*
    precision, which is what a photometric reading deserves anyway.
    """
    return int(np.clip(round(math.log10(max(lux, 1.0)) * 10_000), 0, 65_535))


def _decode_lux(encoded: int) -> float:
    return float(10.0 ** (encoded / 10_000.0))


def decode_payload(payload: bytes) -> dict[str, float]:
    """Unpack an uplink. The gateway and the cloud share this one definition."""
    version, moisture, temp, humidity, lux, ph, air_quality, battery = struct.unpack(
        PAYLOAD_STRUCT, payload
    )
    if version != PAYLOAD_VERSION:
        raise ValueError(f"unsupported payload version {version}")
    return {
        "soil_moisture": moisture / 10_000.0,
        "air_temp_c": temp / 100.0,
        "humidity_pct": humidity / 100.0,
        "lux": _decode_lux(lux),
        "soil_ph": ph / 10.0,
        "air_quality_ppm": float(air_quality),
        "battery_pct": float(battery),
    }


PAYLOAD_BYTES = struct.calcsize(PAYLOAD_STRUCT)
