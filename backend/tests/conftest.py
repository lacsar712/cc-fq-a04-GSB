"""Pytest bootstrap: point the app at SQLite before any app module is imported.

app.database creates its engine at import time from settings.database_url,
so DATABASE_URL must be set here (conftest is imported before test modules),
not inside individual test files.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
