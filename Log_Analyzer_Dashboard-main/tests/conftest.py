import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def preserve_shipped_data():
    def snapshot():
        paths = [*ROOT.joinpath("logs").glob("*"), *ROOT.joinpath("reports").glob("*"), ROOT / "logs.db"]
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}
    before = snapshot()
    yield
    assert snapshot() == before, "Tests must not modify shipped logs, reports, or database"
