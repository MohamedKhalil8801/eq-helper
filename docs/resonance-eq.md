# Measurement-trained resonance EQ

Open **Resonance EQ…** after selecting the intended APO device in the main
window. The bundled starting data is the user's six left and eight right
Truthear Nova / SpinFit W1 IEC-711-clone measurements. Other measurement folders
use the same automatic learning code; there are no IEM-specific fitted curves,
peak positions, or model coefficients in the implementation.

## Measurements and ear selection

Click **Load measurement folder…**. TXT/CSV responses and REW text exports are
read recursively. Use one consistent playback level and calibration across all
measurements, including both ears. Do not mix EQ-on measurements with raw ones.

Side labels are recognized in this order:

- Filenames such as `L 1.txt`, `R1.txt`, `Left 2.csv`, or `Right 3.txt`.
- REW's `* Measurement:` name. Its `Source` input-channel field is deliberately
  ignored: it describes the measurement input, not which IEM was measured.
- `Left`/`Right` or `L`/`R` subfolders, including the selected folder itself.

With both sides present, **Left** and **Right** tabs keep separate resonance
settings. Select a tab to view its curve and send test tones/sweeps to that ear.
**Listen on both channels** overrides tone isolation without linking the EQs.
The previous audio-channel selection is restored when the dialog closes.

With just one labelled side, there is only that side's tab and one peak to find;
its EQ is applied identically to both channels. An entirely unlabelled folder
is treated as one shared set. Unlabelled files mixed into an L/R-labelled set
are skipped rather than guessed. Invalid, ambiguous, and skipped files are
reported in **Model details…**. Imports that fail leave the working model intact.

Imports run in the background. The last successfully imported folder is saved
in settings and read again when the dialog opens. Reload a folder after adding
measurements to retrain the models. Model training does not modify source files.

## Listening and EQ

1. Load any target TXT/CSV (frequency in Hz and level in dB).
2. For each available ear, identify its strongest treble resonance between
   **6,000 and 9,000 Hz**. Move its slider, type a frequency, or drag the orange
   marker while listening to a sine. Click the graph for a sine; Shift+click or
   **Focus Sweep** for a sweep. Use low levels; Space or **Stop** stops playback.
3. Optionally enable **Personalize higher-treble correction** and move the slider or enter the
   higher-frequency treble peak you hear (default initial control position:
   15,500 Hz). The default is OFF, so leaving the option untouched preserves
   the existing EQ exactly. It looks for a resolved
   higher resonance in the current coupler prediction and smoothly realigns the
   *corresponding correction feature* from that frequency toward your entered
   frequency, beginning above the selected primary resonance. If there is no
   resolved higher coupler peak in the target/measurement bandwidth, the
   adjustment is a no-op. The prediction itself stays unchanged. Each ear has
   its own saved frequency. **Reset to predicted** clears only the selected
   ear's preference; the other ear keeps its adjustment. If neither supplied
   ear has a preference, personalization switches off. Turning the checkbox
   off returns both ears to the original EQ without erasing saved frequencies.
   These local preferences are remembered for the next session; they are not
   submitted as training data and do not change the learned model.
4. Click **Generate L/R EQ** (or **Generate shared EQ** for a single-side set).
   Subsequent resonance adjustments add versions when movement pauses or ends.
5. Toggle **EQ on** to audition via Equalizer APO. Version selection restores
   both peak settings, the target, and the measurement model used for that
   version, even after importing a different folder. History lasts this session.
6. **Save selected EQ…** exports one APO TXT file. Add it to a preset using the
   existing EQ library for permanent use.

APO comments and history mark personalized versions. The user-fit peak and
its detected coupler counterpart are recorded in export comments; when no
higher coupler feature is available, the app leaves the original correction
untouched instead of inventing a match. The shared preamp is computed from the
standard, unmodified EQ before any personal warping, so treble preview changes
cannot alter the shared level/headroom setting. A monotone log-frequency map
anchors the detected higher peak exactly to the chosen correction frequency,
while leaving frequencies at/below the primary resonance and the upper coverage
endpoint fixed. Only coordinates are cubic-interpolated; correction magnitudes
are resampled log-linearly from native data, without smoothing or gain overshoot.
Shifts are bounded to a factor of 1.4. If that bound limits a request, the UI and
APO comments show the applied frequency separately from the requested frequency.
The displayed coupler-plus-EQ curve is **not** a prediction of your real-ear
response after personalization. Saved preferences are a starting point only;
recheck them after changing IEMs, tips or fit.

Stereo exports use a common `Channel: ALL` preamp followed by separate
`Channel: L` and `Channel: R` GraphicEQ filters, then restore `Channel: ALL`.
Single-side exports use one `Channel: ALL` GraphicEQ. Bypass retains the same
shared preamp. Using separate independently chosen preamps would undo channel
level balancing, so the worst-case boost across both corrections determines
headroom (plus 1 dB).

Audition temporarily replaces the selected device's preset with an isolated
preview preset to avoid stacked corrections. Closing restores the previous
preset and removes temporary files; exports are kept. Config-wide processing
outside the selected device's preset is not bypassed.

## What is learned

Each usable measurement needs coverage from 1 kHz through 11 kHz and a resolved
local resonance maximum in 5–11 kHz (at least 0.5 dB prominence). That search
window is deliberately wider than the listening control: deeper measurements
above 9 kHz are useful anchors, too. A sloping band edge is not counted as a peak.

For each side independently, the model:

- Detects peak positions from the actual unsmoothed measurements and sorts fits
  by peak frequency, not filename order or a prescribed insertion sequence.
- Retains the densest native frequency grid over the fits' common 20 Hz–20 kHz
  coverage. The bundled data retains 54,561 points per response. Other grids
  are log-frequency-interpolated to it; there is no response smoothing.
- Pools repeated fits whose peak coordinates differ by less than 0.05%.
- Compares local magnitude blending, partial peak alignment, and full peak
  alignment using interior leave-one-out reconstruction checks. The lowest
  band-weighted RMS model is selected automatically when enough fits exist.
- Tests nearest-fit warping against damped local edge trends using held-out
  endpoints. Edge trends are used only if they improve that check by over 5%.
- Blends the whole response, including measured bass/mid and level changes.
  Frequency registration leaves frequencies below 3 kHz in place and aligns
  the moving treble peak to the requested coordinate.

All measurements share **one** level reference: the median 1 kHz measurement
level across the entire imported set. Each ear/fit is NOT separately normalized;
that would erase the very L/R sensitivity differences being corrected. The
unrelated absolute SPL offset of the target is removed at 1 kHz. The difference
between that target and each prediction becomes the correction, without
smoothing, gain limits, treble exceptions, or parametric fitting. Correction is
limited to overlapping measured/target coverage within 20 Hz–20 kHz. The plotted
corrected shape excludes the uniform preamp attenuation.

## Limits and diagnostics

**Model details…** lists detected peaks, source files, chosen models, holdout
scores, edge behavior, and import notes. The active ear's measured coverage and
interpolation/extrapolation state stay visible above the graph, even during APO
audition. Saved EQ comments include the coverage state as well.

The supplied left anchors span about **7,441–9,442 Hz**; the right anchors span
about **6,871–9,454 Hz**. Thus 6 kHz is supported but extrapolated on both sides,
and 6–7.44 kHz is outside the left measurements. More shallow-fit measurements
there are needed to validate that part of the range. With only one distinct
fit, the app can frequency-warp it but cannot learn an insertion trend.

Holdout results describe within-set coupler reconstruction, not uncertainty
bounds or real-ear accuracy. Selecting a heard peak does not uniquely identify
an ear-canal response, and a single coordinate cannot account for independently
varying seal, fit, calibration, or hearing sensitivity. The optional personal high-treble fit is a listening-based EQ-correction
adjustment, not a measurement of the ear, a physical shift of IEM resonance,
or a retraining signal. It only shifts the correction if a corresponding higher
peak is resolved in the coupler prediction; the prediction itself remains
unchanged. This balances the predicted measured channel responses, not
guaranteed perceived loudness.

`eq_helper/dsp/nova_w1.npz` contains the bundled response grid, unsmoothed curves,
measured peak metadata, side labels, and source names. Models are trained again
when it is loaded. For PyInstaller, include it as package data; the local
`EQ Helper.spec` already does so. Equivalent CLI option:
`--add-data "eq_helper/dsp/nova_w1.npz;eq_helper/dsp"`.
