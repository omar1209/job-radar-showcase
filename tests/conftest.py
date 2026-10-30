import os
import sys

import pytest
import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def cfg():
    with open(os.path.join(ROOT, "config", "search.yaml")) as f:
        return yaml.safe_load(f)
