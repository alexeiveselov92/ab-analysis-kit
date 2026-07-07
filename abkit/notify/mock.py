"""A synthetic :class:`ReadoutData` for the ``abk test-report`` smoke test.

Purely fabricated (no lock, no warehouse read — the spec says "mock readout") so
the command exercises channel connectivity + message formatting on a fresh
machine. Values depict a clean WIN with a CI that excludes zero.
"""

from __future__ import annotations

from abkit.notify.base import ReadoutData
from abkit.notify.branding import READOUT_GUIDE_URL
from abkit.utils import now_utc_naive


def create_mock_readout(
    experiment: str,
    metric: str,
    name_1: str = "control",
    name_2: str = "treatment",
    *,
    alpha: float = 0.05,
    project_name: str | None = None,
    description: str | None = None,
    help_url: str | None = READOUT_GUIDE_URL,
) -> ReadoutData:
    return ReadoutData(
        experiment=experiment,
        metric=metric,
        verdict="WIN",
        name_1=name_1,
        name_2=name_2,
        effect=0.0432,
        left_bound=0.0118,
        right_bound=0.0741,
        pvalue=0.0071,
        alpha=alpha,
        relative=True,
        srm_flag=False,
        weekly_cycle_pct=None,
        n_1=12480,
        n_2=12515,
        timestamp=now_utc_naive(),
        timezone="UTC",
        elapsed_days=14.0,
        project_name=project_name,
        description=description or "Mock readout — an abk test-report connectivity / format check.",
        help_url=help_url,
    )
