"""Run the original full nonpublishing MSEn admissions collection."""

import json
import unittest

import adapter


class MSEnCollectionTest(unittest.TestCase):
    def test_complete_admissions_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), 1)
        print(json.dumps(records, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
