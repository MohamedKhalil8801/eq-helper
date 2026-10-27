"""Everything the customer app's windows share, built once at launch."""

from __future__ import annotations

from dataclasses import dataclass, field

from eq_helper.calibration.branding import Branding, load_branding

from .library import CalibrationLibrary, ScanReport
from .paths import AppPaths
from .settings import Settings
from .targets import TargetLibrary


@dataclass
class AppContext:
    paths: AppPaths
    settings: Settings
    library: CalibrationLibrary
    targets: TargetLibrary
    branding: Branding = field(default_factory=load_branding)
    scan_report: ScanReport = field(default_factory=ScanReport)

    @classmethod
    def create(cls, paths: AppPaths | None = None, branding: Branding | None = None) -> "AppContext":
        """Build the context and run the launch scans (calibrations and targets)."""
        paths = paths or AppPaths.detect()
        library = CalibrationLibrary(paths)
        report = library.scan()
        targets = TargetLibrary(paths)
        targets.scan()
        return cls(paths, Settings(paths.settings_path), library, targets,
                   branding or load_branding(), report)
