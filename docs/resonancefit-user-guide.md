# ResonanceFit user guide

ResonanceFit turns the calibration of **your** IEMs into an EQ for **your** ears. Your IEMs were measured
on a coupler at several insertion depths; that is your *calibration*. Because everyone's ears are
different, the app asks you one thing: where the strongest treble peak, your *resonance*, sits in each
ear. You find it by ear with a short guided listening test (a few minutes), choose the sound you want
(a *target*), and the app builds a separate EQ for your left and right ear.

## What you received

Your download from your tuner contains:

- the app for your computer (Windows, macOS or Linux),
- a `Calibrations` folder (Windows/Linux) or a `.rescal` file (macOS): your calibration,
- a `Targets` folder with the target curves (Windows/Linux),
- a calibration sheet (PDF): a one-page record of the measurement,
- `First launch.txt`: how to open the app the first time.

**Use the same ear tips (and the same accessories listed on your sheet) when you listen.** The
calibration was measured with them; other tips change the sound.

## First launch

The app isn't code-signed yet, so your system asks once.

- **Windows:** extract the whole zip first (right-click → Extract All), then open `ResonanceFit.exe` from
  the extracted folder. If you see “Windows protected your PC”, click **More info**, then **Run anyway**.
  Keep the `Calibrations` and `Targets` folders next to the app.
- **macOS (Apple silicon):** open the `.dmg` and drag ResonanceFit into Applications. Right-click (or
  Control-click) the app, choose **Open**, then **Open** again. If macOS still refuses, open System
  Settings → Privacy & Security and click **Open Anyway**. Then double-click your `.rescal` file once
  (or drag it onto the window); it's remembered from then on.
- **Linux:** make the AppImage executable (right-click → Properties → allow executing, or `chmod +x`) and
  run it, with the `Calibrations` and `Targets` folders next to it.

The app opens with your calibration already loaded. If it shows **Open the calibration file you
received**, open or drop your `.rescal` file. A short walkthrough explains the three steps. You can
reopen it from **Settings → Show the walkthrough again**. On Windows and Linux the app may also offer to
open `.rescal` files when you double-click them.

**Start quiet.** Test tones can be piercing at the resonance. The app starts at a low volume every time
and asks you to turn your volume down before the first sound. **Press Space at any time to stop all
sound.** The bar at the bottom of the window always shows the **Output** you're playing on, the
**Volume** and **Stop all sound**.

## ① Find your resonance

**Turn off any other EQ first:** your music app's EQ, sound enhancements (Dolby, Windows or Realtek
effects…), Equalizer APO or Peace, and your dongle's EQ. Otherwise you hear it on top of this one and
find the resonance in the wrong place. The app names the EQ apps it finds on your computer, and if your
own EQ is still on in Equalizer APO it offers to turn it off while you tune (apply again when you're done).

### Find it for me (the guided test)

Press **Find it for me (a few minutes)**. A short screen reminds you to wear both IEMs as usual, keep
the volume low and turn other EQs off; press **Start the test**. Then, for the left ear and then the
right (one ear only for a calibration made for both ears at once):

1. **Loudness match.** Two narrow bands of noise take turns in that ear, **A** then **B** (the two
   squares light up as each one plays). Move the slider until **B sounds exactly as loud as A**, then
   press **They sound equally loud** (Enter). The two sound a little different in pitch: ignore that and
   listen only to loudness. **Hear: A and B in turn / A only / B only** (keys 3, 1, 2) lets you compare
   them however suits you; ← → move the slider (↑ ↓ in finer steps).
   - The slider starts somewhere random and never shows numbers, and sometimes B is the lower band,
     sometimes the higher one, so nothing nudges your answer.
   - Each match measures how much louder one part of the treble sounds than another. A handful of them
     trace the shape of what your ear hears, and the app looks up where your calibration's model
     produces that shape: that is where your resonance is. Your own hearing (which changes across the
     treble for everyone) and the way a higher sound can seem more prominent are allowed for: they are
     smooth across the treble, a resonance isn't.
   - One band is often the same: a band near 4.7 kHz that hardly changes with how deep the IEM sits.
   - The left ear starts from your calibration's measurements, the right ear from the left ear's
     result, so the right ear usually needs fewer matches. About 6–10 matches per ear; if one answer
     doesn't fit the others, the app asks that pair once more.

   Without a model to compare with (rare), the test uses the older **louder test** instead:

   **Louder test.** Two cards, **Play A** and **Play B**, each a narrow band of noise in that ear at
   the same level. Click a card to hear it (the bar on top pauses and plays again); click the other card
   to switch, as often as you like: the sound switches at once. When you've heard both, answer
   **Louder: A** (or B; the wide button follows the card you selected) or **About the same**.
   - The bands start wide and get narrower as the test closes in. Bands rather than pure tones because
     a sealed ear canal adds small dips and bumps that a single tone can land on; a band averages over
     them.
   - "About the same" is a fine answer: two sounds beside your resonance can sound alike, and the test
     keeps that in mind instead of assuming the resonance lies between them.
   - Each round picks the pair that tells it the most. Every third round is an easy check, a pair whose
     answer it can already predict, so answers that don't follow the sounds are noticed. About seven
     rounds are usually enough, a few more if some answers disagree.
2. **Smoothness check.** Three cards, **A**, **B** and **C**: three slightly different EQs around what
   the loudness match found. The steps get finer as you go, down to EQs about 50 Hz apart. **Listen with Pink noise, Grey noise or Sweep** (switch any time, use any;
   keys N, G, W): with noise, pick the one whose treble sounds **smoothest**, with no harsh, hissy or
   dull spot; with **Sweep**, a slow sweep through the treble, pick the one where it **stays even**, with
   no swell or dip as it passes. Grey noise is pink noise with the treble (and bass) lifted the way
   hearing needs it, so a harsh or dull spot high up stands out more.
   Answer **Smoothest: B** or **Can't tell them apart**. A few rounds.

The cards are in a different order every round and never show frequencies, and the moving bars on the
playing card only follow how loud the sound is, the same for every card, so you choose by ear. The
answer buttons wake up once you've listened to each card for a moment (**Listen to each one first**).

When an ear is done the app shows its result, e.g. **Left resonance: 7,640 Hz**, and sets it in the
graph and the EQ exactly as the slider would. **Back** takes back your last answer; **Adjust by hand**
leaves the test and keeps what's been found so far (including the louder test's best guess once it's
narrowed down). With the louder test, if your answers point to more than one place, the app says so and
offers **Do this ear again**, **Use it anyway** (the smoothness check then starts from the best guess, with wider steps) or
**Adjust by hand**. If the tones all sound alike, the test goes on to the smoothness check with wider
steps.

**Upper treble (optional).** After both ears the app offers **Fine-tune my upper treble** or **Skip, I'm
done**. There's no louder test up there: above about 12 kHz hearing falls off steeply and differs a lot
between people, so a high sound seeming quieter says more about your ears than about the IEM. Instead the
app starts from where your calibration predicts the upper peak for the resonance you just found (it sits
at about twice that frequency). For each ear:

1. **Can you hear it?** Play a high sound there at your normal volume. If you hear it clearly, answer
   **I hear it clearly**. If you hear it only faintly or not at all, answer **Barely, or not at all**:
   that ear's upper treble stays as calibrated. That's normal and nothing is lost: what you can barely
   hear matters little for your EQ.
2. **Smoothness check**, a few rounds: three EQs that differ only in the highest treble, around the
   prediction (never further than the EQ can move it). The **sweep** is selected first: your hearing's
   fall-off only makes it fade smoothly as it rises, while a misplaced peak leaves a swell or dip as it
   passes. Pick the one that fades most evenly. Pink or grey noise works too.

A found upper treble turns on **Adjust the upper treble** (step ③) with that value.

**Balance check (optional, at the end).** If both resonances are right, the same sound in both ears
sounds centred at every pitch. The app plays a series of short sounds in both ears through your EQ (use
your normal listening volume). For each one, if it leans to one side, move it back to the middle with
the slider (or ← →, Shift for bigger steps, Home to re-centre), then press **Next**; if it's already
centred, just press **Next**. Two of the sounds come twice, to see how steady the answers are. The check
never changes your EQ by itself:

- **Left and right are balanced ✓**: your two resonances agree.
- **"Your right ear's resonance may be a little higher"**: some sounds leaned the way a misplaced
  resonance would. **Check the right ear again** runs a short smoothness check of that ear from where the
  balance points; then you can run the balance check again. (Balance only shows the difference between
  your ears: if the re-checked ear hardly moves, the next check points at the other ear.)
- **Mostly balanced**: a lean that doesn't look like a misplaced resonance (the calibration or your
  hearing there) is only mentioned, and so is a broad lean across the treble.
- **The bass leans to one side**: the other IEM may not be sealed as well. Reseat it and check again.

Keys: **1 2 3** pick and play a card, **P** play/pause, **Enter** answers for the selected card, **S**
is *About the same* / *Can't tell them apart*, **N** / **W** noise or sweep in the smoothness check,
**Backspace** goes back, **Space** stops all sound.

### Adjust by hand

Press **Adjust by hand** to show the slider and its sound buttons (they're shown from the start once
you've found your resonance before):

1. Put both IEMs in as you normally wear them, with your usual tips.
2. On the **Left** tab, press **▶ Play tone**. The tone plays in your left ear only, and a dotted line on
   the graph shows the frequency that's playing.
3. Move the slider, type a frequency or drag the orange **Resonance** line on the graph until the tone
   sounds **loudest or most piercing**. That's your left resonance. **Sweep around it** plays a slow
   sweep around that frequency if that's easier to judge (the dotted line follows the sweep). The
   resonance is where the sweep gets loudest.
4. Do the same on the **Right** tab.

Each sound button starts its sound and stops it when you press it again (it reads **■ Stop tone** while
playing). Space, or **Stop all sound** at the bottom, stops everything.

To check by ear, press **≈ Pink noise + EQ** or **≈ Grey noise + EQ**: the noise plays through your EQ
(the EQ switches on). Move
the resonance slowly while it plays; where it's right, the treble sounds smooth and even, with no harsh,
hissy or dull spot.

The graph shows **Your IEM** (what the calibration predicts for your setting), the **Target** and
**With your EQ**. Point at the graph to see the frequency under the mouse; click or drag on it to hear a
tone at that frequency. If you land outside what was measured for your IEMs, the app
tells you so: the EQ there is an estimate, which is fine if it sounds right.

## ② Choose your target

The target is the sound you want. The one marked **default** was chosen for you by your tuner. You can
try any other, or **Import…** a target curve (TXT or CSV, frequency and level) you found elsewhere.

## ③ Fine-tune upper treble (advanced, optional)

Only if you hear a second, higher peak (above about 10.5 kHz) that still bothers you. Turn on **Adjust
the upper treble**: an **Upper treble** slider appears under the resonance slider in step ①, with a pink
**Upper** line on the graph. The same sound buttons play at whichever one you last moved (or pick
**Resonance** or **Upper treble** next to them). Move it to where you hear the peak, per ear. **Reset** puts the selected ear back. Leave it off otherwise: off
keeps the EQ as calibrated.

## ④ Listen & save

- **EQ off / on:** switches the EQ in the app on and off so you can compare. It's level-matched, so you
  compare tone, not loudness. Use a tone, a sweep or something you know well (see *Test audio*).
- **Save tuning:** keeps your current choices in **History**, with an optional name (for example
  “Bright tips”). Saving a tuning that's already there just renames it (if you typed a name).
- **Export for my EQ app…:** makes the files for the app you listen with (below).
- **Apply to Equalizer APO** (Windows, when Equalizer APO is installed): makes this tuning your
  computer's EQ for everything you play. Choose your headphone output once. The app keeps one entry,
  “Resonance EQ – your IEM”, in Equalizer APO and replaces it each time you apply. It's a convolution
  filter with a file for each sample rate, so it keeps working if your device's rate changes (any
  unusual rate gets the same EQ as GraphicEQ). **Switch to:** applies any saved tuning, listed by name; the one applied now is marked
  **✓ in use**. Switching never adds entries. To turn it off,
  use **Settings → Turn the EQ off in Equalizer APO**. If Peace is installed, turn its EQ off: it's a
  front-end for Equalizer APO and you'd hear both.
- **Apply to EasyEffects** (Linux, when EasyEffects is installed): the same for EasyEffects. The app
  writes the preset “Resonance EQ - your IEM” (one Convolver) and loads it. Loading a preset replaces
  the effects running in EasyEffects; pick your own preset there to go back. If EasyEffects isn't
  running, start it and choose that preset. **Settings → Turn the EQ off in EasyEffects** bypasses it.

### History

Every tuning you save, export or apply is listed once, most recently used first: its name, when you
last saved, exported or applied it, and (for a named one) its target and resonances, plus the app and
files. Using a tuning again doesn't add a copy; it moves to the top. A tuning you didn't name is
named from its settings, like “PEQdB Diamond · 7400 / 8300 Hz” (left / right). **Rename** gives it a
name of your own. **Reapply** puts every control back exactly as it was, even if the target file is
gone. Older versions listed every export and apply separately; those copies are merged once.

## Using your EQ elsewhere

**Export for my EQ app…** first asks **Where will you listen?**: this computer, a phone or tablet
(Android or iPhone), a dongle or DAP, or another computer. It then shows the apps that fit, each with:

- whether it's free or paid, and whether it works system-wide or only inside that player,
- **Left/right correction: full** (separate EQ per ear) or **shared only** (one EQ for both ears; the
  app averages your two EQs and tells you the balance to set),
- for parametric EQ apps, how closely the filters match (for example “within ±1 dB up to 16 kHz”),
- **Installed** if the app was found on this computer, a **Download** button and **How to set it up**.

Press **Export for this app**, choose a folder, and follow the steps shown. For Equalizer APO the
export is a convolution by default: one file for each sample rate plus `… convolution.txt`, which picks
the file for the rate your device runs at (GraphicEQ is still there as an option). For a phone, copy the files
over first (USB, a cloud drive or email). For convolution files, the only setting is the sample rate,
pre-filled with the rate your output device runs at.

The same cards are on the **Where can I use my EQ?** tab. **Predicted response** exports your IEM's
predicted curves (left and right, optionally with the EQ) for squig.link or PEQdB studio.

## Other tabs

- **Test audio:** your tuner's suggested links plus whatever you add: audio files from your computer
  (any recording you know well) or web links. **+ Add audio files…** and **Add link…** sit in the middle
  of the list while it's empty. Files play in the app
  through your EQ with the EQ on/off switch. Formats the app can't play open in your system's player.
- **Sound tools:** tone, sweep, full sweep, pink and grey noise, volume, and Left/Both/Right. As on the
  Tune tab, each button starts its sound and stops it when pressed again.
- **Settings:** output device, Equalizer APO, the calibrations and data folders, the walkthrough, and
  **Version and updates** (below).
- **About:** your calibration sheet, contact and licences.

## Updates

When the app opens, it checks once a day whether a newer version has been published. If today's check
already worked, it doesn't check again until tomorrow; if it couldn't check (no internet, GitHub
unreachable), it tries again the next time you open it. There is no background service: nothing runs
while the app is closed, and nothing is installed automatically.

When a new version is out, a strip at the top of the window says so, with **Download** and **What's new**.
**Not now** hides it until a newer version comes out. **Settings → Version and updates** shows your
version, the latest one, when the last successful check happened and **Check for updates now**. You can
turn off the daily check there; checking by hand still works.

To update, download the new version and replace the old app (keep the `Calibrations` and `Targets` folders
next to it on Windows and Linux). Your calibrations, history and settings are kept.

## Troubleshooting

- **No sound:** check **Output** at the bottom of the window and your system volume. Press Space and
  try again.
- **The EQ sounds wrong:** check you're wearing the tips listed on your calibration sheet, then find your
  resonance again. Fit matters: re-seat the IEMs and repeat step ①.
- **One side louder in a shared-only app:** set that app's balance as shown after exporting.
- **“Equalizer APO couldn't be changed”:** follow the steps shown, which give the Users group permission
  to change Equalizer APO's config folder, then apply again.
- **A new calibration from your tuner:** double-click the new `.rescal` file, drop it on the window, or put
  it in the calibrations folder (**Settings → Open calibrations folder**). With several calibrations, pick
  one at the top right.

## Your data

Everything stays on your computer: imported calibrations, history and settings live in the app's data
folder (Windows `%APPDATA%\ResonanceFit`, macOS `~/Library/Application Support/ResonanceFit`, Linux
`~/.local/share/resonancefit`). Deleting the download doesn't delete them, and a newer version of the app
finds them automatically. The update check sends one anonymous request to GitHub for the latest
release; nothing about you, your IEMs or your calibration is sent.
