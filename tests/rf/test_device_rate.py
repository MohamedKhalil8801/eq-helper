"""Export sample rate comes from the device's real (WASAPI shared-mode) rate on Windows, not MME's 44.1 kHz."""

from resonancefit.audio import shared_mode_rate


class FakeSd:
    def __init__(self, with_wasapi=True):
        self.apis = [{"name": "MME", "default_output_device": 1}]
        self.devices = [
            {"name": "Microphone", "hostapi": 0, "max_output_channels": 0, "default_samplerate": 44100.0},
            {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0, "max_output_channels": 2, "default_samplerate": 44100.0},
            {"name": "Headphones (Apple USB-C to 3.5m", "hostapi": 0, "max_output_channels": 2, "default_samplerate": 44100.0},
        ]
        if with_wasapi:
            self.apis.append({"name": "Windows WASAPI", "default_output_device": 4})
            self.devices += [
                {"name": "Speakers (Realtek(R) Audio)", "hostapi": 1, "max_output_channels": 2, "default_samplerate": 48000.0},
                {"name": "Headphones (Apple USB-C to 3.5mm Headphone Jack Adapter)", "hostapi": 1,
                 "max_output_channels": 2, "default_samplerate": 96000.0},
            ]

    def query_hostapis(self):
        return self.apis

    def query_devices(self):
        return self.devices


def test_default_output_uses_wasapi_rate():
    assert shared_mode_rate(FakeSd(), None) == 96000   # WASAPI's default output is the headphones here


def test_chosen_device_matched_by_name_even_when_mme_truncates_it():
    assert shared_mode_rate(FakeSd(), 1) == 48000
    assert shared_mode_rate(FakeSd(), 2) == 96000


def test_no_wasapi_means_no_answer():
    assert shared_mode_rate(FakeSd(with_wasapi=False), None) is None
