import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `import api`, `import engine`, etc. work.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Optional: default asyncio fixture loop scope to "function"
def pytest_configure(config):
    config._inicache.setdefault("asyncio_default_fixture_loop_scope", "function")
