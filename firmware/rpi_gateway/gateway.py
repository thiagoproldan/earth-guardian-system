#!/usr/bin/env python3
"""Earth Guardian - Raspberry Pi gateway.

Reads framed samples from the Arduino over serial, decides which ones the
airtime budget can afford to send, packs them with the shared codec and hands
them to the radio.

The interesting part is not the plumbing, it is the budget. LoRaWAN fair-use
allows this node thirty seconds of uplink airtime a day. At the spreading factor
a distant plot is forced onto, one 13-byte uplink costs over half a second, so
the node can speak about fifty times in twenty-four hours - far less often than
the Arduino samples. Something has to choose what to drop, and doing that badly
means the water balance downstream is reconstructed from the wrong moments.

Run it against a real node::

    python gateway.py --port /dev/ttyACM0 --plot CE-GUARACIABA-03

or against a recorded capture, which is what the test-suite does::

    python gateway.py --replay capture.txt --plot CE-GUARACIABA-03 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# The gateway imports the project's own codec rather than re-implementing it.
# One definition of the wire format, shared by the device and the cloud.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from earthguardian.config import get_plot
from earthguardian.edge.lorawan import (
    FAIR_USE_AIRTIME_S_DAY,
    PAYLOAD_BYTES,
    encode_payload,
    plan_uplinks,
)

LOG = logging.getLogger("earthguardian.gateway")

FRAME_PREFIX = "EG1"
FRAME_FIELDS = 8  # prefix plus seven values


@dataclass(slots=True)
class Reading:
    """One parsed sample from the Arduino."""

    soil_wetness: float
    air_temp_c: float
    humidity_pct: float
    lux: float
    soil_ph: float
    air_quality_ppm: float
    battery_pct: float

    @property
    def is_complete(self) -> bool:
        """A DHT failure arrives as NaN and must not be transmitted as zero."""
        return not (math.isnan(self.air_temp_c) or math.isnan(self.humidity_pct))


def parse_frame(line: str) -> Reading | None:
    """Parse one serial line. Returns ``None`` for comments and malformed input."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split(",")
    if len(parts) != FRAME_FIELDS or parts[0] != FRAME_PREFIX:
        LOG.warning("discarding malformed frame: %r", line[:80])
        return None

    try:
        values = [float(part) for part in parts[1:]]
    except ValueError:
        LOG.warning("discarding unparseable frame: %r", line[:80])
        return None

    return Reading(*values)


class AirtimeBudget:
    """Enforces the fair-use allowance over a rolling twenty-four hours.

    A simple "one uplink every N seconds" timer is the tempting implementation
    and it fails at the edges: a node that reboots, or one whose data rate is
    lowered by the network mid-day, silently blows through its allowance. This
    tracks the airtime actually spent.
    """

    def __init__(self, budget_s: float = FAIR_USE_AIRTIME_S_DAY) -> None:
        self.budget_s = budget_s
        self._spent: list[tuple[float, float]] = []  # (timestamp, airtime)

    def _prune(self, now: float) -> None:
        cutoff = now - 24 * 3600
        self._spent = [entry for entry in self._spent if entry[0] >= cutoff]

    def spent_s(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        self._prune(now)
        return sum(airtime for _timestamp, airtime in self._spent)

    def can_send(self, airtime_s: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.spent_s(now) + airtime_s <= self.budget_s

    def record(self, airtime_s: float, now: float | None = None) -> None:
        self._spent.append((time.time() if now is None else now, airtime_s))


class Radio:
    """Stand-in for the LoRaWAN driver.

    On the real Pi this wraps an SX127x HAT - RadioLib or a Python LoRaWAN
    stack - holding the OTAA session and the AU915 sub-band (channel block 2,
    which is what Brazilian networks use). It is left as an obvious stub because
    no radio is attached to this repository, and a mock that returned success
    would make the gateway look tested when it is not.
    """

    def __init__(self, plot_id: str, dry_run: bool = False) -> None:
        self.plot_id = plot_id
        self.dry_run = dry_run
        self.sent = 0

    def send(self, payload: bytes) -> bool:
        if self.dry_run:
            LOG.info("would transmit %d bytes: %s", len(payload), payload.hex())
            self.sent += 1
            return True
        raise NotImplementedError(
            "attach an SX127x driver here: join OTAA on AU915 sub-band 2, then uplink "
            "on port 1. Run with --dry-run to exercise everything up to this point."
        )


def read_serial(port: str, baud: int = 9600) -> Iterator[str]:
    try:
        import serial
    except ImportError:  # pragma: no cover - depends on the deployment host
        raise SystemExit("pyserial is not installed: pip install pyserial") from None

    with serial.Serial(port, baud, timeout=30) as connection:
        while True:
            yield connection.readline().decode("utf-8", errors="replace")


def read_replay(path: Path) -> Iterator[str]:
    yield from path.read_text().splitlines()


def run(source: Iterator[str], plot_id: str, dry_run: bool) -> int:
    plot = get_plot(plot_id)
    budget_plan = plan_uplinks(
        plot.gateway_distance_km, PAYLOAD_BYTES, terrain_loss_db=plot.terrain_loss_db
    )
    LOG.info(
        "%s: %.1f km, DR%d/SF%d, %.0f ms per uplink, %d uplinks/day within fair use",
        plot_id,
        plot.gateway_distance_km,
        budget_plan.data_rate.index,
        budget_plan.data_rate.spreading_factor,
        budget_plan.airtime_s * 1000,
        budget_plan.uplinks_per_day,
    )

    budget = AirtimeBudget()
    radio = Radio(plot_id, dry_run=dry_run)
    interval_s = 24 * 3600 / max(budget_plan.uplinks_per_day, 1)
    last_sent = 0.0
    parsed = skipped = 0

    for line in source:
        reading = parse_frame(line)
        if reading is None:
            continue
        parsed += 1

        if not reading.is_complete:
            LOG.warning("incomplete reading (sensor fault), not transmitting")
            continue

        now = time.time()
        if now - last_sent < interval_s and last_sent:
            skipped += 1
            continue
        if not budget.can_send(budget_plan.airtime_s, now):
            LOG.warning(
                "airtime budget exhausted (%.1f s of %.0f s used), holding",
                budget.spent_s(now),
                budget.budget_s,
            )
            continue

        payload = encode_payload(
            soil_moisture=reading.soil_wetness,
            air_temp_c=reading.air_temp_c,
            humidity_pct=reading.humidity_pct,
            lux=reading.lux,
            soil_ph=reading.soil_ph,
            air_quality_ppm=reading.air_quality_ppm,
            battery_pct=reading.battery_pct,
        )
        if radio.send(payload):
            budget.record(budget_plan.airtime_s, now)
            last_sent = now

    LOG.info(
        "parsed %d frames, transmitted %d, skipped %d for cadence", parsed, radio.sent, skipped
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="serial device the Arduino is on")
    source.add_argument("--replay", type=Path, help="a captured serial log")
    parser.add_argument("--plot", required=True, help="plot id, e.g. CE-GUARACIABA-03")
    parser.add_argument("--dry-run", action="store_true", help="do everything but transmit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    stream = read_replay(args.replay) if args.replay else read_serial(args.port)
    return run(stream, args.plot, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
