import sys
from pathlib import Path

# Make eval/ modules (score.py, run_eval.py) importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
