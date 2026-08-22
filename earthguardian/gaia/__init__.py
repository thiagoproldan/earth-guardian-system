"""GAIA - Gestao de Analise e Insights Ambientais.

The thesis put an AI called GAIA at the centre of the system: it cleans and
organises what the sensors send, analyses it, and hands the farmer something
actionable. This package is that layer, and it reads **uplinks only** - never
the simulator's hidden state.

Its hard problem is not the analysis, it is the reconstruction. What arrives is
a sparse, noisy, uncalibrated trace of volumetric water content from one probe
at one depth, delivered whenever the radio budget allowed. What the irrigation
decision needs is the root-zone depletion in millimetres, today, together with
the soil's field capacity and lower limit - none of which the farmer measured.

``soil_state``
    Recover the soil's hydraulic constants from its own drainage curves, then
    the depletion from the calibrated trace.
``advisories``
    Turn the analysis into ranked, plain-language recommendations - the
    deterministic core the thesis's ChatGPT-backed chat would sit on top of.
"""

from earthguardian.gaia.advisories import (
    Advisory,
    as_briefing,
    burning_advisory,
    detect_burning_events,
    node_advisory,
    ph_advisory,
    rank,
    water_advisory,
)
from earthguardian.gaia.soil_state import (
    SoilStateEstimate,
    detect_wetting_events,
    estimate_field_capacity,
    estimate_soil_state,
)

__all__ = [
    "Advisory",
    "SoilStateEstimate",
    "as_briefing",
    "burning_advisory",
    "detect_burning_events",
    "detect_wetting_events",
    "estimate_field_capacity",
    "estimate_soil_state",
    "node_advisory",
    "ph_advisory",
    "rank",
    "water_advisory",
]
