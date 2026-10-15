"""One live, non-publishing collection test with complete JSON stdout."""

import json
import os
import unittest

if os.environ.get("DATA_SOURCE_TOKEN"):
    raise RuntimeError(
        "Live collection must not receive publication credentials"
    )

import adapter


class LiveAdapterTest(unittest.TestCase):

    def test_live_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
