"""Run the original standalone KAUST VSRP collection without publication."""

import json
import os

import adapter


def main():
    if os.environ.get("DATA_SOURCE_TOKEN"):
        raise RuntimeError("The nonpublishing test refuses a publication token")
    records = adapter.collect()
    adapter.validate_records(records)
    print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    main()
