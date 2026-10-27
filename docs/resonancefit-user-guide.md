# ResonanceFit user guide

ResonanceFit turns the calibration of **your** IEMs into an EQ for **your** ears. Your IEMs were measured
on a coupler at several insertion depths; that is your *calibration*. Because everyone's ears are
different, the app asks you one thing: where the strongest treble peak, your *resonance*, sits in each
ear. You find it by ear in about a minute, choose the sound you want (a *target*), and the app builds a
separate EQ for your left and right ear.

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

To check by ear, press **≈ Pink noise + EQ**: pink noise plays through your EQ (the EQ switches on). Move
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
  compare tone, not loudness. Use a tone, a sweep or a song (see *Test songs*).
- **Save tuning:** keeps your current choices in **History** with an optional note.
- **Export for my EQ app…:** makes the files for the app you listen with (below).
- **Apply to Equalizer APO** (Windows, when Equalizer APO is installed): makes this tuning your
  computer's EQ for everything you play. Choose your headphone output once. The app keeps one entry,
  “Resonance EQ – your IEM”, in Equalizer APO and replaces it each time you apply. **Switch to:** applies
  any saved tuning. To turn it off, use **Settings → Turn the EQ off in Equalizer APO**.

### History

Every tuning you save, export or apply is listed with its date, target, resonances, upper resonance,
note and files. **Reapply** puts every control back exactly as it was, even if the target file is gone.

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

- **Test songs:** your tuner's suggested links plus your own songs and links. Songs play in the app
  through your EQ with the EQ on/off switch. Formats the app can't play open in your system's player.
- **Sound tools:** tone, sweep, full sweep and pink noise, volume, and Left/Both/Right.
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
