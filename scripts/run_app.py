"""Run the module-A local verification UI."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vcr03.web import create_app  # noqa: E402

if __name__ == '__main__':
    create_app().run(host='127.0.0.1',port=5000,debug=False)
