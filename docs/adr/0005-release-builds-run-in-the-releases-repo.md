# Release builds run in the releases repo

The customer app is built, self-tested and published by a workflow in the
public releases-only repo (`resonancefit-releases`, "Build and publish"),
which checks out a chosen `eq-helper` branch, tag or commit. Publishing there
uses that repo's own token, so no secret is needed while `eq-helper` is
public. A `rf-v*` tag in `eq-helper` can start it through a
`repository_dispatch` once a `RELEASES_DISPATCH_TOKEN` secret exists; until
then a release is started by hand from the releases repo's Actions tab.

## Considered Options

- Build in `eq-helper` and push to the releases repo with a deploy token
  (the original plan): needs a long-lived write token stored in `eq-helper`
  before anything can be released.

## Consequences

- Every release runs the full test suite, builds Windows, macOS (Apple
  silicon) and Linux, runs `--self-test` on each build and package, publishes
  with `SHA256SUMS`, then runs the end-to-end check (`packaging/e2e_check.py`)
  against the published files on all three systems.
- If `eq-helper` becomes private, the releases repo needs a read-only
  `SOURCE_REPO_TOKEN` secret to check the source out.
