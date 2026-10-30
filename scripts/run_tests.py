"""Run every Python test with per-test timings and immediate CI diagnostics."""
import faulthandler
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TimedResult(unittest.TextTestResult):
    def startTest(self, test):
        faulthandler.dump_traceback_later(180)
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        self.stream.writeln('  %.3fs %s' % (time.monotonic() - self.started, test.id()))
        super().stopTest(test)

    def addError(self, test, err):
        super().addError(test, err)
        self.stream.writeln(self._exc_info_to_string(err, test))
        for field in ('stdout', 'stderr'):
            output = getattr(err[1], field, None)
            if output:
                self.stream.writeln(str(output))
        root = getattr(test, 'root', None)
        if root:
            log = root / 'logs/service-output.log'
            if log.exists():
                self.stream.writeln(log.read_text(errors='replace')[-8000:])


if __name__ == '__main__':
    # Reset for each test; a stack dump now means one test stalled, rather than
    # an alarming "Timeout" every 90 seconds across an otherwise healthy suite.
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2, failfast=True, resultclass=TimedResult).run(suite)
    sys.exit(not result.wasSuccessful())
