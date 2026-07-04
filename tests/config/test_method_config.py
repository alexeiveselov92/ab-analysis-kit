"""MethodConfig delegation tests — ONE hashing path, instantiation IS validation."""

from __future__ import annotations

import pytest

from abkit.config import MethodConfig
from abkit.stats import (
    MethodParamError,
    QuarantinedMethodError,
    UnknownMethodError,
    create_method,
)


class TestDelegation:
    def test_method_config_id_byte_identical_to_stats_core(self):
        params = {"test_type": "absolute", "calculate_mde": True, "power": 0.8}
        config = MethodConfig(name="z-test", params=params)
        direct = create_method("z-test", alpha=0.05, params=dict(params))
        assert config.method_config_id == direct.method_config_id

    def test_alpha_never_enters_the_id(self):
        config = MethodConfig(name="t-test", params={"test_type": "relative"})
        assert (
            config.bind(alpha=0.01).method_config_id
            == config.bind(alpha=0.2).method_config_id
            == config.method_config_id
        )

    def test_canonical_params_json_matches_instance(self):
        config = MethodConfig(name="t-test", params={"test_type": "relative"})
        from abkit.utils.json_utils import json_dumps_sorted

        assert config.canonical_params_json == json_dumps_sorted(config.bind().method_params)

    def test_bind_returns_working_method(self):
        config = MethodConfig(name="t-test", params={"test_type": "relative"})
        method = config.bind(alpha=0.05)
        assert method.alpha == 0.05


class TestValidationByInstantiation:
    def test_unknown_method_raises(self):
        with pytest.raises(UnknownMethodError):
            _ = MethodConfig(name="not-a-method").method_config_id

    def test_bad_param_raises(self):
        with pytest.raises(MethodParamError):
            MethodConfig(name="t-test", params={"bogus_param": 1}).bind()

    def test_quarantined_branch_fails_at_config_time(self):
        """paired-post-normed-bootstrap relative (its default) is quarantined —
        the config layer must surface this at validate/plan time."""
        with pytest.raises(QuarantinedMethodError):
            MethodConfig(name="paired-post-normed-bootstrap").bind()

    def test_seed_rejected_for_closed_form(self):
        with pytest.raises(MethodParamError):
            MethodConfig(name="z-test", params={"seed": 42}).bind()

    def test_seed_excluded_from_bootstrap_identity(self):
        base = MethodConfig(name="bootstrap", params={"n_samples": 200})
        seeded = MethodConfig(name="bootstrap", params={"n_samples": 200, "seed": 7})
        assert base.method_config_id == seeded.method_config_id

    def test_identity_param_changes_the_id(self):
        a = MethodConfig(name="bootstrap", params={"n_samples": 200})
        b = MethodConfig(name="bootstrap", params={"n_samples": 500})
        assert a.method_config_id != b.method_config_id
