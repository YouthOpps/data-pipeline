"""One actual, nonpublishing IBM programme collection test."""

import json
import os
import sys
import unittest

import adapter


class LiveCollectionTest(unittest.TestCase):
    def test_live_collection(self):
        if "DATA_SOURCE_TOKEN" in os.environ:
            self.fail("Live tests must not receive publication credentials")
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        for record in records:
            self.assertTrue(record["source_documents"])
            self.assertTrue(record["summary"])
            self.assertEqual(record["id"], adapter.profile_identity(record))
            self.assertFalse(record["eligible_countries"])
        json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
