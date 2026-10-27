# One source repo, plus a public releases-only repo

The admin app and customer app share the `dsp`/`audio` code, so all source stays
in one repository (`eq-helper`, may become private). CI publishes customer-app
builds to a separate public repository that holds release binaries only; the
admin app downloads builds from it and the future customer update check reads
it, so neither needs a token and no source becomes public.

## Considered Options

- Splitting admin and customer apps into separate repos (customer one public):
  rejected because the shared code would have to be duplicated or published as
  a package, and a public customer source protects nothing a decompiled
  PyInstaller build doesn't already expose.

## Consequences

- Customer data (names, calibration files, measurements, the customer library)
  never lives in any repo; it stays in the admin app's local data folder.
- While `eq-helper` is public under Apache 2.0, anyone may rebuild and sell the
  customer app; revisit visibility/licence before launch.
