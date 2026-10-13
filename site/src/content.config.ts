import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { PUBLISHED_DOCS } from './lib/published-docs.mjs';

// Docs are read straight from the repo (../docs, ../CHANGELOG.md): one source,
// no copies to drift. The site only chooses which files are public.
const docs = defineCollection({
  loader: glob({
    base: '..',
    pattern: Object.values(PUBLISHED_DOCS),
    generateId: ({ entry }) =>
      Object.keys(PUBLISHED_DOCS).find((k) => PUBLISHED_DOCS[k] === entry) ?? entry,
  }),
});

export const collections = { docs };
