"""Run the original standalone KAUST VSRP collection without publication."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import adapter


def main():
    if os.environ.get("DATA_SOURCE_TOKEN"):
        raise RuntimeError("The nonpublishing test refuses a publication token")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-native-html", type=Path,
                        help="Replay the original decoded, retained native inventory without network access")
    args = parser.parse_args()
    if args.replay_native_html:
        with args.replay_native_html.open("rb") as stream:
            document = stream.read(adapter.MAX_BODY + 1)
        expected = "fe95a30a8ef07c8f90166e84eeaf77060448d1d71d4a380cc2e8ddde95dc8299"
        if len(document) > adapter.MAX_BODY or hashlib.sha256(document).hexdigest() != expected:
            raise ValueError("Input differs from the original native inventory")
        records = adapter.normalize(adapter.extract_facts(document.decode("utf-8")),
                                    datetime.now(timezone.utc))
    else:
        records = adapter.collect()
    adapter.validate_records(records)
    print(json.dumps(records, ensure_ascii=False))


if __name__ == "__main__":
    main()
