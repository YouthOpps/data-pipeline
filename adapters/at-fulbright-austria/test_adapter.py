"""One complete real non-publishing Fulbright Austria collection test."""

import json
import sys
import unittest

import adapter


class LiveAdapterTest(unittest.TestCase):
    def test_live_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"] for record in records}), len(records)
        )
        self.assertTrue(all(record["summary"] for record in records))
        self.assertTrue(
            all(
                record["url"] in record["classification"]["evidence"]
                for record in records
            )
        )
        print(json.dumps(records, ensure_ascii=False, indent=2))
        print(
            f"Validated {len(records)} Fulbright Austria awards and teaching positions",
            file=sys.stderr,
        )


if __name__ == "__main__":
    unittest.main()
