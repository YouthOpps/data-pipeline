"""Exercise the actual ANU international admissions source without publishing."""

import json
import os
import unittest

import adapter


class ANUAdmissionTest(unittest.TestCase):
    def test_complete_live_collection(self):
        self.assertNotIn("DATA_SOURCE_TOKEN", os.environ)
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), 2)
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
