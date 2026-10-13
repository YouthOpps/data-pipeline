"""Run one complete, nonpublishing collection against official source inputs."""

import hashlib
import json
import unittest

import adapter


class TestStudyInCzechia(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(len({record["id"] for record in records}), len(records))
        self.assertTrue(all(
            record["source"] == adapter.SOURCE_ID for record in records
        ))
        for record in records:
            identity = adapter.programme_id(record["url"])
            expected = hashlib.sha256(
                f"{adapter.SOURCE_ID}|programme-{identity}".encode()
            ).hexdigest()[:24]
            self.assertEqual(record["id"], expected)
            self.assertTrue(record["summary"].strip())
            self.assertIn(record["language"], ("en", "cs"))
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
