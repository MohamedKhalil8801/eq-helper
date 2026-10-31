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
  - The upper treble slider is always shown, locked as calibrated until you unlock it; its own panel
    is gone.
  - **Centre** next to Left and Right: sound in both ears, and the sliders move both together.
  - The EQ on/off switch is a **⏻ EQ** power button at the top right of Find your resonance.
  - **〰 Full sweep** joins the sound buttons; the Sound tools tab is gone. The "Where can I use my EQ?"
    tab is gone too: **Export for my EQ app…** shows the same apps.
  - One accent colour throughout (your system's accent colour no longer shows up, e.g. in History),
    and no more blue outlines around the focused control.

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
