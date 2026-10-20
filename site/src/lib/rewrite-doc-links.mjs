import path from 'node:path';
import { PUBLISHED_DOCS, REPO_BLOB } from './published-docs.mjs';

// Markdown in the repo links to siblings as `faq.md`, `../README.md`.
// On the site a published sibling is `<slug>.html`; anything else points at the
// file on GitHub rather than at a 404.
const bySource = new Map(Object.entries(PUBLISHED_DOCS).map(([slug, src]) => [src, slug]));

function rewrite(href, fromFile) {
  if (!href || /^[a-z]+:|^#|^\//i.test(href)) return href;
  const [p, hash = ''] = href.split('#');
  if (!p.endsWith('.md')) return href;
  const fromDir = path.posix.dirname(fromFile);
  const target = path.posix.normalize(path.posix.join(fromDir, p));
  const frag = hash ? '#' + hash : '';
  const slug = bySource.get(target);
  return slug ? `${slug}.html${frag}` : `${REPO_BLOB}${target}${frag}`;
}

export function rewriteDocLinks() {
  return (tree, file) => {
    const abs = file.path || file.history?.[0] || '';
    const repoRoot = path.resolve(process.cwd(), '..');
    const rel = path.relative(repoRoot, abs).split(path.sep).join('/');
    const walk = (node) => {
      if (node.type === 'element' && node.tagName === 'a' && node.properties?.href) {
        node.properties.href = rewrite(String(node.properties.href), rel);
      }
      node.children?.forEach(walk);
    };
    walk(tree);
  };
}
export { rewrite as _rewriteForTest };
