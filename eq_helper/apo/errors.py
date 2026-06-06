"""Exceptions raised by the APO integration layer."""


class ApoError(Exception):
    """Base class for APO integration errors."""


class ApoNotFoundError(ApoError):
    """The Equalizer APO config directory could not be located."""


class ApoWriteError(ApoError):
    """A write to an APO config file failed."""
