"""Run the sole actual, non-publishing Mercedes-Benz collection test."""

import json
import os
import sys

import adapter


def test_live_collection():
    if os.environ.get("DATA_SOURCE_TOKEN"):
        raise RuntimeError("The live test must not receive publication credentials")
    records = adapter.collect()
    adapter.validate_records(records)
    json.dump(records, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    test_live_collection()
