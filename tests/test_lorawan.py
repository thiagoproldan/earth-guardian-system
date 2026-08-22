"""The radio model decides how much data the analytics ever gets."""

from __future__ import annotations

from itertools import pairwise

import pytest

from earthguardian.config import PLOTS_BY_ID
from earthguardian.edge import lorawan


def test_time_on_air_matches_the_semtech_calculator():
    """A 51-byte payload at SF12/125 kHz takes about 2.46 s; at SF7, 0.10 s."""
    slowest = lorawan.AU915_DATA_RATES[0]
    fastest = lorawan.AU915_DATA_RATES[-1]
    assert lorawan.time_on_air_s(51, slowest) == pytest.approx(2.466, abs=0.02)
    assert lorawan.time_on_air_s(51, fastest) == pytest.approx(0.103, abs=0.005)


def test_airtime_roughly_doubles_with_each_spreading_factor():
    times = [lorawan.time_on_air_s(13, rate) for rate in lorawan.AU915_DATA_RATES]
    for slower, faster in pairwise(times):
        assert slower > faster
    assert times[0] / times[-1] > 20, "SF12 should cost far more airtime than SF7"


def test_payload_fits_the_slowest_data_rate():
    assert lorawan.AU915_DATA_RATES[0].max_payload >= lorawan.PAYLOAD_BYTES


def test_payload_round_trips_within_sensor_precision():
    original = dict(
        soil_moisture=0.2317,
        air_temp_c=28.44,
        humidity_pct=62.5,
        lux=48210.0,
        soil_ph=6.3,
        air_quality_ppm=412.0,
        battery_pct=87.0,
    )
    decoded = lorawan.decode_payload(lorawan.encode_payload(**original))
    for field, value in original.items():
        assert decoded[field] == pytest.approx(value, rel=0.001), field


def test_payload_rejects_an_unknown_version():
    payload = bytearray(
        lorawan.encode_payload(
            soil_moisture=0.2,
            air_temp_c=20,
            humidity_pct=50,
            lux=1000,
            soil_ph=6.0,
            air_quality_ppm=400,
            battery_pct=80,
        )
    )
    payload[0] = 99
    with pytest.raises(ValueError, match="unsupported payload version"):
        lorawan.decode_payload(bytes(payload))


def test_lux_encoding_keeps_relative_precision_across_four_decades():
    for lux in (10.0, 1_000.0, 100_000.0):
        decoded = lorawan.decode_payload(
            lorawan.encode_payload(
                soil_moisture=0.2,
                air_temp_c=20,
                humidity_pct=50,
                lux=lux,
                soil_ph=6.0,
                air_quality_ppm=400,
                battery_pct=80,
            )
        )
        assert decoded["lux"] == pytest.approx(lux, rel=0.001)


def test_distance_and_terrain_both_weaken_the_link():
    near = lorawan.select_data_rate(2.0, lorawan.PAYLOAD_BYTES)[1]
    far = lorawan.select_data_rate(8.0, lorawan.PAYLOAD_BYTES)[1]
    obstructed = lorawan.select_data_rate(2.0, lorawan.PAYLOAD_BYTES, terrain_loss_db=15.0)[1]
    assert far < near
    assert obstructed < near


def test_adaptive_rate_slows_down_as_the_link_degrades():
    close = lorawan.select_data_rate(1.0, lorawan.PAYLOAD_BYTES)[0]
    distant = lorawan.select_data_rate(9.0, lorawan.PAYLOAD_BYTES, terrain_loss_db=12.0)[0]
    assert distant.spreading_factor > close.spreading_factor


def test_rssi_lands_in_the_range_field_trials_report():
    """Rural LoRa at 915 MHz measures roughly -100 dBm at 2 km, -120 at 8 km."""
    assert -110 < lorawan.select_data_rate(2.0, 13)[1] < -95
    assert -130 < lorawan.select_data_rate(8.0, 13)[1] < -110


def test_the_airtime_allowance_binds_on_a_distant_node():
    """The whole point of the radio model: fair use limits how often you speak."""
    budget = lorawan.plan_uplinks(6.4, lorawan.PAYLOAD_BYTES, terrain_loss_db=10.0)
    assert budget.daily_airtime_s <= lorawan.FAIR_USE_AIRTIME_S_DAY + 1e-6
    assert budget.uplinks_per_day < 96, "a distant node cannot report every 15 minutes"


def test_the_coffee_plot_is_radio_limited_and_the_others_are_not():
    limits = {}
    for plot in PLOTS_BY_ID.values():
        budget = lorawan.plan_uplinks(
            plot.gateway_distance_km,
            lorawan.PAYLOAD_BYTES,
            terrain_loss_db=plot.terrain_loss_db,
            max_uplinks_per_day=96,
        )
        limits[plot.plot_id] = budget.uplinks_per_day
    assert limits["ES-VENDANOVA-02"] < 96
    assert limits["SP-IBIUNA-01"] == 96
    assert limits["CE-GUARACIABA-03"] == 96


def test_delivery_probability_rises_with_margin():
    link = lorawan.LoRaWANLink(3.0, seed=1)
    assert link.delivery_probability(0.0) < link.delivery_probability(8.0)
    assert link.delivery_probability(8.0) < link.delivery_probability(25.0)
    assert 0.0 <= link.delivery_probability(-5.0) <= 1.0
