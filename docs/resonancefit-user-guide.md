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
received**, open or drop your `.rescal` file. A short walkthrough explains the steps. You can
reopen it from **Settings → Show the walkthrough again**. On Windows and Linux the app may also offer to
open `.rescal` files when you double-click them.

**Start quiet.** Test tones can be piercing at the resonance. The app starts at a low volume every time
and asks you to turn your volume down before the first sound. **Press Space at any time to stop all
sound.** The bar at the bottom of the window always shows the **Output** you're playing on, the
**Volume** and **Stop all sound**.

## The bar at the top

The bar at the top shows your IEM, who it was calibrated for and the accessories it was measured with
(point at the IEM's name for the coupler it was measured on). Amber always means *your ear*: your
resonance, the ear you're on, the card you chose.

- **Calibration** (next to the IEM's name): which of your calibrations you're tuning. The list is the calibrations in your
  `Calibrations` folder (plus any you opened from elsewhere): when your tuner sends a new calibration that
  replaces an old file, or you delete a file there, the old one leaves the list at the next launch.
  **⋯ → Remove this calibration…** takes the selected one out of the app; its file isn't touched, and
  opening it again brings it back with its tunings.
- **Target**: the sound you want. The one marked **(default)** was chosen for you by your tuner. You can
  try any other, or **⋯ → Import a target…** (TXT or CSV, frequency and level) you found elsewhere.
- **EQ on / off** (the switch with the light, or **E**): switches the EQ in the app on and off so you can
  compare. It's level-matched, so you compare tone, not loudness. It only changes what you hear in the
  app, not Equalizer APO.

## Find your resonance

**Turn off any other EQ first:** your music app's EQ, sound enhancements (Dolby, Windows or Realtek
effects…), Equalizer APO or Peace, and your dongle's EQ. Otherwise you hear it on top of this one and
find the resonance in the wrong place. The app names the EQ apps it finds on your computer. If your
own tuning is switched on in Equalizer APO, a box says so, with **Pause it while I tune** right in it (the
test's first screen offers the same). Once it's paused the box says **Paused in Equalizer APO**; when
you're done, **Apply to Equalizer APO** switches it back on with your new tuning. The app keeps checking,
so switching it on or off in Equalizer APO itself shows here within a few seconds.

### Find it for me (the guided test)

Press **Find it for me (a few minutes)**. A short screen reminds you to wear both IEMs as usual, keep
the volume low and turn other EQs off; press **Start the test**. While the test runs it has the window to
itself: only the output, volume and **Stop all sound** stay at the bottom; the calibration and target,
the tabs and **Tunings** come back when it ends. Then, for the left ear and then the
right (one ear only for a calibration made for both ears at once), a **smoothness check** in two parts.
Every card plays the same sound through a slightly different EQ, each placing your resonance a little
differently; you pick the one whose treble sounds smoothest.

1. **Overview: eight cards**, **1** to **8** in two rows (the numbers are also their keys), in order
   from an EQ made for a low resonance to one made for a high one. Going through them in order, the
   sound usually gets smoother card by card, then harsher again past the right one, which makes the
   smoothest easy to spot. Listen to them all and compare any two as often as you like. **Rule out** the ones that clearly sound worse (select a card and press **Rule out** or X, or
   right-click it): it fades and gets a ✕ but stays playable, and the same again brings it back. Then
   pick the smoothest of the rest: **Smoothest: 3**. Ruling out is optional, it just leaves fewer to
   compare.
2. **Pairs**: then a few rounds of just two cards, the lower one first. Which sounds smoother? Answer
   **Smoothest: 1** (or 2) or **Can't tell them apart**, which is a fine answer when they're close.

**Listen with Pink noise, Grey noise, Focused noise or Sweep** (switch any time, use any; keys N, G, F,
W): with noise, the smoothest card has no harsh, hissy or dull spot; with **Sweep**, a slow sweep
through the treble, it **stays even**, with no swell or dip as it passes. Grey noise is pink noise with
the treble (and bass) lifted the way hearing needs it, so a harsh or dull spot high up stands out more.
**Focused noise** is grey noise kept to about an octave and a half around the cards (fading out gently
beyond), so everything you hear is where they differ.

How it decides: your calibration's model knows how rough each card should sound for every possible
resonance, so every answer counts for the resonances it fits and against the rest. No single answer
decides anything: a slip only counts a little and later answers outvote it. Each pair is the one whose
answer should tell the most, and the ear is done when your answers point to one place about 100 Hz wide
(usually 8–10 rounds, at most 14). The left ear leans on your calibration's measurements at first, the
right ear on the left ear's result, but both start with the overview, so neither can get stuck in the
wrong place. Without the model (rare) each ear is the older three-card smoothness check: it starts wide
around your calibration's guess and closes in.

The cards never show frequencies, and the moving bars on the playing card only follow how loud the
sound is, the same for every card, so you choose by ear. Every card plays at the same loudness: the
app turns the louder ones down to match the quietest, for the sound you're using, so a card never
wins just by being louder. You can answer whenever you're ready; a ✓
marks the cards you've listened to. The sound you pick (pink, grey, focused or sweep) stays the same
from round to round and for the upper treble, until you change it.

When an ear is done the app shows its result, e.g. **Left resonance: 7,640 Hz**, and sets it in the
graph and the EQ exactly as the slider would. **Back** takes back your last answer; **✕ Stop the test**
leaves it and keeps what's been found so far (**Cancel** on the first screen).

Without a starting point from the calibration (rare), each ear begins with a **louder test** instead:
two cards, each a narrow band of noise at the same level; answer **Louder: 1** (or 2) or **About the
same**, about seven rounds, then the smoothness check from what it found. If those answers point to more
than one place, the app offers **Do this ear again**, **Use it anyway** or **Stop the test**.

**Upper treble (optional).** After both ears the app offers **Fine-tune my upper treble** or **Skip, I'm
done**. There's no louder test up there: above about 12 kHz hearing falls off steeply and differs a lot
between people, so a high sound seeming quieter says more about your ears than about the IEM. Instead the
app starts from where your calibration predicts the upper peak for the resonance you just found (it sits
at about twice that frequency). For each ear:

1. **Overview**: eight cards, **1** to **8** in order, EQs that differ only in the highest treble,
   from an upper peak set low to one set high, around the prediction (never further than the EQ can
   move it), with whichever sound you were using. Pick the smoothest and rule out the clearly worse
   ones as in the first part. With the **sweep**, your hearing's fall-off only makes it fade smoothly
   as it rises, while a misplaced peak leaves a swell or dip as it passes. If all
   eight sound the same, answer **Can't tell them apart**: that ear's upper treble stays as calibrated.
   That's common and nothing is lost: what you can barely hear matters little for your EQ.
2. **Smoothness check**, a few rounds of three cards around your pick, closer together each time.

A found upper treble switches the **Upper treble** slider to **Manual** with that value.

Keys: **1**–**8** pick and play a card, **P** play/pause, **Enter** always confirms the selected card (whatever has focus), **S**
is *About the same* / *Can't tell them apart*, **X** rules the selected card out (in the overview),
**N** / **G** / **F** / **W** pink, grey or focused noise, or the sweep in the smoothness check,
**Backspace** goes back, **Space** stops all sound.

### Adjust by hand

The slider and its sound buttons are always under **Find it for me**: use them to find the resonance
yourself, or to fine-tune what the test found:

1. Put both IEMs in as you normally wear them, with your usual tips.
2. On the **Left** tab, press **▶ Play tone**. The tone plays in your left ear only, and a dotted line on
   the graph shows the frequency that's playing. **Centre** plays in both ears, and moving the slider
   there moves both ears' resonance together (the graph shows the left ear). If your ears have different
   values, the app asks first: **Set both the same** or **Keep them different**.
3. Move the slider, type a frequency or drag the orange **Resonance** line on the graph until the tone
   sounds **loudest or most piercing**. That's your left resonance. **Sweep around it** plays a slow
   sweep around that frequency if that's easier to judge (the dotted line follows the sweep). The
   resonance is where the sweep gets loudest.
4. Do the same on the **Right** tab.

**〰 Full sweep** plays a slow sweep from 20 Hz to 20 kHz. Each sound button starts its sound and stops
it when you press it again (it reads **■ Stop tone** while playing). **Space** stops whatever plays and,
pressed again, plays the last sound again (as in EQ Helper); **Stop all sound** at the bottom stops
everything.

To check by ear, press **≈ Pink noise + EQ** or **≈ Grey noise + EQ**: the noise plays through your EQ
(the EQ switches on). Move
the resonance slowly while it plays; where it's right, the treble sounds smooth and even, with no harsh,
hissy or dull spot.

The graph shows **Your IEM** (what the calibration predicts for your setting), the **Target** and
**With your EQ**. Point at the graph to see the frequency under the mouse; click or drag on it to hear a
tone at that frequency. If you land outside what was measured for your IEMs, the app
tells you so: the EQ there is an estimate, which is fine if it sounds right.

Both ears' results are always at the top of the panel: the ear you're on in amber, the other beside it.
Switch ears with **Left**, **Right** and **Centre** next to them (or **L**, **R**, **C**). Every sound
has a key: **T** tone, **S** sweep around it, **F** full sweep, **P** pink noise, **G** grey noise; the
list is under the sliders.

**Upper treble (advanced, optional).** The **Upper treble** slider under the resonance slider starts on
**Auto**: it shows where your calibration puts the upper peak for your resonance, so it moves as you move
the resonance, and the EQ keeps it as calibrated. Switch to **Manual** only if you hear a second, higher
peak (above about 10.5 kHz) that still bothers you: a pink **Upper** line appears on the graph, and the
same sound buttons play at whichever one you last moved (or pick **Resonance** or **Upper treble** next
to them). Move it to where you hear the peak, per ear. On **Manual** each ear keeps its own value (an ear
you haven't moved keeps the one it showed), so moving the resonance never moves it. **Auto** puts both
back as calibrated.

## Tunings

The panel on the right lists your saved tunings, each on two lines you can read at a glance: its name,
then `L 8.0k · R 7.4k · PEQdB Diamond` (the resonances and target). The tuning applied to Equalizer APO
is tagged **in use**. Point at one for everything else (when it was saved, its files).

- **Click a tuning to switch to it**: every control goes back exactly as it was, even if its target file
  is gone.
- Moving anything never changes a saved tuning. What you have instead appears at the top in amber,
  **Current · unsaved**. While you try saved tunings it stays there as **Last unsaved**: click it to go
  back.
- **Save** keeps the tuning you're hearing, with a name if you type one (for example “Bright tips”);
  without one it's named from its target. Saving a tuning that's already there just renames it.
- Right-click a saved tuning (or press F2 / Delete) to rename or delete it.
- **Export for my EQ app…:** makes the files for the app you listen with (below).
- **Apply to Equalizer APO** (Windows, when Equalizer APO is installed): makes this tuning your
  computer's EQ for everything you play. Choose your headphone output once. The app keeps one entry,
  “Resonance EQ – your IEM”, in Equalizer APO and replaces it each time you apply. It's a convolution
  filter with a file for each sample rate, so it keeps working if your device's rate changes (any
  unusual rate gets the same EQ as GraphicEQ). To put a saved tuning there, click it, then Apply; the one
  applied is tagged **in use**, and applying or exporting keeps it in the list. To turn it off,
  use **Settings → Turn the EQ off in Equalizer APO**. If Peace is installed, turn its EQ off: it's a
  front-end for Equalizer APO and you'd hear both.
- **Apply to EasyEffects** (Linux, when EasyEffects is installed): the same for EasyEffects. The app
  writes the preset “Resonance EQ - your IEM” (one Convolver) and loads it. Loading a preset replaces
  the effects running in EasyEffects; pick your own preset there to go back. If EasyEffects isn't
  running, start it and choose that preset. **Settings → Turn the EQ off in EasyEffects** bypasses it.

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

**Predicted response** exports your IEM's predicted curves (left and right, optionally with the EQ) for squig.link or PEQdB studio.

## Other tabs

- **Test audio:** your tuner's suggested links plus whatever you add: audio files from your computer
  (any recording you know well) or web links. **+ Add audio files…** and **Add link…** sit in the middle
  of the list while it's empty. Files play in the app
  through your EQ with the **⏻ EQ** switch. Formats the app can't play open in your system's player.
- **Settings:** **Appearance** (System, Light or Dark; System follows your computer), output device,
  Equalizer APO, the calibrations and data folders, the walkthrough, and
  **Version and updates** (below).
- **About:** your calibration sheet, contact and licences.

## Updates

When the app opens, it checks once a day whether a newer version has been published. If today's check
already worked, it doesn't check again until tomorrow; if it couldn't check (no internet, GitHub
unreachable), it tries again the next time you open it. There is no background service: nothing runs
while the app is closed.

When a new version is out, it downloads in the background; a strip at the top of the window shows the
progress. Once it's downloaded, the strip offers **Install and restart**: the app closes, the new version
replaces it and opens. If you don't, it installs when you close the app (and doesn't open again). Every
download is checked against the release's checksums first, and if installing fails your current version
is put back and the strip says what went wrong. **Not now** hides the strip; the update still installs
when you close the app.

Only the app itself is replaced: the `Calibrations` and `Targets` folders and the calibration sheets next
to it, and your history and settings, are kept.

**Settings → Version and updates** shows your version, the latest one, when the last successful check
happened and **Check for updates now**. You can turn off the check when the app opens there; checking by
hand still works.

Where the app can't replace itself, the strip offers **Download** instead: then download the new version
and replace the old app yourself. That's the case when its folder can't be written to, on macOS when
the app runs from the disk image or Downloads (move it to Applications), and on Linux when it isn't run
as the AppImage.

## Troubleshooting

- **No sound:** check **Output** at the bottom of the window and your system volume. Press Space and
  try again.
- **The EQ sounds wrong:** check you're wearing the tips listed on your calibration sheet, then find your
  resonance again. Fit matters: re-seat the IEMs and find it again.
- **One side louder in a shared-only app:** set that app's balance as shown after exporting.
- **“Equalizer APO couldn't be changed”:** follow the steps shown, which give the Users group permission
  to change Equalizer APO's config folder, then apply again.
- **A new calibration from your tuner:** double-click the new `.rescal` file, drop it on the window, or put
  it in the calibrations folder (**Settings → Open calibrations folder**). With several calibrations, pick
  one at the top right; **Remove…** takes one out.

## Your data

Everything stays on your computer: imported calibrations, history and settings live in the app's data
folder (Windows `%APPDATA%\ResonanceFit`, macOS `~/Library/Application Support/ResonanceFit`, Linux
`~/.local/share/resonancefit`). Deleting the download doesn't delete them, and a newer version of the app
finds them automatically. The update check sends one anonymous request to GitHub for the latest
release, and downloading an update fetches only the release files; nothing about you, your IEMs or your
calibration is sent.
