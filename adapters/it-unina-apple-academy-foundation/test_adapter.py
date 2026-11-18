"""Run the sole complete genuine Foundation collection without publication."""

import json
import unittest

import adapter


class FoundationCollectionTest(unittest.TestCase):
    def test_complete_foundation_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), len(adapter.COHORTS))
        print(json.dumps(records, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
