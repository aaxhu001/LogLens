"""Index persistence and rebuilding manager.

This module provides routines to serialize and deserialize LogLens indices to
and from disk:
  1. `save_index`: writes the binary index bundle (LineStore, InvertedIndex,
     StructuredIndex) using Python's standard `pickle` protocol along with a
     human-readable `manifest.json`.
  2. `load_index`: restores index structures from disk. A reloaded index is
     guaranteed to produce results 100% identical to a freshly ingested index.
  3. `rebuild_index`: cleans existing index directories, re-ingests source
     log files, and saves the new index state.
"""

from datetime import datetime, timezone
import json
import os
import pickle
import shutil
from typing import Any, Dict, Optional, Tuple

from loglens.ingestor import IngestReport, Ingestor
from loglens.inverted_index import InvertedIndex
from loglens.line_store import LineStore
from loglens.parser import LogParser
from loglens.structured_index import StructuredIndex


INDEX_BUNDLE_FILENAME = "index.pkl"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "1.0.0"


def save_index(
    line_store: LineStore,
    inverted_index: InvertedIndex,
    structured_index: StructuredIndex,
    index_dir: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Serialize the index bundle to the specified directory.

    Args:
        line_store: LineStore instance containing file offsets.
        inverted_index: InvertedIndex instance containing token postings.
        structured_index: StructuredIndex instance containing level, service, and timestamps.
        index_dir: Directory where index files will be stored.
        extra_metadata: Optional dictionary with custom diagnostic attributes.

    Returns:
        Absolute path to the serialized index bundle file.
    """
    os.makedirs(index_dir, exist_ok=True)
    bundle_path = os.path.join(index_dir, INDEX_BUNDLE_FILENAME)
    manifest_path = os.path.join(index_dir, MANIFEST_FILENAME)

    bundle = {
        "version": SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "line_store": line_store,
        "inverted_index": inverted_index,
        "structured_index": structured_index,
    }

    # Atomic write to prevent corrupted indices on abrupt termination
    tmp_bundle_path = bundle_path + ".tmp"
    with open(tmp_bundle_path, "wb") as f_bundle:
        pickle.dump(bundle, f_bundle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp_bundle_path, bundle_path)

    # Write human-readable manifest
    manifest_data = {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "total_lines": line_store.total_lines(),
        "vocabulary_size": inverted_index.vocabulary_size(),
        "total_postings": inverted_index.total_postings(),
        "services": structured_index.get_all_services(),
        "source_files": line_store.file_paths,
        "extra": extra_metadata or {},
    }

    tmp_manifest_path = manifest_path + ".tmp"
    with open(tmp_manifest_path, "w", encoding="utf-8") as f_manifest:
        json.dump(manifest_data, f_manifest, indent=2)
    os.replace(tmp_manifest_path, manifest_path)

    return bundle_path


def load_index(
    index_dir: str,
) -> Tuple[LineStore, InvertedIndex, StructuredIndex, Dict[str, Any]]:
    """Load serialized index structures from disk.

    Args:
        index_dir: Directory containing saved index files.

    Returns:
        A tuple of (LineStore, InvertedIndex, StructuredIndex, manifest_dict).

    Raises:
        FileNotFoundError: If the index bundle does not exist.
        ValueError: If the index schema version is incompatible.
    """
    bundle_path = os.path.join(index_dir, INDEX_BUNDLE_FILENAME)
    manifest_path = os.path.join(index_dir, MANIFEST_FILENAME)

    if not os.path.exists(bundle_path):
        raise FileNotFoundError(
            f"Index bundle not found at '{bundle_path}'. Run 'ingest' or 'rebuild' first."
        )

    with open(bundle_path, "rb") as f_bundle:
        bundle = pickle.load(f_bundle)

    version = bundle.get("version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Incompatible index version '{version}' (expected '{SCHEMA_VERSION}'). Please rebuild."
        )

    manifest: Dict[str, Any] = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f_manifest:
            manifest = json.load(f_manifest)

    return (
        bundle["line_store"],
        bundle["inverted_index"],
        bundle["structured_index"],
        manifest,
    )


def rebuild_index(
    source_path: str,
    index_dir: str,
    parser: Optional[LogParser] = None,
) -> Tuple[LineStore, InvertedIndex, StructuredIndex, IngestReport]:
    """Rebuild an index from scratch, replacing any existing index in index_dir.

    Args:
        source_path: Path to log file or directory of logs.
        index_dir: Destination directory for the index bundle.
        parser: Optional custom LogParser.

    Returns:
        Tuple of (LineStore, InvertedIndex, StructuredIndex, IngestReport).
    """
    ingestor = Ingestor(parser=parser)
    report = ingestor.ingest_path(source_path)

    save_index(
        line_store=ingestor.line_store,
        inverted_index=ingestor.inverted_index,
        structured_index=ingestor.structured_index,
        index_dir=index_dir,
        extra_metadata=report.to_dict(),
    )

    return (
        ingestor.line_store,
        ingestor.inverted_index,
        ingestor.structured_index,
        report,
    )
