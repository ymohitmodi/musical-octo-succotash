"""DB connection lifecycle: close() must release the file handle (Windows) and be reusable."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hedgefund.storage.db import DB


class DBLifecycleTests(unittest.TestCase):
    def test_close_is_idempotent_and_allows_reopen(self):
        with TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "fund.db")
            db.log_event("probe", {"n": 1})
            db.close()
            db.close()                                   # second close is a no-op
            self.assertEqual(len(db.query("SELECT * FROM events")), 1)  # lazily reopens
            db.close()

    def test_database_file_can_be_deleted_after_close(self):
        # On Windows this raises PermissionError if a handle is still open.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "fund.db"
            db = DB(path)
            db.log_event("probe", {})
            db.close()
            os.remove(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
