from __future__ import annotations

import os
import sys
from pathlib import Path

from a2wsgi import ASGIMiddleware


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"

sys.path.insert(0, str(BACKEND))

os.environ.setdefault("FRONTEND_DIST_DIR", str(ROOT / "dist"))
os.environ.setdefault("SUPPORT_COUNTER_DB", str(BACKEND / "agent_support_counter.db"))

from server import app as fastapi_app  # noqa: E402


application = ASGIMiddleware(fastapi_app)
