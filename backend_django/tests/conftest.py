from __future__ import annotations

import os
import sys
from pathlib import Path

import django
from django.core.management import call_command


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "app"))

os.environ.setdefault("TRACK_ANYWHERE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend_django.config.settings")

django.setup()
call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)
