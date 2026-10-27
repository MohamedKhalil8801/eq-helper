# Generic customer app plus a per-customer calibration file

Customers need the customer app on Windows, macOS and Linux, but PyInstaller
only builds for the OS it runs on and macOS apps must be signed. We therefore
build one generic customer app per release and OS (CI), and the admin app's
"Generate" only writes a small per-customer calibration file and packages it
with the prebuilt app for each chosen OS. The app discovers the calibration
(and its targets folder) next to itself.

## Considered Options

- A full PyInstaller build per customer: one-click only for the operator's own
  OS (Windows), 1–3 minutes per build, and every bug fix needs a rebuild for
  every customer.

## Consequences

- Recalibration ships a new calibration file, not a new app.
- The calibration file format is a versioned contract between admin app and
  customer app.
