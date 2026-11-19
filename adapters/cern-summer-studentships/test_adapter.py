"""Run the complete genuine CERN summer studentship collection once."""

import json
import unittest

import adapter


class CERNStudentshipCollectionTest(unittest.TestCase):
    def test_complete_studentship_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertEqual(len(records), len(adapter.PROGRAMMES))
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
