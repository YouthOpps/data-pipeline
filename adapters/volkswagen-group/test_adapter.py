"""One actual nonpublishing Volkswagen AG finite youth collection test."""

import json
import sys
import unittest

import adapter


class LiveCollectionTest(unittest.TestCase):
    def test_live_collection(self):
        records = adapter.collect()
        adapter.validate_records(records, require_complete=True)
        self.assertGreater(len(records), 0)
        json.dump(records, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
