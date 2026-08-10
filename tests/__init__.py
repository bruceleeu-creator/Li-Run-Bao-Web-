"""测试进程的本地持久化隔离。"""

import os
import tempfile
from pathlib import Path


_TEST_STATE_HOLDER = tempfile.TemporaryDirectory(prefix="lirunbao-tests-")
_TEST_STATE = Path(_TEST_STATE_HOLDER.name)
os.environ["LIRUNBAO_DB_PATH"] = str(_TEST_STATE / "app.db")
os.environ["LIRUNBAO_AI_CONFIG_PATH"] = str(_TEST_STATE / ".ai_config.json")
os.environ["LIRUNBAO_WORKSPACE_PATH"] = str(_TEST_STATE / "workspaces")
