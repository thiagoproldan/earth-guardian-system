"""The ingestion path: AWS-shaped envelopes, documents, relations."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from earthguardian.cloud.documents import DocumentStore
from earthguardian.cloud.iot_core import (
    decode_battery,
    decode_uplink,
    device_identity,
    encode_battery,
    wrap_uplink,
)
from earthguardian.cloud.pipeline import curate, ingest_fleet
from earthguardian.cloud.relational import CuratedStore
from earthguardian.edge.lorawan import encode_payload

SAMPLE = dict(
    soil_moisture=0.2317,
    air_temp_c=28.44,
    humidity_pct=62.5,
    lux=48210.0,
    soil_ph=6.3,
    air_quality_ppm=412.0,
    battery_pct=87.0,
)


def _envelope(plot_id: str = "CE-GUARACIABA-03", **overrides) -> dict:
    defaults = dict(
        timestamp=datetime(2025, 8, 22, 14, 30, tzinfo=UTC),
        spreading_factor=8,
        data_rate=4,
        rssi=-116.0,
        battery_pct=87.0,
        frame_counter=1423,
    )
    return wrap_uplink(plot_id, encode_payload(**SAMPLE), **{**defaults, **overrides})


# -- the AWS envelope ----------------------------------------------------------------------


def test_envelope_has_the_fields_aws_actually_emits():
    envelope = _envelope()
    assert set(envelope) == {"WirelessDeviceId", "PayloadData", "WirelessMetadata"}
    lorawan = envelope["WirelessMetadata"]["LoRaWAN"]
    for field in (
        "DevEui",
        "DevAddr",
        "FCnt",
        "FPort",
        "DataRate",
        "Frequency",
        "Battery",
        "Margin",
        "Gateways",
        "SpreadingFactor",
        "Timestamp",
    ):
        assert field in lorawan, field


def test_payload_is_base64_in_the_envelope():
    envelope = _envelope()
    assert base64.b64decode(envelope["PayloadData"]) == encode_payload(**SAMPLE)


def test_envelope_round_trips_through_the_decoder():
    record = decode_uplink(_envelope())
    for field, value in SAMPLE.items():
        if field == "battery_pct":
            continue  # the envelope carries a coarser byte; checked separately
        assert record[field] == pytest.approx(value, rel=0.001), field
    assert record["spreading_factor"] == 8
    assert record["rssi_dbm"] == pytest.approx(-116.0)


def test_battery_byte_reserves_its_flag_values():
    """0 means external power and 255 means unmeasurable; neither is a level."""
    assert decode_battery(0) is None
    assert decode_battery(255) is None
    for percent in (0.0, 50.0, 100.0):
        encoded = encode_battery(percent)
        assert 1 <= encoded <= 254
        assert decode_battery(encoded) == pytest.approx(percent, abs=1.0)


def test_margin_stays_inside_the_lorawan_range():
    for rssi in (-60.0, -116.0, -140.0):
        margin = _envelope(rssi=rssi)["WirelessMetadata"]["LoRaWAN"]["Margin"]
        assert -32 <= margin <= 31


def test_local_time_is_converted_to_utc():
    """The node reports wall clock; the envelope must carry UTC."""
    naive = datetime(2025, 1, 1, 0, 0)
    envelope = wrap_uplink(
        "SP-IBIUNA-01",
        encode_payload(**SAMPLE),
        timestamp=naive,
        spreading_factor=7,
        data_rate=5,
        rssi=-105.0,
        battery_pct=90,
        frame_counter=1,
        timezone_offset_h=-3,
    )
    assert envelope["WirelessMetadata"]["LoRaWAN"]["Timestamp"] == "2025-01-01T03:00:00Z"


def test_device_identity_is_stable_and_distinct():
    first = device_identity("SP-IBIUNA-01")
    assert device_identity("SP-IBIUNA-01") == first
    assert device_identity("CE-GUARACIABA-03") != first


# -- the document store ---------------------------------------------------------------------


def test_documents_round_trip(tmp_path):
    store = DocumentStore(tmp_path)
    store.put_documents(
        "raw/uplinks/plot_id=X/date=2025-01-01/part-00000.jsonl", [{"a": 1}, {"a": 2}]
    )
    assert store.get_documents("raw/uplinks/plot_id=X/date=2025-01-01/part-00000.jsonl") == [
        {"a": 1},
        {"a": 2},
    ]


def test_hive_partitions_are_parsed(tmp_path):
    store = DocumentStore(tmp_path)
    store.put_documents(DocumentStore.uplink_key("PLOT-A", "2025-03-04", 0), [{}])
    assert store.partitions() == [{"plot_id": "PLOT-A", "date": "2025-03-04"}]


def test_scan_prunes_by_partition(tmp_path):
    store = DocumentStore(tmp_path)
    store.put_documents(DocumentStore.uplink_key("A", "2025-01-01", 0), [{"n": 1}])
    store.put_documents(DocumentStore.uplink_key("B", "2025-01-01", 0), [{"n": 2}, {"n": 3}])
    assert len(list(store.scan())) == 3
    assert [d["n"] for d in store.scan(plot_id="A")] == [1]


def test_unsafe_keys_are_rejected(tmp_path):
    store = DocumentStore(tmp_path)
    for key in ("/etc/passwd", "raw/../../escape"):
        with pytest.raises(ValueError, match="unsafe object key"):
            store.put_documents(key, [{}])


# -- end to end -------------------------------------------------------------------------------


def test_ingest_then_curate_preserves_every_uplink(tmp_path, managed_year, guaraciaba):
    uplinks, truth, events = managed_year
    sample = uplinks.head(300).copy()

    store = DocumentStore(tmp_path / "raw")
    curated = CuratedStore(tmp_path / "db.sqlite")

    report = ingest_fleet(sample, store)
    assert report.uplinks == len(sample)

    written = curate(store, curated, (guaraciaba,), truth.head(5), events.head(5))
    assert written["readings"] == len(sample)

    back = curated.read_frame("readings", guaraciaba.plot_id)
    assert len(back) == len(sample)
    assert back["soil_moisture"].to_numpy() == pytest.approx(
        sample.sort_values("timestamp")["soil_moisture"].to_numpy(), rel=0.001
    )


def test_curation_joins_devices_to_plots_through_the_registry(tmp_path, managed_year, guaraciaba):
    """The uplink knows a radio; only the application knows which field it is in."""
    uplinks, _truth, _events = managed_year
    store = DocumentStore(tmp_path / "raw")
    curated = CuratedStore(tmp_path / "db.sqlite")
    ingest_fleet(uplinks.head(96), store)

    curate(store, curated, ())  # no registry
    assert curated.read_frame("readings", "unknown").shape[0] == 96

    curated2 = CuratedStore(tmp_path / "db2.sqlite")
    curate(store, curated2, (guaraciaba,))
    assert curated2.read_frame("readings", guaraciaba.plot_id).shape[0] == 96
