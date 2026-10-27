# Resonance EQ

Resonance EQ is a calibration service: an IEM is measured on the operator's
IEC-711 coupler, and the customer receives an app that turns those
measurements into a personal L/R EQ for any target, without measuring anything
themselves.

## People and apps

**Operator**:
The person who measures IEMs and produces calibrations for customers.
_Avoid_: Admin, seller

**Customer**:
The person a calibration is made for and who uses the customer app.
_Avoid_: User, client

**Admin app**:
EQ Helper, used by the operator to create calibrations and package them for customers.
_Avoid_: Admin panel, generator

**Customer app**:
The app a customer receives; it only does resonance tuning for the calibrations it holds.
_Avoid_: Generated exe, player

## Calibration

**Calibration**:
One measuring session by the operator: one customer, one IEM, the accessories used, the date, and a measurement set for each side. This is the product being sold.
_Avoid_: Profile, calibration profile, measurement

**Accessory**:
Anything besides the IEM that affects the measured response (ear tips, DAC/amp, cable, filters), recorded as either supplied by the customer or the operator's own.
_Avoid_: Gear, equipment

**Calibration sheet**:
A one-page summary of a calibration (customer, IEM, accessories, date, plotted fits) given to the customer as a record of the service.
_Avoid_: Certificate, report

**Measurement set**:
All fits measured for one side of the IEM within a calibration.
_Avoid_: Measurement folder, measurements

**Fit**:
A single coupler measurement at one insertion depth; a measurement set runs from fully inserted until the IEM pops out.
_Avoid_: Measurement, sample, insertion

## Tuning

**Primary resonance**:
The strongest treble peak between 6 and 9 kHz, found by ear separately for each side.
_Avoid_: Resonance peak, first peak

**Upper resonance**:
The optional higher treble peak (10.5–18 kHz) the customer can place by ear to personalize the upper-treble correction.
_Avoid_: Second resonance peak, personal treble, high-treble fit

**Predicted response**:
The coupler response the calibration predicts for a side at a given primary resonance.
_Avoid_: Predicted measurement, prediction

**Target**:
The frequency-response curve the EQ makes the predicted response match.
_Avoid_: Target curve, house curve

**Default target**:
The target a calibration opens with (initially PEQdB Diamond).

**Tuning**:
One saved set of customer choices: primary resonance for each side, upper resonance, and target. Re-tuning makes a new tuning; it never changes the calibration.
_Avoid_: Attempt, version, recalibration, profile

**History**:
The list of saved tunings for a calibration, any of which can be reapplied.

**Export**:
Files produced from a tuning: an Equalizer APO EQ file, convolution impulse responses, or predicted-response files.
_Avoid_: Output, generated EQ
