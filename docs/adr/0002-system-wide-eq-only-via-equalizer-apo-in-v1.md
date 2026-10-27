# System-wide EQ only via Equalizer APO in v1

The customer app previews the EQ in-app (its own audio engine) on every OS, but
only applies EQ system-wide where Equalizer APO is detected on Windows. On
macOS and Linux it exports files and shows app-specific setup instructions.
Writing into other EQ apps (EasyEffects, CamillaDSP, eqMac, …) is deferred and
will be added one app at a time; APO-dependent features (profiles, preset
contents) are hidden when APO is absent.
