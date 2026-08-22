"""Cloud tier - the thesis's AWS pipeline, emulated locally.

The documented architecture is LoRaWAN gateway to AWS IoT Core, an IoT Rule
invoking Lambda to decode the binary payload, raw objects landing in S3, curated
records in a relational store, and API Gateway serving the app. Reproducing that
would make the project impossible to run from a clone, so the same *shape* is
built against the filesystem:

===============================  ==============================================
Managed service                  Local stand-in
===============================  ==============================================
AWS IoT Core for LoRaWAN         :func:`~earthguardian.cloud.iot_core.wrap_uplink`
IoT Rule + Lambda decoder        :func:`~earthguardian.cloud.pipeline.decode_uplink`
S3 raw zone (NoSQL/documents)    :class:`~earthguardian.cloud.documents.DocumentStore`
Relational curated tier          :class:`~earthguardian.cloud.relational.CuratedStore`
API Gateway + QuickSight         the CLI and the Streamlit console
===============================  ==============================================

The two-tier split - raw documents, curated relations - is the thesis's own
design, not an invention here: *"os dados brutos sao armazenados em um banco
NoSQL, enquanto os dados filtrados e enriquecidos com insights sao organizados
em um banco relacional."*

The uplink envelope is the real one, field for field, as AWS IoT Core for
LoRaWAN emits it. Inventing a tidier schema would have been easier and would
have taught nothing about the thing being modelled.
"""

from earthguardian.cloud.documents import DocumentStore, ObjectSummary
from earthguardian.cloud.iot_core import decode_uplink, wrap_uplink
from earthguardian.cloud.pipeline import IngestReport, curate, ingest_fleet
from earthguardian.cloud.relational import CuratedStore

__all__ = [
    "CuratedStore",
    "DocumentStore",
    "IngestReport",
    "ObjectSummary",
    "curate",
    "decode_uplink",
    "ingest_fleet",
    "wrap_uplink",
]
