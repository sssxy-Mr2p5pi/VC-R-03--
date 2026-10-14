"""Fixed-source CLI, calling the same service used by the web button."""
import argparse
import json
from pathlib import Path

from vcr03 import ROOT
from vcr03.ingest import service

SOURCES = {'full': ROOT/'data/raw/online_retail_II.xlsx',
           'performance': ROOT/'data/processed/performance-10000.xlsx'}
DATABASES = {'full': ROOT/'data/processed/app-full.db',
             'performance': ROOT/'data/processed/app-performance.db'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=SOURCES, default='full')
    parser.add_argument('--database', type=Path)
    args = parser.parse_args(argv)
    result = service.ingest_workbook(SOURCES[args.source], args.database or DATABASES[args.source],
                                     progress=lambda item: print(json.dumps(item, ensure_ascii=False), flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
