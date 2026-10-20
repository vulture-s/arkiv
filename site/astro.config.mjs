// @ts-check
import { defineConfig } from 'astro/config';
import { rewriteDocLinks } from './src/lib/rewrite-doc-links.mjs';

// GitHub Pages project site: https://vulture-s.github.io/arkiv/
// `format: 'file'` keeps the URLs the Jekyll-rendered /docs site already
// published (quickstart.html, faq.html …) — the old homepage links to them and
// so do READMEs and outside posts. Changing the shape would 404 every one.
export default defineConfig({
  site: 'https://vulture-s.github.io',
  base: '/arkiv',
  build: { format: 'file' },
  // Off on purpose: the compressor dropped the whitespace between inline
  // elements ("English: README" rendered as "English:README").
  compressHTML: false,
  markdown: { rehypePlugins: [rewriteDocLinks] },
});
