"""Fixed official/sample inputs; same reconciliation service as the page."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vcr03.cli import SOURCES, DATABASES  # noqa: E402
from vcr03.matching import service  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=SOURCES, default='full')
    parser.add_argument('--database', type=Path)
    args = parser.parse_args(argv)
    result = service.reconcile_workbook(SOURCES[args.source], args.database or DATABASES[args.source])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
