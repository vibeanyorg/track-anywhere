from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend" / "app"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend_django.config.settings")

from django.core.asgi import get_asgi_application


application = get_asgi_application()
