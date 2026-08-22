"""Shared fixtures.

A year of one plot is generated once per session: it costs about a second and
every estimator test needs the same data.
"""

from __future__ import annotations

from datetime import date

import pytest

from earthguardian.config import PLOTS_BY_ID, get_settings
from earthguardian.edge.simulator import FieldSimulator, IrrigationPolicy

START = date(2025, 1, 1)
DAYS = 365


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def ibiuna():
    return PLOTS_BY_ID["SP-IBIUNA-01"]


@pytest.fixture(scope="session")
def guaraciaba():
    return PLOTS_BY_ID["CE-GUARACIABA-03"]


@pytest.fixture(scope="session")
def vendanova():
    return PLOTS_BY_ID["ES-VENDANOVA-02"]


def _run(plot, settings, policy):
    return FieldSimulator(plot, seed=settings.seed, settings=settings, policy=policy).run(
        START, DAYS
    )


@pytest.fixture(scope="session")
def rainfed_year(guaraciaba, settings):
    """A year with no irrigation - the cleanest case for the estimators."""
    return _run(guaraciaba, settings, IrrigationPolicy.rainfed())


@pytest.fixture(scope="session")
def managed_year(guaraciaba, settings):
    """A year under sensor-driven irrigation."""
    return _run(guaraciaba, settings, IrrigationPolicy.sensor_driven(0.9))
