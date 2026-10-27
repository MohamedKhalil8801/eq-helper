# App catalog as data, export formats as code

Customers listen through many EQ apps across Windows, macOS, Linux, Android,
iOS and hardware, and the list keeps changing. EQ apps are therefore described
as entries in a data catalog (platforms, links, accepted export formats and
their per-app limits, L/R support, setup steps, detection hints), while only
the few export formats (GraphicEQ, convolution, parametric) are code. Adding an
app that uses an existing format is a catalog edit with no new code; a new
format is one new exporter.

## Consequences

- The catalog can later be refreshed from the releases repo without an app
  rebuild.
- Shared-only apps (one EQ for both ears) stay listed but ranked below
  full-L/R apps and clearly labelled as a compromise.
