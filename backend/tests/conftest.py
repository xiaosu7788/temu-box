import os
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="temu-box-tests-"))
os.environ["TEMUBOX_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_DATA_DIR / 'test.db').as_posix()}"
