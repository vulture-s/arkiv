// Which repo markdown files become pages. Everything else under ../docs is
// internal (handovers, reviews, session logs) and stays repo-only.
// Key = output slug (→ <slug>.html), value = path relative to the repo root.
export const PUBLISHED_DOCS = {
  'quickstart': 'docs/quickstart.md',
  'quickstart-mac': 'docs/quickstart-mac.md',
  'quickstart-windows': 'docs/quickstart-windows.md',
  'install': 'docs/install.md',
  'pipeline': 'docs/pipeline.md',
  'pipeline.zh-TW': 'docs/pipeline.zh-TW.md',
  'api': 'docs/api.md',
  'api.zh-TW': 'docs/api.zh-TW.md',
  'architecture-anti-hallucination-guard': 'docs/architecture-anti-hallucination-guard.md',
  'changelog': 'CHANGELOG.md',
};

export const REPO_BLOB = 'https://github.com/vulture-s/arkiv/blob/main/';
