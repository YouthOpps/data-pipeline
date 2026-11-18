"""Run the complete genuine Strasbourg first-year master’s collection without publication."""

import json
import unittest

import adapter


class StrasbourgAdmissionsCollectionTest(unittest.TestCase):
    def test_complete_admissions_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), len(adapter.PROGRAMS))
        print(json.dumps(records, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
