"""Notification state mixin: ``_ab_notify_states`` operations (m12 NTF-3).

Owns the row shape for "what this comparison last ANNOUNCED"; the decision of
whether an announcement is due lives in ``abkit/notify/cooldown.py``, which
touches no database. Splitting them keeps the rule unit-testable without a
warehouse and keeps this file a plain accessor, the ``_ab_tasks`` /
``_ab_experiments`` discipline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_NOTIFY_STATES
from abkit.utils.datetime_utils import now_utc_naive, to_naive_utc

#: The FULL comparison identity (§0.4 point 2). ``method_config_id`` is in the
#: key on purpose: a re-tuned comparison is a different measurement, so it
#: starts a fresh announcement history instead of inheriting the old one's.
NOTIFY_STATE_KEY = ("experiment", "metric", "name_1", "name_2", "method_config_id")


def notice_state_key(kind: str) -> tuple[str, str, str, str]:
    """The ``(metric, name_1, name_2, method_config_id)`` a NOTICE's row uses.

    A recurring notice (m12 NTF-5) is a property of the whole experiment, not
    of one comparison, so it needs a key in the same table that no comparison
    can ever collide with. The sentinel-metric name is this repo's own idiom
    (``_ab_aa_runs`` stores the composed family sweep under
    ``metric='__family__'``) but it is NOT what makes the key safe —
    ``MetricConfig`` accepts underscores, so a metric may legitimately be
    called ``__stale__``. What separates the rows is the EMPTY arm pair: every
    comparison row carries two declared variant names, and a variant name
    cannot be empty.

    Compose it ONLY here: the two dispatchers and every test must agree on the
    row a signal reads and writes, and a hand-copied tuple is how the m9 state
    identity went wrong.
    """
    return (f"__{kind}__", "", "", "")


class _NotifyStatesMixin(_InternalTablesBase):
    def notify_states_table_exists(self) -> bool:
        """True when ``_ab_notify_states`` exists.

        A project that predates m12 (or a `--notify` run whose pipeline never
        reached ``ensure_tables``) has none. Callers treat that as "no state
        yet" and SEND — the dedup may never be the reason a message is
        withheld.
        """
        return self._manager.table_exists(
            TABLE_NOTIFY_STATES, schema=self._manager.internal_location
        )

    def get_notify_state(
        self,
        experiment: str,
        metric: str,
        name_1: str,
        name_2: str,
        method_config_id: str,
    ) -> dict[str, Any]:
        """The last announcement for one comparison; a never-seen key answers
        with the empty state (never ``None``, so callers cannot forget a
        branch)."""
        empty: dict[str, Any] = {
            "last_verdict": None,
            "last_srm_flag": False,
            # m14 DEC-4. NULL on a pre-0.9.0 row and on a fresh key alike, so a
            # readout carrying no rollup compares EQUAL to both and upgrading
            # does not re-announce every comparison in the project.
            "last_rollup": None,
            "last_notified_at": None,
            "notify_count": 0,
        }
        if not self.notify_states_table_exists():
            return empty
        full_table_name = self._manager.get_full_table_name(TABLE_NOTIFY_STATES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT * FROM {full_table_name} WHERE experiment = %(e)s AND metric = %(m)s "
            "AND name_1 = %(n1)s AND name_2 = %(n2)s AND method_config_id = %(mid)s",
            {
                "e": experiment,
                "m": metric,
                "n1": name_1,
                "n2": name_2,
                "mid": method_config_id,
            },
        )
        if not rows:
            return empty
        # ClickHouse keeps pre-merge duplicates: the freshest row wins, mirroring
        # every other ReplacingMergeTree reader in this package.
        row = max(rows, key=lambda r: to_naive_utc(r.get("updated_at")) or datetime.min)
        return {
            "last_verdict": row.get("last_verdict"),
            "last_srm_flag": bool(row.get("last_srm_flag")),
            "last_rollup": row.get("last_rollup"),
            "last_notified_at": to_naive_utc(row.get("last_notified_at")),
            "notify_count": int(row.get("notify_count") or 0),
        }

    def record_notification(
        self,
        experiment: str,
        metric: str,
        name_1: str,
        name_2: str,
        method_config_id: str,
        *,
        verdict: str | None,
        srm_flag: bool,
        rollup: str | None = None,
        notified_at: datetime | None = None,
    ) -> None:
        """Stamp what was just announced, incrementing the count.

        Called ONLY after a channel accepted the message — see
        ``notify/dispatch.py``. Recording an announcement nobody received would
        make the next run treat the flip as old news and lose it permanently.
        """
        previous = self.get_notify_state(experiment, metric, name_1, name_2, method_config_id)
        now = now_utc_naive()
        record = {
            "experiment": experiment,
            "metric": metric,
            "name_1": name_1,
            "name_2": name_2,
            "method_config_id": method_config_id,
            "last_verdict": verdict,
            "last_srm_flag": bool(srm_flag),
            "last_rollup": rollup,
            "last_notified_at": notified_at if notified_at is not None else now,
            "notify_count": int(previous["notify_count"]) + 1,
            "updated_at": now,
        }
        full_table_name = self._manager.get_full_table_name(TABLE_NOTIFY_STATES, use_internal=True)
        data = {col: np.array([value], dtype=object) for col, value in record.items()}
        # sync=True, the `_ab_experiments` precedent: an async ClickHouse delete
        # would leave a transient duplicate that the next run's read could pick
        # as freshest, and this row decides whether a message is sent at all.
        self._manager.upsert_record(
            full_table_name,
            {col: record[col] for col in NOTIFY_STATE_KEY},
            data,
            sync=True,
        )
