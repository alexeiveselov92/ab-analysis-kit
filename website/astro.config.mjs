// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
  site: 'https://abkit.pipelab.dev',
  integrations: [
    starlight({
      title: 'abkit',
      description:
        'Declarative A/B experiment analysis in SQL + YAML. Watch the cumulative effect and its confidence interval converge, then read a WIN / LOSE / FLAT / INCONCLUSIVE verdict.',
      logo: { src: './src/assets/logomark.svg', alt: 'abkit' },
      favicon: '/favicon.svg',
      customCss: ['./src/styles/brand.css', './src/styles/landing.css'],
      // Brand fonts (docs/design/brand-tokens.md: Schibsted Grotesk + JetBrains Mono).
      // Inlined, not hoisted to a const, so Starlight's HeadConfig contextually narrows
      // each `tag` string literal — a hoisted const widens `tag` to `string` (ts2322).
      head: [
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
        {
          tag: 'link',
          attrs: {
            rel: 'stylesheet',
            href: 'https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap',
          },
        },
      ],
      // Brand uses dark terminal surfaces everywhere — keep code blocks dark on both themes.
      expressiveCode: {
        themes: ['github-dark'],
        // The docs use a ```jinja2 fence (the assignment macro); Shiki bundles it as "jinja".
        shiki: { langAlias: { jinja2: 'jinja' } },
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/alexeiveselov92/ab-analysis-kit' },
      ],
      sidebar: [
        { label: 'Overview', link: '/overview/' },
        {
          label: 'Getting Started',
          items: [
            { label: 'Installation', link: '/getting-started/installation/' },
            { label: 'Quickstart', link: '/getting-started/quickstart/' },
          ],
        },
        {
          label: 'Guides',
          items: [
            { label: 'Configuration', link: '/guides/configuration/' },
            { label: 'Experiments', link: '/guides/experiments/' },
            { label: 'Metrics', link: '/guides/metrics/' },
            { label: 'Compute methods', link: '/guides/compute-methods/' },
            { label: 'Databases', link: '/guides/databases/' },
            { label: 'Reading a readout', link: '/guides/reading-a-readout/' },
            { label: 'Explore cockpit', link: '/guides/explore/' },
            { label: 'Validate — A/A matrix', link: '/guides/validate/' },
            { label: 'Sequential analysis', link: '/guides/sequential/' },
            { label: 'Planning', link: '/guides/plan/' },
            { label: 'Visualizing results', link: '/guides/visualizing-results/' },
            { label: 'Notification channels', link: '/guides/notification-channels/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'CLI', link: '/reference/cli/' },
            { label: 'Internal tables', link: '/reference/internal-tables/' },
            { label: 'Legacy method catalogue', link: '/reference/legacy-method-catalogue/' },
          ],
        },
        { label: 'Examples', link: '/examples/' },
        {
          label: 'Development',
          items: [
            { label: 'Architecture', link: '/development/architecture/' },
            { label: 'Contributing', link: '/development/contributing/' },
          ],
        },
        { label: 'Changelog', link: '/changelog/' },
      ],
    }),
  ],
});
