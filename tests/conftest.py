import json
import pathlib

import pytest

from calendar_sharer.excluded import EXCLUDED
from calendar_sharer.merge import Source

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def index():
    path = FIXTURES / "index.json"
    if not path.is_file():
        pytest.skip("run scripts/fetch_fixtures.py first")
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def sources(index):
    return [
        Source(id=c["id"], name=c["name"], ics=(FIXTURES / f"{c['id']}.ics").read_text())
        for c in index
        if c["id"] not in EXCLUDED
    ]


@pytest.fixture(scope="session")
def merged(sources):
    from calendar_sharer.merge import merge

    return merge(sources, cal_name="Test")
