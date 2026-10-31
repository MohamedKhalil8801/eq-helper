---
target: ResonanceFit desktop app
total_score: 25
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
timestamp: 2026-10-31T06-37-06Z
slug: resonancefit-ui
---
Method: dual-agent (A: design review · B: detector + mechanical checks)

## Design Health Score
| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | APO notice excellent; EQ pill low-contrast; test progress bar unlabeled |
| 2 | Match System / Real World | 3 | "Grey noise + EQ", "Centre", Auto/Manual unexplained |
| 3 | User Control and Freedom | 3 | Back/Stop/Space good; History delete no undo |
| 4 | Consistency and Standards | 2 | Guided test runs a second palette (purple #7c6cff); 3+ button styles; segment/walkthroughButton unstyled |
| 5 | Error Prevention | 3 | Double-EQ warning good; Remove… beside the calibration combo |
| 6 | Recognition Rather Than Recall | 2 | History = 6–9 line text blocks; card order low→high not shown |
| 7 | Flexibility and Efficiency | 3 | Test has full shortcuts; Tune page none |
| 8 | Aesthetic and Minimalist Design | 1 | ~25 controls on Tune; 7-option sound row; file names in History |
| 9 | Error Recovery | 2 | Few error states seen |
| 10 | Help and Documentation | 3 | Inline help + walkthrough + guide; long |
| **Total** | | **25/40** | **Acceptable** |

## Design Specificity Verdict
Graph and 8-card test are product-specific; the guided test's dark stage is the best idea. The rest is stock Qt: one blue for all emphasis, 1px grey card borders, single font weight, dated tab bar. Header takes ~17% height for rarely-changing data; the resonance readout sits below the fold. Detector: directory scan read no .py (not a real clean); common.py:178 side-tab accent border (history selected row). Contrast: borders fail 3:1 both themes (2.03 dark / 1.69 light); disabled primary text fails; primary-as-link 4.46 dark; checked primary 4.13. Guided view hard-codes its own palette (guided_widgets.py:38-47, #7c6cff accent 3.86 on white). No :focus rules remain; history has outline:none.

## Priority Issues
- [P0] No visible keyboard focus after removing outlines; Enter acts on focused button in the test. Fix: neutral 2px focus border, keyboard-focus only. (harden)
- [P1] Tune has no single focus; result below fold; header too tall. Fix: 56px toolbar header, L/R result rail at top, provenance into a popover. (layout)
- [P1] Control strip 7+ options + paragraph. Fix: Play tone primary, other sounds in one menu, upper treble under Advanced, one-line hint. (distill)
- [P2] Two palettes / flat single blue / failing borders. Fix: one token set for app + guided test, one filled primary per region, tinted segments, borders ≥3:1 or fill-based elevation. (colorize/quieter)
- [P2] History unscannable. Fix: delegate rows: name + in-use pill, "L 8.0k · R 7.4k · target", relative date, files in tooltip, mini EQ sparkline. (clarify)

## Persona Red Flags
Alex: no Tune shortcuts (ears, tone, EQ), History not comparable, Switch-to duplicates Reapply.
Jordan: first sees Calibration/Target/Remove/Import; Centre, Grey noise unexplained; 8 blank cards don't show low→high; Smoothest disabled without reason.
Sam: invisible focus; disabled buttons ~1.7–3.1:1; ruled-out vs chosen may lean on colour; graph has no text alternative.

## Minor Observations
Byline in header; output picker twice; Settings bottom third empty; Test audio full-width EQ pill; legend overlaps plot; History filename truncation inconsistent; progress bar unlabeled; 7 font sizes mixed relative/absolute.

## Questions
Should the manual slider be an expert view behind the guided result? Does provenance need to be on every screen? With an always-visible L/R readout, are ear tabs needed?

## Direction proposed: "Listening Room"
Near-black instrument look; amber = your ear (#F2A93B), target cyan #4FB3D9, corrected green #3CC79A; surfaces #0E0F11/#16181B/#1E2125, fill-based elevation, no default borders; Segoe UI Variable, 32/600 tabular Hz; slim header with hardware-style EQ switch; graph as hero with fader-cap resonance handle and shaded 5–10 kHz band; cards show a tiny notch glyph at their position; result reveal at the end.
