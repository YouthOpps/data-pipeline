"""The sole genuine, complete, nonpublishing live collection test."""
import json
import os
import sys

import adapter


def main():
    if os.environ.get('DATA_SOURCE_TOKEN'):
        raise adapter.ScopeError(
            'Native test must not receive publication token')
    records = adapter.collect()
    adapter.validate_records(records)
    json.dump(records, sys.stdout, ensure_ascii=False)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
