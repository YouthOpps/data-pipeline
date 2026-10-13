"""Run one complete nonpublishing live collection; stdout stays in memory."""

import json
import unittest

import adapter


class TestUniversityOfZagreb(unittest.TestCase):
    def test_complete_public_collection(self):
        records = adapter.collect()
        adapter.validate_records(records)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"] for record in records}), len(records)
        )
        self.assertTrue(
            all(record["source"] == adapter.SOURCE_ID for record in records)
        )
        self.assertTrue(
            all(
                record["summary"] and record["classification"]["evidence"]
                for record in records
            )
        )
        self.assertTrue(
            any(record["kind"] == "programme-overview" for record in records)
        )
        self.assertTrue(
            any("/nc/vijest/article/" in record["url"] for record in records)
        )
        notice_summaries = {}
        for record in records:
            if "/nc/vijest/article/" not in record["url"]:
                continue
            summaries = notice_summaries.setdefault(record["url"], set())
            self.assertNotIn(record["summary"], summaries)
            summaries.add(record["summary"])
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
