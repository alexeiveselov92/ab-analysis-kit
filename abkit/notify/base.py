"""The channel contract for ``abk test-report`` notifications.

Two pieces, both experiment-primary (abkit has NO alerting — there is no
anomaly/recovery/no-data/error "kind", no severity, no detector quorum, no
consecutive-firing / cooldown machinery; a notification is one *readout*, a
decision snapshot):

* :class:`ReadoutData` — the flat, display-oriented payload a channel sends. It
  mirrors the readout contract (``docs/specs/data-contract-and-reporting.md §5.3``):
  a verdict (WIN/LOSE/FLAT/INCONCLUSIVE), the effect + confidence interval, the
  p-value, the EFFECTIVE post-correction per-comparison alpha, the SRM gate, and
  the weekly-cycle representativeness — plus channel-display fields (timezone,
  project name, mentions, links).
* :class:`BaseChannel` — an ABC whose only abstract method is :meth:`send`.
  :meth:`build_context` is the single source of every display string (shared by
  the webhook attachment, the Telegram HTML body and the email card, so all read
  the same); :meth:`format_message` renders the default (or a caller) template
  with a fallback-on-error guard.

Status presentation keys off the five brand verdict tokens
(``docs/design/brand-tokens.md``) — never a hardcoded ad-hoc hex.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

VerdictKind = str  # "WIN" | "LOSE" | "FLAT" | "INCONCLUSIVE"
VERDICT_KINDS: tuple[str, ...] = ("WIN", "LOSE", "FLAT", "INCONCLUSIVE")


@dataclass
class ReadoutData:
    """One experiment-readout notification payload (the channel-facing message).

    ``name_1`` is the control arm, ``name_2`` the treatment (readout convention).
    ``effect`` / ``left_bound`` / ``right_bound`` are the point effect and its CI,
    expressed as a relative fraction when ``relative`` is True (rendered ``%``) or
    in the metric's absolute units otherwise. ``alpha`` is the EFFECTIVE
    post-correction per-comparison alpha (never re-derive corrections here).
    """

    experiment: str
    metric: str
    verdict: VerdictKind
    name_1: str
    name_2: str
    effect: float | None = None
    left_bound: float | None = None
    right_bound: float | None = None
    pvalue: float | None = None
    alpha: float | None = None
    relative: bool = True
    srm_flag: bool = False
    srm_pvalue: float | None = None
    weekly_cycle_pct: float | None = None
    n_1: int | None = None
    n_2: int | None = None
    timestamp: datetime | None = None
    timezone: str = "UTC"
    elapsed_days: float | None = None
    project_name: str | None = None
    description: str | None = None
    mentions: list[str] = field(default_factory=list)
    dashboard_url: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    help_url: str | None = None


def _finite(value: Any) -> bool:
    """A usable number: not None, and (if float) neither NaN nor ±inf."""
    if value is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return True


class BaseChannel(ABC):
    """Abstract notification channel. Subclasses implement only :meth:`send`."""

    # The five brand verdict tokens (docs/design/brand-tokens.md). SRM is the loud
    # sample-ratio gate: results withheld — it overrides any verdict.
    _VERDICT_COLORS = {
        "WIN": "#1E9E6A",
        "LOSE": "#D6453D",
        "FLAT": "#7A8595",
        "INCONCLUSIVE": "#E0A23B",
        "SRM": "#B23A6B",
    }
    _VERDICT_WORDS = {
        "WIN": "Win",
        "LOSE": "Lose",
        "FLAT": "Flat",
        "INCONCLUSIVE": "Inconclusive",
        "SRM": "SRM gate failed",
    }
    _VERDICT_EMOJI = {
        "WIN": "\U0001f7e2",  # green circle
        "LOSE": "\U0001f534",  # red circle
        "FLAT": "\U000026aa",  # white circle
        "INCONCLUSIVE": "\U0001f7e1",  # yellow circle
        "SRM": "\U0001f7e3",  # purple circle
    }

    @abstractmethod
    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        """Deliver *readout* to this channel.

        Returns True on success, False on a (handled) delivery failure. Never
        raises on an ordinary network/SMTP error — the caller counts the bool.
        """

    # ---- status presentation ------------------------------------------------
    @staticmethod
    def verdict_kind(readout: ReadoutData) -> str:
        """The presentation kind: ``SRM`` when the gate failed, else the verdict.

        A failed SRM withholds the result, so it wins over any WIN/LOSE/FLAT.
        """
        if readout.srm_flag:
            return "SRM"
        v = (readout.verdict or "").upper()
        return v if v in BaseChannel._VERDICT_COLORS else "FLAT"

    def verdict_color(self, readout: ReadoutData) -> str:
        return self._VERDICT_COLORS[self.verdict_kind(readout)]

    def verdict_word(self, readout: ReadoutData) -> str:
        return self._VERDICT_WORDS[self.verdict_kind(readout)]

    def verdict_emoji(self, readout: ReadoutData) -> str:
        return self._VERDICT_EMOJI[self.verdict_kind(readout)]

    # ---- shared display context --------------------------------------------
    def build_context(self, readout: ReadoutData) -> dict[str, Any]:
        """Every display string, computed once (no escaping — each channel escapes
        its own). The ``*_line`` values carry a trailing newline and collapse to
        ``""`` when absent, so the default template renders cleanly either way.
        """
        rel = readout.relative
        effect_display = _fmt_signed(readout.effect, rel)
        ci_display = _fmt_interval(readout.left_bound, readout.right_bound, rel)
        pvalue_display = _fmt_plain(readout.pvalue)
        alpha_display = _fmt_plain(readout.alpha)
        ci_label = f"{(1.0 - readout.alpha) * 100:.0f}% CI" if _finite(readout.alpha) else "CI"

        ts_str = _fmt_ts(readout.timestamp, readout.timezone)

        description_line = f"{readout.description}\n" if readout.description else ""

        samples_display = ""
        if readout.n_1 is not None and readout.n_2 is not None:
            samples_display = (
                f"{readout.name_1} n={readout.n_1:,} · {readout.name_2} n={readout.n_2:,}"
            )
        samples_line = f"{samples_display}\n" if samples_display else ""

        srm_display = ""
        if readout.srm_flag:
            p = f" (p={readout.srm_pvalue:.4g})" if _finite(readout.srm_pvalue) else ""
            srm_display = f"⚠ SRM gate FAILED{p} — sample split is off, results withheld"
        srm_line = f"{srm_display}\n" if srm_display else ""

        weekly_cycle_display = ""
        if _finite(readout.weekly_cycle_pct):
            weekly_cycle_display = (
                f"Representativeness: only {readout.weekly_cycle_pct:.0f}% of a weekly "
                "cycle elapsed — weekly seasonality may not be captured"
            )
        weekly_cycle_line = f"{weekly_cycle_display}\n" if weekly_cycle_display else ""

        dashboard_url = readout.dashboard_url or ""
        dashboard_line = f"Report: {dashboard_url}\n" if dashboard_url else ""

        help_url = readout.help_url or ""
        from abkit.notify.branding import READOUT_GUIDE_LABEL

        help_line = f"{READOUT_GUIDE_LABEL}: {help_url}\n" if help_url else ""

        mentions_str = self.format_mentions(readout.mentions)
        mentions_line = f"\n{mentions_str}" if mentions_str else ""

        project_name = readout.project_name or ""
        project_name_prefix = f"[{project_name}] " if project_name else ""

        return {
            "experiment": readout.experiment,
            "metric": readout.metric,
            "name_1": readout.name_1,
            "name_2": readout.name_2,
            "verdict": self.verdict_kind(readout),
            "verdict_word": self.verdict_word(readout),
            "verdict_emoji": self.verdict_emoji(readout),
            "verdict_color": self.verdict_color(readout),
            "effect_display": effect_display,
            "ci_display": ci_display,
            "ci_label": ci_label,
            "pvalue_display": pvalue_display,
            "alpha_display": alpha_display,
            "timestamp": ts_str,
            "timezone": readout.timezone,
            "description": readout.description or "",
            "description_line": description_line,
            "samples_display": samples_display,
            "samples_line": samples_line,
            "srm_display": srm_display,
            "srm_line": srm_line,
            "weekly_cycle_display": weekly_cycle_display,
            "weekly_cycle_line": weekly_cycle_line,
            "dashboard_url": dashboard_url,
            "dashboard_line": dashboard_line,
            "help_url": help_url,
            "help_line": help_line,
            "help_label": READOUT_GUIDE_LABEL,
            "project_name": project_name,
            "project_name_prefix": project_name_prefix,
            "mentions": mentions_str,
            "mentions_line": mentions_line,
        }

    def get_default_template(self) -> str:
        """The default plain-text readout body (one message kind, no alert kinds)."""
        return (
            "{verdict_emoji} {project_name_prefix}{experiment} · {metric}: {verdict_word}\n"
            "{description_line}"
            "Effect: {effect_display}  ·  {ci_label} {ci_display}\n"
            "p = {pvalue_display}  ·  α = {alpha_display}  ·  {name_1} vs {name_2}\n"
            "{samples_line}"
            "{srm_line}"
            "{weekly_cycle_line}"
            "Observed: {timestamp}\n"
            "{dashboard_line}"
            "{help_line}"
            "{mentions_line}"
        )

    def format_message(self, readout: ReadoutData, template: str | None = None) -> str:
        """Render *template* (or the default) with the shared context.

        On a bad placeholder / format spec, falls back to the default template
        with an equality guard so it never recurses forever.
        """
        if template is None:
            template = self.get_default_template()
        ctx = self.build_context(readout)
        try:
            return template.format(**ctx)
        except (KeyError, ValueError, TypeError):
            fallback = self.get_default_template()
            if template == fallback:
                raise
            return self.format_message(readout, fallback)

    def format_title(self, readout: ReadoutData) -> str:
        """Short one-line title for channels with a separate title field."""
        ctx = self.build_context(readout)
        return (
            f"{ctx['verdict_emoji']} {ctx['project_name_prefix']}"
            f"{readout.experiment} · {readout.metric}: {ctx['verdict_word']}"
        )

    def format_mentions(self, mentions: list[str]) -> str:
        """Default: ``@name`` space-joined. Channels override for native syntax."""
        if not mentions:
            return ""
        return " ".join(f"@{m}" for m in mentions)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ---- formatting helpers -----------------------------------------------------
def _fmt_signed(value: float | None, relative: bool) -> str:
    """A point effect with an explicit sign; ``%`` when relative."""
    if not _finite(value):
        return "N/A"
    assert value is not None
    return f"{value * 100:+.2f}%" if relative else f"{value:+.4g}"


def _fmt_bound(value: float | None, relative: bool) -> str:
    """A CI bound (no forced sign — a bound may legitimately be negative)."""
    if not _finite(value):
        return "N/A"
    assert value is not None
    return f"{value * 100:.2f}%" if relative else f"{value:.4g}"


def _fmt_interval(lo: float | None, hi: float | None, relative: bool) -> str:
    if not _finite(lo) or not _finite(hi):
        return "N/A"
    return f"[{_fmt_bound(lo, relative)}, {_fmt_bound(hi, relative)}]"


def _fmt_plain(value: float | None) -> str:
    if not _finite(value):
        return "N/A"
    assert value is not None
    return f"{value:.4g}"


def redact_url(url: str) -> str:
    """Scheme + host only — drop the path/query where a webhook/token secret lives.

    A Slack/Mattermost incoming-webhook URL and the Telegram Bot API URL carry
    the credential in the PATH, and ``requests`` embeds the full URL in its
    exception strings. Channels must log this redacted form, never the raw
    exception, so a delivery failure can't leak a live credential to stdout/CI.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(url)"
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return parts.netloc or "(url)"


def describe_error(exc: BaseException) -> str:
    """A secret-free one-liner for a delivery failure: HTTP status if present,
    else the exception class name (never the raw message — it may embed the URL)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        return f"HTTP {status}"
    return type(exc).__name__


def _fmt_ts(value: datetime | None, tz: str) -> str:
    """Format a naive-UTC datetime in *tz* with a ``(tz)`` suffix."""
    if value is None:
        return ""
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    label = tz or "UTC"
    try:
        shown = value.astimezone(ZoneInfo(label))
    except Exception:
        shown = value.astimezone(timezone.utc)
        label = "UTC"
    return f"{shown.strftime('%Y-%m-%d %H:%M:%S')} ({label})"
