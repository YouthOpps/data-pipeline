"""Run the sole nonpublishing live collection and emit all records."""

import json
import unittest

import adapter


class TestIPDJ(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(len({r["id"] for r in records}), len(records))
        self.assertTrue(all(r["source"] == adapter.SOURCE_ID for r in records))
        self.assertTrue(
            all(
                r["summary"] and r["classification"]["evidence"]
                for r in records
            )
        )
        self.assertTrue(any("/-/" in r["url"] for r in records))
        self.assertTrue(any("/-/" not in r["url"] for r in records))
        for record in records:
            self.assertEqual(record["categories"], [record["category"]])
            self.assertEqual(
                record["classification"]["status"],
                "unknown" if record["category"] == "other" else "classified",
            )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
