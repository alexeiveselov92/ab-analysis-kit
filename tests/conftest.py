"""Root test fixtures: make tests/_helpers importable everywhere."""

import sys
from pathlib import Path

_HELPERS = Path(__file__).parent / "_helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))
