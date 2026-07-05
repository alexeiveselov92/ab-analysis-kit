"""Root test fixtures: put the repo root and tests/_helpers on sys.path.

The repo-root insert lets ``from tests.<pkg> import ...`` resolve regardless of how
``pip install -e .`` exposes the tree — a strict (PEP-660) editable install does not
put the root on ``sys.path``, which silently broke CI collection (m4 CI fix). This
conftest is the ancestor of every test module, so it runs before any collection.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_HELPERS = Path(__file__).parent / "_helpers"
for _path in (_HELPERS, _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
