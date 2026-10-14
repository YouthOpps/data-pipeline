"""Run one complete live collection without publishing or saving its records."""

import copy
import json
import re
import unittest

import adapter


class TestFRSE(unittest.TestCase):
    def test_complete_public_collection(self):
        with adapter.execution():
            with adapter.phase("prepublication", adapter.COLLECTION_TIMEOUT):
                attempt = adapter.utc_now()
                adapter.prepare_collection()
                pages = adapter.read_pages()
                records = adapter.parse_inventory(pages)
                adapter.fresh_records(records, [], attempt, adapter.utc_now())
        adapter.validate_records(records)
        for key, info in adapter.ACCESS_POLICIES.items():
            root = pages[0][info["url"]]
            expected = adapter.policy_fingerprint(root, key)
            self.assertEqual(expected, info["sha256"])
            unrelated = copy.deepcopy(root)
            for node in unrelated.walk():
                if node.tag in ("nav", "footer", "form"):
                    node.children.append(" Unrelated layout change.")
            self.assertEqual(
                adapter.policy_fingerprint(unrelated, key), expected
            )
            changed = copy.deepcopy(root)
            adapter.policy_content(changed, key).children.append(
                " Changed legal condition."
            )
            self.assertNotEqual(
                adapter.policy_fingerprint(changed, key), expected
            )
            changed = copy.deepcopy(root)
            link = adapter.Node(
                "a", [("href", "https://example.org/changed-policy")]
            )
            adapter.policy_content(changed, key).children.append(link)
            self.assertNotEqual(
                adapter.policy_fingerprint(changed, key), expected
            )
            missing = copy.deepcopy(root)
            adapter.policy_content(missing, key).attrs["class"] = "missing"
            with self.assertRaises(adapter.AdapterError):
                adapter.policy_fingerprint(missing, key)
            duplicate = copy.deepcopy(root)
            adapter.nodes(duplicate, "main")[0].children.append(
                copy.deepcopy(adapter.policy_content(duplicate, key))
            )
            with self.assertRaises(adapter.AdapterError):
                adapter.policy_fingerprint(duplicate, key)
        self.assertTrue(records)
        self.assertEqual(
            len({record["id"] for record in records}), len(records)
        )
        self.assertTrue(
            all(record["source"] == adapter.SOURCE_ID for record in records)
        )
        for record in records:
            details = record["details"]
            self.assertNotIn("paragraphs", details)
            self.assertEqual(len(details["material_sha256"]), 64)
            for conflict in record.get("source_conflicts", []):
                self.assertEqual(conflict["status"], "unresolved")
                self.assertTrue(conflict["governing_documents"])
                self.assertIn("conflicting-overview", details["verification"])
            if "WorldSkills" in record["title"]:
                self.assertIsNone(record["deadline"])
                self.assertTrue(
                    any(
                        "WorldSkills-specific" in limit
                        for limit in record["limitations"]
                    )
                )

        # Perturb a late field of genuine retrieved HTML, never the live result.
        url = next(
            url for url in pages[1] if "/finansowanie-projektow/" in url
        )
        content = adapter.detail_content(pages[0][url])
        title = adapter.nodes(content, "h2")[0].text()
        before = adapter.record_from_content(url, title, content, "grants")
        changed_content = copy.deepcopy(content)
        values = adapter.nodes(changed_content, class_name="list_right")
        self.assertTrue(values)
        values[-1].children.append(" Finansowanie: 12345 EUR.")
        changed = adapter.record_from_content(
            url, title, changed_content, "grants"
        )
        self.assertEqual(before["id"], changed["id"])
        self.assertNotEqual(before["details"], changed["details"])
        self.assertNotEqual(
            before["details"]["fields"], changed["details"]["fields"]
        )
        self.assertNotEqual(
            before["details"]["material_sha256"],
            changed["details"]["material_sha256"],
        )
        adapter.fresh_records(
            [before], [], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"
        )
        adapter.fresh_records(
            [changed], [before], "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"
        )
        self.assertEqual(changed["updated_at"], "2026-01-02T00:00:00Z")
        self.assertEqual(changed["created_at"], before["created_at"])
        unchanged = copy.deepcopy(changed)
        adapter.fresh_records(
            [unchanged],
            [changed],
            "2026-01-03T00:00:00Z",
            "2026-01-03T00:00:00Z",
        )
        self.assertEqual(unchanged["updated_at"], changed["updated_at"])
        for text in (
            "Zgłoszenia do 15 października. Wydarzenie: 20 listopada 2026.",
            "Wydarzenie odbędzie się 15 października 2026.",
            "Projekt trwa do 15 października 2026.",
            "Zgłoszenia do 31 lutego 2026.",
            "Planowany nabór potrwa do 11 grudnia 2026.",
            "Czas trwania całego projektu: w I terminie naboru wniosków; nie później niż do 30 czerwca 2026.",
        ):
            self.assertEqual(adapter.closing_dates([text]), [])
        self.assertEqual(
            adapter.closing_dates(["Zgłoszenia do 15 października 2026."])[0][
                "date"
            ],
            "2026-10-15",
        )
        future = adapter.Node("p")
        future.children = ["Nabór zostanie zakończony 11 grudnia 2026 roku."]
        projected = adapter.record_from_content(url, title, future, "grants")
        self.assertEqual(projected["status"], "unknown")
        self.assertFalse(projected["application_closed"])
        self.assertIsNone(projected["deadline"])
        self.assertNotIn(
            "duration",
            adapter.factual_extracts(["Kontakt: 40lat@erasmusplus.org.pl"]),
        )
        self.assertIn(
            "2200",
            json.dumps(
                adapter.factual_extracts(
                    [
                        "Napisz autorski tekst w języku polskim, liczący od 2200 do 2600 znaków ze spacjami."
                    ]
                )
            ),
        )
        for candidate, root in pages[0].items():
            if candidate not in pages[1] or pages[1][candidate] == "jobs":
                continue
            try:
                offers = adapter.anchored_offers(adapter.detail_content(root))
            except adapter.AdapterError:
                continue
            for anchor, _, content in offers:
                record = next(
                    r for r in records if r["url"] == candidate + "#" + anchor
                )
                self.assertTrue(record["details"]["facts"])
                extracts = json.dumps(
                    record["details"]["facts"], ensure_ascii=False
                )
                for requirement in re.findall(
                    r"\d+ sekund|\d+ do \d+ znaków|w języku polskim|sympatyków programu",
                    content.text(),
                ):
                    self.assertIn(requirement, extracts)
                self.assertEqual(
                    record["application_dates"],
                    adapter.closing_dates(adapter.content_blocks(content)),
                )
        print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
