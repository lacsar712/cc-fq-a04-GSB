"""测试环境引导：在任何 app.* 导入前把数据库切到临时 SQLite。"""

import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(prefix="qc-test-", suffix=".db")
os.close(_db_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")
os.environ.setdefault("JWT_SECRET", "test-secret")
