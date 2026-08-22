"""Raw zone - the document store the thesis calls the NoSQL tier.

Uplinks land here exactly as AWS IoT Core for LoRaWAN emitted them, one JSON
document per uplink, in Hive-partitioned keys:

    raw/uplinks/plot_id=CE-GUARACIABA-03/date=2025-08-22/part-00017.jsonl.gz

The partitioning is not decoration. It is what lets a query for one plot-week
read three files instead of a year of them, and it is the layout Athena, DuckDB
or Spark would expect to find if this were really S3.

The zone is **append-only and never rewritten**. A bug in the curation step
costs a re-run; a bug in a pipeline that transformed data in place costs the
season's measurements. That is the whole argument for keeping a raw tier at all,
and it is why the curated store is derived rather than authoritative.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    key: str
    size_bytes: int
    last_modified: datetime
    records: int = 0

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024


class DocumentStore:
    """Prefix-addressable JSONL storage, gzip-compressed, S3-shaped."""

    def __init__(self, root: Path, compress: bool = True) -> None:
        self.root = Path(root)
        self.compress = compress
        self.root.mkdir(parents=True, exist_ok=True)

    # -- keys ------------------------------------------------------------------

    def _path(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"unsafe object key: {key!r}")
        suffix = ".gz" if self.compress and not key.endswith(".gz") else ""
        return self.root / (key + suffix)

    def _key(self, path: Path) -> str:
        key = path.relative_to(self.root).as_posix()
        return key[:-3] if key.endswith(".gz") else key

    @staticmethod
    def uplink_key(plot_id: str, day: str, sequence: int) -> str:
        return f"raw/uplinks/plot_id={plot_id}/date={day}/part-{sequence:05d}.jsonl"

    # -- writes ----------------------------------------------------------------

    def put_documents(self, key: str, documents: list[dict]) -> ObjectSummary:
        """Write one JSONL object. One line per uplink, order preserved."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(document, default=str) for document in documents)
        data = body.encode("utf-8")
        path.write_bytes(gzip.compress(data, mtime=0) if self.compress else data)
        return self._summary(path, records=len(documents))

    def put_json(self, key: str, payload: dict | list) -> ObjectSummary:
        return (
            self.put_documents(key, [payload])
            if isinstance(payload, dict)
            else self.put_documents(key, payload)
        )

    # -- reads -----------------------------------------------------------------

    def get_documents(self, key: str) -> list[dict]:
        path = self._path(key)
        raw = path.read_bytes()
        text = gzip.decompress(raw).decode("utf-8") if path.suffix == ".gz" else raw.decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line]

    def list_objects(self, prefix: str = "") -> list[ObjectSummary]:
        return sorted(
            (
                self._summary(path)
                for path in self.root.rglob("*")
                if path.is_file() and self._key(path).startswith(prefix)
            ),
            key=lambda summary: summary.key,
        )

    def scan(self, plot_id: str | None = None, day: str | None = None) -> Iterator[dict]:
        """Read documents back, pruning partitions the way a query engine would."""
        prefix = "raw/uplinks/"
        if plot_id:
            prefix += f"plot_id={plot_id}/"
            if day:
                prefix += f"date={day}/"
        for summary in self.list_objects(prefix):
            yield from self.get_documents(summary.key)

    def partitions(self, prefix: str = "raw/") -> list[dict[str, str]]:
        """The partition values present, parsed out of the keys."""
        found: list[dict[str, str]] = []
        for summary in self.list_objects(prefix):
            values = {}
            for segment in summary.key.split("/")[:-1]:
                if "=" in segment:
                    name, _, value = segment.partition("=")
                    values[name] = value
            if values and values not in found:
                found.append(values)
        return found

    def delete_prefix(self, prefix: str) -> int:
        removed = 0
        for summary in self.list_objects(prefix):
            self._path(summary.key).unlink(missing_ok=True)
            removed += 1
        return removed

    def total_bytes(self, prefix: str = "") -> int:
        return sum(summary.size_bytes for summary in self.list_objects(prefix))

    def _summary(self, path: Path, records: int = 0) -> ObjectSummary:
        stat = path.stat()
        return ObjectSummary(
            key=self._key(path),
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            records=records,
        )
