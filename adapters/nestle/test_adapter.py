"""The sole actual, complete, nonpublishing Nestle category test."""

import json
import os
import sys
import unittest

import adapter


class LiveCollectionTest(unittest.TestCase):

    def test_live_collection(self):
        self.assertNotIn("DATA_SOURCE_TOKEN", os.environ)
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        for record in records:
            self.assertEqual(
                record["id"],
                "nestle-" + adapter.canonical_job(record["url"])[0])
            self.assertEqual(record["source"], "nestle")
            self.assertEqual(record["eligible_countries"], [])
            self.assertTrue(record["source_documents"])
        json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
