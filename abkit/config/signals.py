"""The notification signal kinds — the one vocabulary two config models share.

A leaf module on purpose: ``profile.py`` (per-channel ``on:``) and
``experiment_config.py`` (per-experiment ``notify.on:``) both need the literal,
and neither may import the other's dependency tree to get it.

Only ``readout`` fires in NTF-1. The rest are declared from the start so an
``on:`` filter written today keeps its meaning as NTF-2..NTF-5 wire the
remaining signals — a filter that silently widens when a new kind ships would
deliver messages nobody asked for (m12-implementation-plan.md §1).
"""

from __future__ import annotations

from typing import Literal, get_args

SignalKind = Literal["readout", "verdict_change", "srm", "calibration_red", "stale", "error"]

#: Every declared kind, for validation messages and test rosters.
SIGNAL_KINDS: tuple[str, ...] = get_args(SignalKind)
