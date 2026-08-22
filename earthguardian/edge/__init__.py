"""The field tier - what the thesis called the device layer.

An Arduino Uno reads the sensors and does light pre-processing; a Raspberry Pi
aggregates and transmits over LoRaWAN. Here the same interface is fed by an
agrometeorological simulator, so the whole system can be exercised without the
hardware that stayed at the university.

The firmware that would run on the real boards is in ``firmware/`` and speaks
the same payload format this package encodes.
"""

from earthguardian.edge.agromet import (
    crop_coefficient,
    extraterrestrial_radiation,
    reference_et0,
    solar_radiation_from_lux,
)
from earthguardian.edge.lorawan import LoRaWANLink, RadioBudget, encode_payload
from earthguardian.edge.simulator import FieldSimulator, simulate_fleet
from earthguardian.edge.soil import WaterBalance, water_balance_step

__all__ = [
    "FieldSimulator",
    "LoRaWANLink",
    "RadioBudget",
    "WaterBalance",
    "crop_coefficient",
    "encode_payload",
    "extraterrestrial_radiation",
    "reference_et0",
    "simulate_fleet",
    "solar_radiation_from_lux",
    "water_balance_step",
]
