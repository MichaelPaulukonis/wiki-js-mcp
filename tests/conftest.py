import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_TEST_DB = _REPO_ROOT / "tests" / "_test_wikijs_mappings.db"
_TEST_LOG = _REPO_ROOT / "tests" / "_test.log"

# Remove artifacts from previous runs: the FileMapping table has a unique
# constraint on file_path, so a stale DB makes reruns fail whenever pytest's
# tmp_path repeats (fresh /tmp with a cached workspace, --basetemp, etc.).
_TEST_DB.unlink(missing_ok=True)
_TEST_LOG.unlink(missing_ok=True)

os.environ["WIKIJS_API_URL"] = "http://test.invalid"
os.environ["WIKIJS_TOKEN"] = "test-token"
os.environ["WIKIJS_MCP_DB"] = str(_TEST_DB)
os.environ["LOG_FILE"] = str(_TEST_LOG)
os.environ["LOG_LEVEL"] = "ERROR"

sys.path.insert(0, str(_REPO_ROOT / "src"))
