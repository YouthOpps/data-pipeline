"""Run the complete genuine PhD Track collection without publication."""

import json
import unittest

import adapter


class PhDTrackCollectionTest(unittest.TestCase):
    def test_complete_admissions_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), 1)
        print(json.dumps(records, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
