"""One real complete CampusBourses collection; never publishes or writes records."""

import importlib.util
import json
import os
from pathlib import Path
import unittest

if os.environ.get("DATA_SOURCE_TOKEN"):
    raise RuntimeError(
        "The live test must not receive publication credentials"
    )

spec = importlib.util.spec_from_file_location(
    "fr_campus_france", Path(__file__).with_name("adapter.py")
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class LiveAdapterTest(unittest.TestCase):
    def test_live_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"] for record in records}), len(records)
        )
        self.assertTrue(
            all(record["status"] == "unknown" for record in records)
        )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
