"""Run one complete nonpublishing live collection; stdout stays in memory."""

import json
import unittest

import adapter


class TestINSAIT(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"] for record in records}), len(records)
        )
        self.assertTrue(all(record["summary"] for record in records))
        self.assertTrue(
            all(
                record["classification"]["method"] == "publisher-html"
                for record in records
            )
        )
        self.assertTrue(
            all(record["source"] == adapter.SOURCE_ID for record in records)
        )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
