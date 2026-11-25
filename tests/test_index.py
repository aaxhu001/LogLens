"""Unit tests for InvertedIndex, StructuredIndex, LineStore, and Persistence."""

import os
import shutil
import tempfile
import unittest
from array import array

from loglens.ingestor import Ingestor
from loglens.inverted_index import InvertedIndex
from loglens.line_store import LineStore
from loglens.persistence import load_index, rebuild_index, save_index
from loglens.query_engine import QueryEngine
from loglens.structured_index import StructuredIndex, parse_iso_to_epoch


class TestLineStore(unittest.TestCase):
    """Verify LineStore disk offset tracking and random access retrieval."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sample_file = os.path.join(self.temp_dir, "test.log")
        with open(self.sample_file, "w", encoding="utf-8") as f:
            f.write("line 0: first entry\n")
            f.write("line 1: second entry\n")
            f.write("line 2: third entry\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_line_store_offsets_and_fetch(self):
        store = LineStore()
        file_id = store.register_file(self.sample_file)
        self.assertEqual(file_id, 0)

        # Record exact byte offsets
        with open(self.sample_file, "rb") as f:
            pos0 = f.tell()
            l0 = f.readline()
            pos1 = f.tell()
            l1 = f.readline()
            pos2 = f.tell()
            l2 = f.readline()

        id0 = store.add_line(file_id, pos0, len(l0))
        id1 = store.add_line(file_id, pos1, len(l1))
        id2 = store.add_line(file_id, pos2, len(l2))

        self.assertEqual(id0, 0)
        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)
        self.assertEqual(store.total_lines(), 3)

        self.assertEqual(store.get_line(0), "line 0: first entry")
        self.assertEqual(store.get_line(1), "line 1: second entry")
        self.assertEqual(store.get_line(2), "line 2: third entry")

    def test_context_retrieval(self):
        store = LineStore()
        fid = store.register_file(self.sample_file)
        with open(self.sample_file, "rb") as f:
            while True:
                pos = f.tell()
                b = f.readline()
                if not b:
                    break
                store.add_line(fid, pos, len(b))

        context = store.get_context(1, window=1)
        # Should return line 0, 1, 2
        self.assertEqual(len(context), 3)
        self.assertEqual(context[0][0], 0)
        self.assertFalse(context[0][2])
        self.assertEqual(context[1][0], 1)
        self.assertTrue(context[1][2])  # target line


class TestInvertedIndex(unittest.TestCase):
    """Verify posting list construction and token indexing."""

    def test_add_and_retrieve_postings(self):
        idx = InvertedIndex()
        idx.add_line_tokens(0, ["timeout", "database", "timeout"])  # Duplicate token
        idx.add_line_tokens(1, ["database", "connection"])
        idx.add_line_tokens(2, ["timeout", "retry"])

        p_timeout = idx.get_postings("timeout")
        self.assertEqual(list(p_timeout), [0, 2])

        p_db = idx.get_postings("database")
        self.assertEqual(list(p_db), [0, 1])

        p_missing = idx.get_postings("nonexistent")
        self.assertEqual(len(p_missing), 0)

        self.assertEqual(idx.vocabulary_size(), 4)


class TestStructuredIndex(unittest.TestCase):
    """Verify structured metadata filtering and bisect time range conversion."""

    def test_time_range_bisect_inclusive(self):
        struct = StructuredIndex()
        # Add 5 lines with timestamps 100, 200, 300, 400, 500
        for i, ts in enumerate([100, 200, 300, 400, 500]):
            struct.add_entry(i, ts, "INFO", "payments")

        # Range covering exact elements 200..400 -> LineIDs 1..3
        min_id, max_id = struct.time_range_to_id_range(from_epoch=200, to_epoch=400)
        self.assertEqual((min_id, max_id), (1, 3))

        # Range outside bounds
        min_id, max_id = struct.time_range_to_id_range(from_epoch=50, to_epoch=99)
        self.assertTrue(min_id > max_id)

        # Open lower bound (<= 300)
        min_id, max_id = struct.time_range_to_id_range(from_epoch=None, to_epoch=300)
        self.assertEqual((min_id, max_id), (0, 2))

        # Open upper bound (>= 300)
        min_id, max_id = struct.time_range_to_id_range(from_epoch=300, to_epoch=None)
        self.assertEqual((min_id, max_id), (2, 4))


class TestPersistence(unittest.TestCase):
    """Verify save, load, and rebuild equality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.index_dir = os.path.join(self.temp_dir, "index")
        self.log_file = os.path.join(self.temp_dir, "test.log")
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("2026-03-01T10:00:00Z INFO auth user login session 101\n")
            f.write("2026-03-01T10:01:00Z WARN db slow query 2000ms\n")
            f.write("2026-03-01T10:02:00Z ERROR payments timeout calling db after 3000ms\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_save_and_reload_identity(self):
        # 1. Fresh ingest
        ingestor = Ingestor()
        ingestor.ingest_path(self.log_file)

        fresh_engine = QueryEngine(
            ingestor.line_store, ingestor.inverted_index, ingestor.structured_index
        )
        fresh_res = fresh_engine.search("timeout db")

        # 2. Save
        save_index(
            ingestor.line_store,
            ingestor.inverted_index,
            ingestor.structured_index,
            self.index_dir,
        )

        # 3. Load
        store_loaded, inv_loaded, struct_loaded, manifest = load_index(self.index_dir)
        reloaded_engine = QueryEngine(store_loaded, inv_loaded, struct_loaded)
        reloaded_res = reloaded_engine.search("timeout db")

        # Results must be 100% identical
        self.assertEqual(fresh_res.total_matches, reloaded_res.total_matches)
        self.assertEqual(fresh_res.level_counts, reloaded_res.level_counts)
        self.assertEqual(fresh_res.lines, reloaded_res.lines)


if __name__ == "__main__":
    unittest.main()
