"""SMTP email channel.

Sends a ``multipart/alternative`` message: a plain-text body (the default
readout template) plus a branded, table-based, inline-CSS HTML card (email
clients ignore CSS custom properties, so the brand hexes are inlined from
``docs/design/brand-tokens.md``). ``use_tls=True`` = STARTTLS (port 587);
``use_tls=False`` = implicit TLS via ``SMTP_SSL`` (port 465) — NOT plaintext.
"""

from __future__ import annotations

import html
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData
from abkit.notify.branding import BRAND_USERNAME

# Brand tokens inlined (email clients drop CSS custom properties).
_IRIS = "#6A45C4"
_INK = "#1B1916"
_PAPER = "#F5F1E8"
_SURFACE = "#FBF9F3"
_BORDER = "#E6E0D4"
_MUTED = "#6E675B"
_SANS = "'Schibsted Grotesk', -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"


class EmailChannel(BaseChannel):
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        from_email: str,
        to_emails: list[str] | str,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        use_tls: bool = True,
        subject_template: str = "{verdict_emoji} {project_name_prefix}{experiment} · {metric}: {verdict_word}",
        from_name: str = BRAND_USERNAME,
        template: str | None = None,
    ) -> None:
        if not smtp_host:
            raise ValueError("smtp_host is required")
        if not smtp_port:
            raise ValueError("smtp_port is required")
        if not from_email:
            raise ValueError("from_email is required")
        recipients = (
            [e.strip() for e in to_emails.split(",")]
            if isinstance(to_emails, str)
            else list(to_emails)
        )
        recipients = [e for e in recipients if e]
        if not recipients:
            raise ValueError("to_emails is required")
        self.smtp_host = smtp_host
        self.smtp_port = int(smtp_port)
        self.from_email = from_email
        self.to_emails = recipients
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.use_tls = use_tls
        self.subject_template = subject_template
        self.from_name = from_name
        self.template = template

    def format_mentions(self, mentions: list[str]) -> str:
        """Email has no mention syntax — surface real handles as a CC line."""
        real = [m for m in mentions if m.lower() not in ("channel", "all", "here", "everyone")]
        return f"CC: {', '.join(real)}" if real else ""

    def _subject(self, readout: ReadoutData) -> str:
        ctx = self.build_context(readout)
        try:
            subject = self.subject_template.format(**ctx)
        except (KeyError, ValueError, TypeError):
            subject = f"{ctx['verdict_emoji']} {readout.experiment} · {readout.metric}"
        # Header-injection guard: no CR/LF in a header value.
        return subject.replace("\r", " ").replace("\n", " ")

    def _build_html_body(self, readout: ReadoutData) -> str:
        ctx = self.build_context(readout)
        accent = self.verdict_color(readout)

        def esc(value: Any) -> str:
            return html.escape(str(value))

        rows = [
            (
                "Effect",
                f"{esc(ctx['effect_display'])} &nbsp; {esc(ctx['ci_label'])} {esc(ctx['ci_display'])}",
            ),
            (
                "p-value",
                f"{esc(ctx['pvalue_display'])} &nbsp;·&nbsp; α {esc(ctx['alpha_display'])}",
            ),
            ("Arms", f"{esc(readout.name_1)} vs {esc(readout.name_2)}"),
        ]
        if ctx["samples_display"]:
            rows.append(("Samples", esc(ctx["samples_display"])))
        if ctx["weekly_cycle_display"]:
            rows.append(("Note", esc(ctx["weekly_cycle_display"])))
        row_html = "".join(
            f'<tr><td style="padding:4px 12px 4px 0;color:{_MUTED};font-size:13px;">{label}</td>'
            f'<td style="padding:4px 0;color:{_INK};font-size:14px;font-weight:600;">{value}</td></tr>'
            for label, value in rows
        )

        srm_html = ""
        if ctx["srm_display"]:
            srm_html = (
                f'<p style="margin:12px 0 0;padding:10px 12px;border-radius:6px;'
                f'background:#F7E9EF;color:#B23A6B;font-size:13px;font-weight:600;">'
                f'{esc(ctx["srm_display"])}</p>'
            )

        links = []
        if readout.dashboard_url:
            links.append((readout.dashboard_url, "Open report"))
        if readout.help_url:
            links.append((readout.help_url, ctx["help_label"]))
        link_html = ""
        if links:
            btns = " ".join(
                f'<a href="{html.escape(url, quote=True)}" '
                f'style="color:{_IRIS};text-decoration:none;font-weight:600;">{esc(label)} →</a>'
                for url, label in links
            )
            link_html = f'<p style="margin:16px 0 0;font-size:14px;">{btns}</p>'

        desc_html = (
            f'<p style="margin:0 0 12px;color:{_MUTED};font-size:14px;">{esc(readout.description)}</p>'
            if readout.description
            else ""
        )

        return f"""\
<!DOCTYPE html>
<html><body style="margin:0;background:{_PAPER};font-family:{_SANS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAPER};padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
  style="max-width:600px;background:{_SURFACE};border:1px solid {_BORDER};border-radius:10px;overflow:hidden;">
<tr><td style="height:4px;background:{accent};"></td></tr>
<tr><td style="padding:24px;">
<p style="margin:0 0 4px;color:{_MUTED};font-size:12px;text-transform:uppercase;letter-spacing:.06em;">
  {esc(ctx['project_name']) or BRAND_USERNAME} · readout</p>
<h1 style="margin:0 0 16px;color:{_INK};font-size:20px;">
  {ctx['verdict_emoji']} {esc(readout.experiment)} · {esc(readout.metric)}
  <span style="color:{accent};">{esc(ctx['verdict_word'])}</span></h1>
{desc_html}
<table role="presentation" cellpadding="0" cellspacing="0">{row_html}</table>
{srm_html}
{link_html}
<p style="margin:20px 0 0;color:{_MUTED};font-size:12px;">Observed: {esc(ctx['timestamp'])}</p>
</td></tr>
<tr><td style="padding:14px 24px;border-top:1px solid {_BORDER};color:{_MUTED};font-size:12px;">
  Sent by {BRAND_USERNAME}{(' · ' + esc(ctx['project_name'])) if ctx['project_name'] else ''}</td></tr>
</table></td></tr></table></body></html>"""

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        text_body = self.format_message(readout, template or self.template)
        html_body = self._build_html_body(readout)

        message = MIMEMultipart("alternative")
        message["Subject"] = self._subject(readout)
        message["From"] = formataddr((self.from_name, self.from_email))
        message["To"] = ", ".join(self.to_emails)
        # Plain first, HTML last (the last part is the client-preferred one).
        message.attach(MIMEText(text_body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if self.use_tls:
                server: smtplib.SMTP = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10)
            try:
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.from_email, self.to_emails, message.as_string())
            finally:
                server.quit()
        except (smtplib.SMTPException, OSError) as exc:
            print(f"Failed to send email notification: {exc}")
            return False
        return True

    def __repr__(self) -> str:
        return f"EmailChannel(to={', '.join(self.to_emails)})"
