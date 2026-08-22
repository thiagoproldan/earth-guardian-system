"""Ingestion and curation - the Lambda the IoT Rule would invoke.

Two stages, deliberately separate:

``ingest_fleet``
    Wrap every uplink in the AWS envelope and land it in the document store,
    partitioned by plot and day. Append-only.
``curate``
    Read the raw zone back, decode the payloads and promote them into typed
    relations.

The separation is what makes the pipeline safe to iterate on. Curation is a pure
function of the raw zone, so a decoder bug is a re-run rather than a loss - and
the raw zone still holds the original bytes, which is the only version nobody
can argue with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from earthguardian.cloud.documents import DocumentStore
from earthguardian.cloud.iot_core import decode_uplink, device_identity, wrap_uplink
from earthguardian.cloud.relational import CuratedStore
from earthguardian.edge.lorawan import encode_payload


@dataclass(slots=True)
class IngestReport:
    """What a run of the pipeline actually moved."""

    uplinks: int = 0
    objects: int = 0
    bytes_written: int = 0
    plots: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def megabytes(self) -> float:
        return self.bytes_written / 1_048_576

    @property
    def bytes_per_uplink(self) -> float:
        return self.bytes_written / max(self.uplinks, 1)

    def as_dict(self) -> dict:
        return {
            "uplinks": self.uplinks,
            "objects": self.objects,
            "megabytes": round(self.megabytes, 3),
            "bytes_per_uplink": round(self.bytes_per_uplink, 1),
            "plots": self.plots,
            "started_at": self.started_at.isoformat(),
        }


def _known_plots() -> tuple:
    from earthguardian.config import PLOTS

    return PLOTS


def ingest_fleet(uplinks: pd.DataFrame, store: DocumentStore) -> IngestReport:
    """Land every uplink in the raw zone, one object per plot-day."""
    report = IngestReport()
    store.delete_prefix("raw/uplinks/")

    frame = uplinks.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["_day"] = frame["timestamp"].dt.strftime("%Y-%m-%d")

    offsets = {plot.plot_id: plot.timezone_offset for plot in _known_plots()}

    for plot_id, plot_frame in frame.groupby("plot_id", sort=True):
        counter = 0
        offset = offsets.get(str(plot_id), 0)
        for sequence, (day, day_frame) in enumerate(plot_frame.groupby("_day", sort=True)):
            documents = []
            for row in day_frame.sort_values("timestamp").itertuples():
                payload = encode_payload(
                    soil_moisture=row.soil_moisture,
                    air_temp_c=row.air_temp_c,
                    humidity_pct=row.humidity_pct,
                    lux=row.lux,
                    soil_ph=row.soil_ph,
                    air_quality_ppm=row.air_quality_ppm,
                    battery_pct=row.battery_pct,
                )
                counter += 1
                documents.append(
                    wrap_uplink(
                        str(plot_id),
                        payload,
                        timestamp=row.timestamp,
                        spreading_factor=int(row.spreading_factor),
                        data_rate=int(str(row.data_rate).removeprefix("DR")),
                        rssi=float(row.rssi_dbm),
                        battery_pct=float(row.battery_pct),
                        frame_counter=counter,
                        timezone_offset_h=offset,
                    )
                )
            summary = store.put_documents(
                DocumentStore.uplink_key(str(plot_id), str(day), sequence), documents
            )
            report.objects += 1
            report.uplinks += len(documents)
            report.bytes_written += summary.size_bytes
        report.plots.append(str(plot_id))

    store.put_json("raw/_manifest.json", report.as_dict())
    return report


def read_raw_zone(store: DocumentStore, plot_lookup: dict[str, str]) -> pd.DataFrame:
    """Decode the raw zone back into flat records - the Lambda's job.

    ``plot_lookup`` maps DevEui to plot id, which is the device registry a real
    deployment keeps. The uplink itself carries no plot: the network knows a
    radio, and only the application knows what field it is standing in.
    """
    rows: list[dict] = []
    for envelope in store.scan():
        record = decode_uplink(envelope)
        record["plot_id"] = plot_lookup.get(record["dev_eui"], "unknown")
        rows.append(record)

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame = frame.sort_values(["plot_id", "timestamp"], ignore_index=True)
    return frame


def curate(
    store: DocumentStore,
    curated: CuratedStore,
    plots: tuple = (),
    truth: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
) -> dict[str, int]:
    """Promote the raw zone into the curated relations."""
    lookup = {device_identity(plot.plot_id)[1]: plot.plot_id for plot in plots}
    readings = read_raw_zone(store, lookup)

    keep = [
        "plot_id",
        "timestamp",
        "dev_eui",
        "frame_counter",
        "soil_moisture",
        "air_temp_c",
        "humidity_pct",
        "lux",
        "soil_ph",
        "air_quality_ppm",
        "battery_pct",
        "data_rate",
        "spreading_factor",
        "rssi_dbm",
        "snr_db",
    ]
    written = {
        "readings": curated.write_frame("readings", readings[[c for c in keep if c in readings]])
    }
    if truth is not None and not truth.empty:
        written["ground_truth"] = curated.write_frame("ground_truth", truth)
    if events is not None and not events.empty:
        written["site_events"] = curated.write_frame("site_events", events)
    return written
