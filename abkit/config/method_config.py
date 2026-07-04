"""Method configuration — the tunable, hashed statistical object.

Plan R4 (m2-implementation-plan.md): this model deliberately does NOT
re-implement hashing or a parallel param schema. It stores only
``{name, params}``; validation, the quarantine policy, and the canonical
``method_config_id`` all come from ONE instantiation through the
``abkit.stats`` factory (declarative-config.md §7: one canonical spec).
Instantiation IS validation — a quarantined branch or a bad param fails at
config-validate/plan time, never at run time.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from abkit.stats import BaseMethod, create_method


class MethodConfig(BaseModel):
    """One comparison's statistical method: registry name + params.

    ``alpha`` is deliberately NOT here — it is an experiment-level setting
    (post-correction effective alpha is injected at bind time) and never
    enters ``method_config_id``.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Registry name (e.g. 'z-test', 'cuped-t-test')")
    params: dict[str, Any] = Field(default_factory=dict, description="Method params")

    _bound_probe: BaseMethod | None = PrivateAttr(default=None)

    def bind(self, alpha: float = 0.05) -> BaseMethod:
        """Instantiate the method via the stats factory (validates params).

        Raises the stats-core exceptions unchanged (``UnknownMethodError``,
        ``MethodParamError``, ``QuarantinedMethodError``) — the validator
        surfaces them as config errors.

        Closed-form instances are reusable across cutoffs; bootstrap methods
        are re-bound per row by the analyze stage with the deterministic
        derived seed injected into a params copy (``seed`` is
        identity-excluded, so the id below is unaffected).
        """
        return create_method(self.name, alpha=alpha, params=dict(self.params))

    @property
    def method_config_id(self) -> str:
        """The canonical identity hash, read off a probe instance (cached).

        Byte-identical to ``BaseMethod.method_config_id`` by construction —
        there is no second hashing path.
        """
        if self._bound_probe is None:
            # alpha does not enter the id; any value produces the same hash
            self._bound_probe = self.bind(alpha=0.05)
        return self._bound_probe.method_config_id

    @property
    def covariate_lookback(self) -> str | int | None:
        """The CUPED pre-period lookback param (pipeline-consumed; see the
        stats-core ``COVARIATE_LOOKBACK_PARAM`` — identity-bearing there)."""
        return self.params.get("covariate_lookback")

    @property
    def canonical_params_json(self) -> str:
        """The canonical ``method_params`` JSON string persisted to ``_ab_results``.

        Read off the probe instance so config-time and enrich-time
        serialisations can never disagree (quorum: "canonical method_params
        JSON everywhere").
        """
        if self._bound_probe is None:
            self._bound_probe = self.bind(alpha=0.05)
        from abkit.utils.json_utils import json_dumps_sorted

        return json_dumps_sorted(self._bound_probe.method_params)
