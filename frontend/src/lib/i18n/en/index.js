// English dictionary, one fragment per UI area so parallel edits don't collide.
// Keys are the zh-TW source strings (see ../../i18n.js). Duplicate keys across
// fragments must agree — tests/i18n.test.mjs fails if two fragments disagree.
import core from './core.js'

export const FRAGMENTS = { core }

const en = {}
for (const frag of Object.values(FRAGMENTS)) Object.assign(en, frag)
export default en
