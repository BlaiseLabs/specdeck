"""pytest plugin: which package functions each test function calls directly (depth 1).

    SWARM_PKG=<abs package dir> SWARM_OUT=<json> pytest -p direct_plugin
"""
import json
import os
import sys

import pytest

PKG = os.environ.get("SWARM_PKG", "")
OUT = os.environ.get("SWARM_OUT", "direct.json")
SEEN = {}   # "file:firstline:name" -> [nodeid, ...]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    obj = getattr(item, "obj", None)
    test_code = getattr(getattr(obj, "__func__", obj), "__code__", None)

    def prof(frame, event, arg):
        if event == "call" and frame.f_back is not None and frame.f_back.f_code is test_code:
            c = frame.f_code
            if c.co_filename.startswith(PKG):
                SEEN.setdefault(f"{c.co_filename}:{c.co_firstlineno}:{c.co_name}", []).append(item.nodeid)
    old = sys.getprofile()
    sys.setprofile(prof)
    try:
        yield
    finally:
        sys.setprofile(old)


def pytest_sessionfinish(session):
    with open(OUT, "w") as fh:
        json.dump(SEEN, fh, indent=1)
