"""Run exactly one complete nonpublishing actual source collection."""

import json
import unittest

import adapter


class TestArtsCouncilMalta(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"].casefold() for record in records}), len(records)
        )
        self.assertTrue(
            all(record["source"] == adapter.SOURCE_ID for record in records)
        )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
