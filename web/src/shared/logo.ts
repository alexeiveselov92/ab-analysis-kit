/**
 * The abkit "Diverge" brand mark, shared by the report and explore headers so
 * EVERY generated surface carries the logo (docs/design/brand-tokens.md §Logo).
 *
 * Rendered as INLINE SVG (not an <img>): self-contained, zero network, and it
 * keeps the report's XSS guard honest (web/test/smoke.mjs asserts no injected
 * <img> survives). The mark is an Iris tile with one node fanning into two arms
 * — byte-for-byte the same geometry as the favicon baked by `abkit/tuning/html.py`
 * and `abkit/reporting/html_report.py`. Its two frozen Iris hexes (#6a45c4 iris,
 * #fbf9f3 paper) live in the one brand-token layer (`chart.ts` TOKEN_FALLBACKS);
 * the wordmark colour is the `--abk` CSS token so a palette swap flows through.
 */
export const LOGO_SVG =
  "<svg class='abk-logomark' viewBox='0 0 100 100' width='22' height='22'" +
  " role='img' aria-label='abkit' focusable='false'>" +
  "<rect x='3' y='3' width='94' height='94' rx='26' fill='#6a45c4'/>" +
  "<g fill='none' stroke='#fbf9f3' stroke-width='9' stroke-linecap='round'" +
  " stroke-linejoin='round'>" +
  "<polyline points='13 50 34 50'/><polyline points='34 50 86 27'/>" +
  "<polyline points='34 50 86 61'/></g>" +
  "<circle cx='86' cy='27' r='7' fill='#fbf9f3'/></svg>";

/** The mark + "abkit" wordmark lockup, prepended to a surface header. */
export function makeBrandLockup(): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'abk-brand';
  // Trusted constant SVG markup (no user input) — safe to set as innerHTML.
  wrap.innerHTML = LOGO_SVG;
  const word = document.createElement('span');
  word.className = 'abk-wordmark';
  word.textContent = 'abkit';
  wrap.appendChild(word);
  return wrap;
}
