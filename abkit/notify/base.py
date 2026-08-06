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

#: Signal kinds that are NOT a verdict — there is no effect, no CI, no p-value,
#: because nothing was measured (m12 NTF-2). A payload carrying one of these in
#: ``ReadoutData.kind`` renders as a notice: the sentence in ``notice``, the
#: brand's SRM token, and none of the statistics scaffolding.
NOTICE_KINDS: tuple[str, ...] = ("error", "calibration_red", "stale")


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
    #: Fork B (m13 STAT-1): under a read-time correction the family rule declined
    #: to reject this comparison while its own interval excludes zero. Rendered as
    #: one caveat line beside the numbers — without it the message shows a CI that
    #: excludes zero under a verdict that does not call it, with no explanation
    #: and nowhere to look one up.
    family_divergence: bool = False
    #: The decision layer (m14 DEC-4), for the verdict's OWN metric. ``leader``
    #: is the arm to ship — ``None`` when nobody beat the control, which is the
    #: common state — and ``separation`` says whether it is decisively ahead of
    #: the other treatments. Both are ``None`` on a two-arm experiment's message
    #: too, in the sense that ``arm_count`` gates the RENDERING: with one
    #: treatment "leader: treatment" restates the verdict word beside it.
    leader: str | None = None
    separation: str | None = None
    #: How many arms the experiment declares. The renderers' one gate for the
    #: DEC-4 line, so a two-arm message is `0.8.0`'s to the character.
    arm_count: int = 2
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
    #: The signal this payload carries (m12). ``"readout"`` — the default and
    #: the only shape that existed before NTF-2 — is a verdict with numbers
    #: behind it. A :data:`NOTICE_KINDS` value means nothing was measured, and
    #: every channel renders ``notice`` INSTEAD of the effect/CI/p-value block:
    #: a failed run showing "Effect: N/A · Flat" would be a statement about the
    #: experiment where the truth is that abkit never got to look at it.
    kind: str = "readout"
    #: The human sentence a notice carries (the pipeline error, the stale
    #: warning). Ignored for ``kind="readout"``.
    notice: str | None = None
    #: Does this readout's verdict WORD differ from the one last announced for
    #: the same comparison (m12 NTF-6)? Routing only — no channel renders it —
    #: and it rides on the payload for the reason ``kind`` does: the decision
    #: is made where the dedup state is read, and consumed deep inside
    #: ``signal_kinds_for``. False for a first-ever announcement and for a
    #: readout re-sent because its SRM gate moved, both of which are delivered
    #: without the verdict changing.
    verdict_changed: bool = False


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

    # The presentation of a NON-verdict (m12 NTF-2). The brand's five tokens are
    # VERDICT tokens (docs/design/brand-tokens.md) and a notice is not a verdict,
    # so no sixth hex is invented here: every notice reuses `--srm` #B23A6B, the
    # one token that already means "there is no trustworthy result — look at
    # this". The word and emoji carry the distinction; a designer adding a real
    # error token later changes this map and nothing else.
    # NTF-5 corrected `stale`'s word: what the signal actually reports is that
    # the SCHEDULE fell behind (detected while planning a run that then computes
    # the missing looks), so "Data is stale" — NTF-2's placeholder — would be
    # false about the warehouse by the time the message arrived.
    _NOTICE_PRESENTATION = {
        "error": ("Pipeline error", "\U0001f6d1"),  # stop sign
        "calibration_red": ("Calibration failed", "\U0001f9ea"),  # test tube
        "stale": ("Schedule fell behind", "\U000023f3"),  # hourglass
    }
    _NOTICE_COLOR = "#B23A6B"

    @abstractmethod
    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        """Deliver *readout* to this channel.

        Returns True on success, False on a (handled) delivery failure. Never
        raises on an ordinary network/SMTP error — the caller counts the bool.
        """

    # ---- status presentation ------------------------------------------------
    @staticmethod
    def is_notice(readout: ReadoutData) -> bool:
        """Does this payload carry a non-verdict signal (m12 NTF-2)?"""
        return readout.kind in NOTICE_KINDS

    @staticmethod
    def verdict_kind(readout: ReadoutData) -> str:
        """The presentation kind: ``SRM`` when the gate failed, else the verdict.

        A failed SRM withholds the result, so it wins over any WIN/LOSE/FLAT.
        A notice has no verdict at all and answers with its own kind — the
        fallback below must never claim FLAT ("no detectable effect") for a run
        that never produced an effect to detect.
        """
        if BaseChannel.is_notice(readout):
            return readout.kind
        if readout.srm_flag:
            return "SRM"
        v = (readout.verdict or "").upper()
        return v if v in BaseChannel._VERDICT_COLORS else "FLAT"

    def verdict_color(self, readout: ReadoutData) -> str:
        if self.is_notice(readout):
            return self._NOTICE_COLOR
        return self._VERDICT_COLORS[self.verdict_kind(readout)]

    def verdict_word(self, readout: ReadoutData) -> str:
        if self.is_notice(readout):
            return self._NOTICE_PRESENTATION[readout.kind][0]
        return self._VERDICT_WORDS[self.verdict_kind(readout)]

    def verdict_emoji(self, readout: ReadoutData) -> str:
        if self.is_notice(readout):
            return self._NOTICE_PRESENTATION[readout.kind][1]
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

        family_divergence_display = ""
        if readout.family_divergence:
            family_divergence_display = (
                "Note: the interval above excludes zero, but the read-time "
                "multiple-testing rule over this experiment's metric family does not "
                "reject — the interval is per-comparison, the verdict is family-wide"
            )
        family_divergence_line = (
            f"{family_divergence_display}\n" if family_divergence_display else ""
        )

        # m14 DEC-4, gated on the ARM COUNT: with one treatment "leader:
        # treatment" only restates the verdict word beside it, so a two-arm
        # message is `0.8.0`'s to the character. It says "ship X", never just
        # "X wins", because the whole point of the rollup is that it is a
        # decision over arms rather than one pair's result — and it names the
        # separation, since a leader nobody could separate from its rivals is a
        # weaker recommendation than one that is decisively ahead.
        rollup_display = ""
        if readout.arm_count > 2 and readout.kind == "readout":
            if readout.leader is not None:
                rollup_display = f"Leader on {readout.metric}: {readout.leader}"
                if readout.separation == "separated":
                    rollup_display += " — separated from every other arm"
                elif readout.separation == "co_leaders":
                    rollup_display += " — not separated from every other arm"
                elif readout.separation == "untested":
                    rollup_display += " — separation untested"
            elif readout.separation == "no_leader" and not readout.srm_flag:
                # deliberately silent under a failed SRM gate: the gate line
                # above already says the effects are untrustworthy, and "no arm
                # beat the control" beside it would report a measured finding
                # where nothing was measurable (the DEC-3 renderer lesson)
                rollup_display = f"No arm beat the control on {readout.metric} yet"
        rollup_line = f"{rollup_display}\n" if rollup_display else ""

        dashboard_url = readout.dashboard_url or ""
        dashboard_line = f"Report: {dashboard_url}\n" if dashboard_url else ""

        help_url = readout.help_url or ""
        from abkit.notify.branding import READOUT_GUIDE_LABEL

        help_line = f"{READOUT_GUIDE_LABEL}: {help_url}\n" if help_url else ""

        notice_display = readout.notice or ""
        notice_line = f"{notice_display}\n" if notice_display else ""
        # the collapsing form the notice template uses — a bare "Observed:" with
        # nothing after it is worse than no line
        timestamp_line = f"Observed: {ts_str}\n" if ts_str else ""

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
            "family_divergence_display": family_divergence_display,
            "family_divergence_line": family_divergence_line,
            "rollup_display": rollup_display,
            "rollup_line": rollup_line,
            "dashboard_url": dashboard_url,
            "dashboard_line": dashboard_line,
            "help_url": help_url,
            "help_line": help_line,
            "help_label": READOUT_GUIDE_LABEL,
            "project_name": project_name,
            "project_name_prefix": project_name_prefix,
            "mentions": mentions_str,
            "mentions_line": mentions_line,
            "kind": readout.kind,
            "notice": notice_display,
            "notice_line": notice_line,
            "timestamp_line": timestamp_line,
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
            "{family_divergence_line}"
            "{rollup_line}"
            "Observed: {timestamp}\n"
            "{dashboard_line}"
            "{help_line}"
            "{mentions_line}"
        )

    def get_notice_template(self) -> str:
        """The body for a non-verdict signal (m12 NTF-2).

        Deliberately NOT the readout template with blanks: every statistics
        placeholder is gone, because there is no effect, CI, p-value or arm
        pair to report — rendering them as ``N/A`` would suggest the numbers
        were looked for and not found.
        """
        return (
            "{verdict_emoji} {project_name_prefix}{experiment}: {verdict_word}\n"
            "{notice_line}"
            "{description_line}"
            "{timestamp_line}"
            "{dashboard_line}"
            "{mentions_line}"
        )

    def default_template_for(self, readout: ReadoutData) -> str:
        """The built-in template this payload renders with when none is given."""
        return (
            self.get_notice_template() if self.is_notice(readout) else self.get_default_template()
        )

    def format_message(self, readout: ReadoutData, template: str | None = None) -> str:
        """Render *template* (or this payload's built-in one) with the shared context.

        On a bad placeholder / format spec, falls back to the built-in template
        with an equality guard so it never recurses forever. The fallback is
        chosen by KIND, so a broken custom template on a pipeline error degrades
        to the notice body, never to a readout body full of ``N/A``.
        """
        if template is None:
            template = self.default_template_for(readout)
        ctx = self.build_context(readout)
        try:
            return template.format(**ctx)
        except (KeyError, ValueError, TypeError):
            fallback = self.default_template_for(readout)
            if template == fallback:
                raise
            return self.format_message(readout, fallback)

    def format_title(self, readout: ReadoutData) -> str:
        """Short one-line title for channels with a separate title field."""
        ctx = self.build_context(readout)
        if self.is_notice(readout):
            # no metric: a pipeline error belongs to the experiment, not to one
            # of its comparisons
            return (
                f"{ctx['verdict_emoji']} {ctx['project_name_prefix']}"
                f"{readout.experiment}: {ctx['verdict_word']}"
            )
        return (
            f"{ctx['verdict_emoji']} {ctx['project_name_prefix']}"
            f"{readout.experiment} · {readout.metric}: {ctx['verdict_word']}"
        )

    def send_notice(self, notice: ReadoutData) -> bool:
        """Deliver a non-verdict signal (m12 NTF-2).

        The default routes through :meth:`send`, so every channel supports
        notices the moment its transport works — the "only ``send`` is
        abstract" contract is unchanged. A channel whose rich rendering assumes
        a verdict (see ``email.py``'s HTML card) overrides its own renderer, not
        this method.

        The kind is read off ``notice.kind`` rather than taken as a parameter:
        ``verdict_color()`` is called deep inside each channel's payload
        builder, where no argument of this method could reach it, so a separate
        kind argument would be a second source of truth free to disagree with
        the payload.
        """
        if not self.is_notice(notice):
            raise ValueError(
                f"send_notice expects one of {NOTICE_KINDS}, got kind={notice.kind!r} "
                "— a verdict payload goes through send()"
            )
        return self.send(notice)

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
