"""Regression: the CLI must not crash printing symbols when stdout is a cp1252 stream
(redirected output on Windows: Task Scheduler, CI, `> log.txt`)."""
import io
import sys
import unittest
from unittest import mock

from hedgefund.main import _ensure_utf8_output


class CliEncodingTests(unittest.TestCase):
    def test_ensure_utf8_output_survives_cp1252_streams(self):
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            _ensure_utf8_output()
            print("✓ healthy ❌ broken")     # would raise UnicodeEncodeError on cp1252
            sys.stdout.flush()
        self.assertTrue(out.buffer.getvalue().decode("utf-8").startswith("✓ healthy"))


if __name__ == "__main__":
    unittest.main()
