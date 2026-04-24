# GitHub Support Ticket — Draft

Submit at: <https://support.github.com/contact>
Category: Privacy / Repository content

---

## Subject

Request to purge cached commits after history rewrite on `vulture-s/arkiv`

## Body

Hello,

I recently performed a `git filter-repo` rewrite on my repository
`vulture-s/arkiv` to remove files that were committed by mistake —
personal media proxies and private-path metadata that were never
meant to be public. The rewrite has been force-pushed to all branches
and tags.

I understand that unreachable commits remain accessible via the
repository's reflog for roughly 90 days. Because the removed content
is sensitive (personal media and filenames), I would like to request
that your team expire those cached objects now, so the old commit
SHAs can no longer be fetched.

Details:

- Repository: `vulture-s/arkiv`
- Operation: `git filter-repo --path proxies --path arkiv.db ...
  --invert-paths` followed by `git filter-repo --replace-text ...`
  on a fresh mirror clone, then force-push of all branches and tags.
- Current heads are at:
    - `main`: `<fill in with: git rev-parse main>`
    - `archive/pre-reset-phase5-7`: `<fill in>`
- Tags rewritten: `v0.1.0`, `v0.2.0`, `v0.2.1`,
  `mac-snapshot-20260331`, `pc-snapshot-20260331`

Please garbage-collect any unreachable commits / blobs on this
repository so they are no longer available via direct SHA lookup.

Thanks very much,
<your name>

---

## Before sending

1. Run locally to get the current branch heads:
   ```bash
   git ls-remote https://github.com/vulture-s/arkiv.git | head
   ```
2. Paste those SHAs into the `main` and `archive/pre-reset-phase5-7`
   lines above.
3. Send via the contact form (logged in as the repo owner so they can
   verify ownership).

Typical turnaround is 1–3 business days. They usually confirm in
writing once the purge is complete.
