import faulthandler

faulthandler.dump_traceback_later(5)
import pytest

pytest.main(["--collect-only", "tests/unit"])
