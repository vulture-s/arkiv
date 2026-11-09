// English dictionary, one fragment per UI area so parallel edits don't collide.
// Keys are the zh-TW source strings (see ../../i18n.js). Duplicate keys across
// fragments must agree — tests/i18n.test.mjs fails if two fragments disagree.
import core from './core.js'
import settings from './settings.js'
import main from './main.js'
import bins from './bins.js'
import ingest from './ingest.js'

export const FRAGMENTS = { core, settings, main, bins, ingest }

const en = {}
for (const frag of Object.values(FRAGMENTS)) Object.assign(en, frag)
export default en
