"""Explicit live publisher verification; never publishes results."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("at_oead_ernst_mach", Path(__file__).with_name("adapter.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class LiveAdapterTest(unittest.TestCase):
    def test_live_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], adapter.SOURCE_ID)
        self.assertEqual(records[0]["url"], adapter.SOURCE_URL)
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
