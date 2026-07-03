"""Tests for the canonical JSON helpers (the ONE method_params serialisation path)."""

import math

import pytest

from abkit.utils.json_utils import json_dumps_sorted, json_loads


class TestJsonDumpsSorted:
    def test_sorted_keys(self):
        assert json_dumps_sorted({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_compact_separators(self):
        assert json_dumps_sorted({"a": [1, 2]}) == '{"a":[1,2]}'

    def test_unicode_not_escaped(self):
        assert json_dumps_sorted({"名": "é"}) == '{"名":"é"}'

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            json_dumps_sorted({"a": math.nan})

    def test_infinity_rejected(self):
        with pytest.raises(ValueError):
            json_dumps_sorted({"a": math.inf})

    def test_nested_sorted(self):
        assert json_dumps_sorted({"b": {"d": 1, "c": 2}, "a": 0}) == '{"a":0,"b":{"c":2,"d":1}}'


class TestJsonLoads:
    def test_str(self):
        assert json_loads('{"a": 1}') == {"a": 1}

    def test_bytes(self):
        assert json_loads(b'{"a": 1}') == {"a": 1}

    def test_bytearray_and_memoryview(self):
        assert json_loads(bytearray(b"[1, 2]")) == [1, 2]
        assert json_loads(memoryview(b"[1, 2]")) == [1, 2]

    def test_str_subclass_coerced(self):
        class MyStr(str):
            pass

        assert json_loads(MyStr('{"a": 1}')) == {"a": 1}

    def test_round_trip(self):
        obj = {"params": {"test_type": "relative", "n_samples": 1000}, "z": [1.5, None, True]}
        assert json_loads(json_dumps_sorted(obj)) == obj

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            json_loads("{not json")
