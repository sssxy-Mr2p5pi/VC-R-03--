"""CLI adapter; all import logic lives in the shared service."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vcr03.cli import main  # noqa: E402

if __name__ == '__main__':
    main()
