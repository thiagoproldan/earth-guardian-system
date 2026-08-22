"""Earth Guardian System - environmental monitoring for smallholder farms.

An offline, reproducible implementation of the system specified in the author's
2024 undergraduate thesis (FIAP). The thesis documented a five-sensor LoRaWAN
node feeding an AWS pipeline and an AI advisor called GAIA; the bench prototype
that accompanied it was an Arduino with a single soil-moisture probe.

This repository implements the *specification*, end to end, with no hardware, no
cloud account and no network access:

``earthguardian.edge``
    The field node - FAO-56 agrometeorology, a soil water balance, the sensor
    error budget of the real bill of materials, and a LoRaWAN radio model with
    an honest airtime budget.
``earthguardian.cloud``
    The ingestion path the thesis describes: MQTT-shaped uplinks landing raw in
    a document store, curated into a relational store.
``earthguardian.gaia``
    Gestao de Analise e Insights Ambientais - reconstructing the root-zone water
    balance from sparse, lossy uplinks, recovering soil hydraulic properties,
    and turning both into advisories.
``earthguardian.decisions``
    Irrigation scheduling and the counterfactual that prices it.
"""

from earthguardian.config import CROPS, PLOTS, SOILS, Settings, get_settings

__version__ = "1.0.0"

__all__ = ["CROPS", "PLOTS", "SOILS", "Settings", "__version__", "get_settings"]
