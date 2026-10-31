# ResonanceFit changelog

Each release's section is copied into its notes on the releases page. Add a
`## X.Y.Z` section here before tagging `rf-vX.Y.Z`.

## Unreleased

- Find it for me: every card in a round now plays at the same loudness, for whichever sound you
  use. Before, cards could differ by up to about 6 dB, and the louder one tends to sound better.
- A simpler window:
  - The calibration list matches your `Calibrations` folder: replaced or deleted files leave it.
    **Remove…** takes any calibration out of the app.
  - The target sits under the calibration at the top; its own panel is gone.
  - The upper treble slider is always shown. On **Auto** it shows your calibration's upper treble,
    which moves with your resonance; **Manual** lets you set it. Its own panel is gone.
  - **Centre** next to Left and Right: sound in both ears, and the sliders move both together.
  - The EQ on/off switch is a **⏻ EQ** power button at the top right of Find your resonance.
  - **〰 Full sweep** joins the sound buttons; the Sound tools tab is gone. The "Where can I use my EQ?"
    tab is gone too: **Export for my EQ app…** shows the same apps.
  - **Settings → Appearance**: System, Light or Dark.
- A new look, "studio gear": a near-black instrument (or a warm light version), where amber always
  means *your ear*. Every colour meets WCAG contrast, and the guided test uses the same colours as the
  rest of the app (your system's accent colour no longer shows up anywhere).
- A slim bar at the top: your IEM and calibration, the target, an **EQ on/off** switch with a status
  light, and a ⋯ menu (import a target, remove a calibration).
- Both ears' results are always visible at the top of Find your resonance, with Left / Right / Centre
  beside them.
- **Tunings** replaces Listen & save and History: click a saved tuning to switch to it; moving
  anything never changes it, but becomes **Current · unsaved**, which you can save or go back to.
- Keys on the Tune page: T tone, S sweep around, F full sweep, P pink, G grey, E EQ on/off, L / R / C
  ear.
- Guided test: **Enter** always confirms the selected card, and each card shows where it sits from low
  to high.
- Keyboard focus shows as a neutral ring only when you use the keyboard.
- Upper treble: **Auto** or **Manual**, no Reset. On Manual each ear keeps its own value, so moving the
  resonance no longer moves an ear you hadn't set (it did for the right ear).
- Centre asks before giving both ears the same value when they differ.
- **Space** plays and stops, as in EQ Helper: it stops whatever plays, and plays the last sound again.
- The EQ follows the resonance slider in real time while you drag it, instead of after you stop.
- Automatic updates: a new version downloads in the background (checked against the release's
  checksums), then **Install and restart**, or it installs when you close the app. Your calibrations,
  the files next to the app, history and settings are kept. Where the app can't replace itself (a
  read-only folder, macOS running it from Downloads, Linux not as an AppImage) it shows the download
  link as before. Updating from 0.3.0 still uses the link once: automatic updates start with the
  version after this one.

## 0.3.0

### New: Find it for me
- A guided listening test finds each ear's resonance for you. It plays eight EQs spread across the
  range, then asks you to compare pairs, narrowing in after every answer. It stops once the answer
  is pinned to within about 100 Hz (at most 14 rounds).
- Cards are numbered 1–8 and shown from low to high, so the number is also the key to press.
- Rule out a card you're sure about with **Rule out**, the X key or a right-click.
- Answer whenever you're ready. A ✓ marks the cards you've listened to.
- Optionally tune the upper treble the same way: eight cards, then a smoothness check.
  **Can't tell** keeps it as calibrated.
- The test has the window to itself; only the output device and volume stay visible.
  **Stop the test** leaves at any time.

### Sounds
- Grey noise plays wherever pink noise did, matched in level.
- Focused noise: noise centred on the area being tuned.
- The sound you pick (pink, grey, focused or sweep) stays until you change it.
- A new **Test audio** tab, and Sound tools buttons start and stop their own sound.

### Equalizer APO
- A clearer notice when another EQ is on in Equalizer APO. It updates by itself while the app is
  open, and **Pause it while I tune** sits right next to it.
- Files written for Equalizer APO now select `Channel: L R` instead of `Channel: all`, which could
  upset the left/right balance.

### Other
- History keeps each tuning once, with a short name.
- Unexpected errors are written to `errors.log` in the app's data folder, to help with support.
