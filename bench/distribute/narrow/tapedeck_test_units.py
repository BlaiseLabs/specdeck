"""Direct unit tests for three tapedeck functions that the suite only checks end to end.

Added to experiment copies only (DIST_NARROW=1). Rigged: written for the three planted
bugs. Expected values come from the original code. The first also covers the gap the
weak workers' fixes slipped through: a secret inside a plain string must be redacted.
"""
from tapedeck.engine.proxies import _is_stream
from tapedeck.engine.serialize import Redactor, snapshot
from tapedeck.gateway.services import diff_tapes


def test_snapshot_redacts_a_secret_inside_a_plain_string():
    assert snapshot("Bearer sk-live-abc123", Redactor(["sk-live-abc123"]), {}) == "Bearer [REDACTED]"


def test_snapshot_keeps_a_plain_string_unquoted():
    assert snapshot("hello", Redactor([]), {}) == "hello"


class _Paged:
    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration

    def __getitem__(self, key):
        return key


def test_is_stream_is_false_for_an_indexable_iterable():
    assert _is_stream(_Paged()) is False


def test_diff_tapes_finds_no_drift_between_identical_tapes():
    tape = {"mocked_modules": {"c": {"methods": {"m": {"return_value": {"id": 1}}}}}, "timeline": []}
    assert diff_tapes(tape, tape) == []
