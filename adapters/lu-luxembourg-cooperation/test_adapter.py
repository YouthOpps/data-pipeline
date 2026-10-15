"""Run the sole complete live source collection without publication or files."""

import json
import unittest

import adapter


class TestLuxembourgCooperation(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len(records), len({record["id"].lower() for record in records})
        )
        self.assertTrue(
            all(record["source"] == adapter.SOURCE_ID for record in records)
        )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
