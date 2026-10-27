"""In-app test-song playback through the per-ear EQ, with A/B and seeking."""

import numpy as np
import pytest
import soundfile as sf

from eq_helper.audio.engine import AudioEngine
from eq_helper.audio.shared_state import ChannelMode
from eq_helper.audio.stereo_fir import gain_fir
from resonancefit.playback import SongPlayer, UnsupportedFile, can_play, decode

FS = 48000


def _tones(path, rate=44100, seconds=1.0, channels=2):
    t = np.arange(int(rate * seconds)) / rate
    data = np.c_[0.4 * np.sin(2 * np.pi * 440 * t), 0.2 * np.sin(2 * np.pi * 1000 * t)]
    sf.write(path, data[:, :channels] if channels == 2 else data[:, :1], rate)
    return path


def _amp(x, hz, fs=FS):
    t = np.arange(len(x)) / fs
    basis = np.c_[np.sin(2 * np.pi * hz * t), np.cos(2 * np.pi * hz * t)]
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    return float(np.hypot(*coef))


def test_decode_resamples_to_device_rate_and_keeps_stereo(tmp_path):
    data = decode(_tones(tmp_path / "a.wav"), FS)
    assert data.dtype == np.float32 and data.shape[1] == 2
    assert abs(data.shape[0] - FS) <= 2
    assert _amp(data[4000:40000, 0], 440) == pytest.approx(0.4, rel=0.01)
    assert _amp(data[4000:40000, 1], 1000) == pytest.approx(0.2, rel=0.01)


def test_mono_files_play_on_both_ears_and_flac_works(tmp_path):
    data = decode(_tones(tmp_path / "m.flac", rate=48000, channels=1), FS)
    np.testing.assert_array_equal(data[:, 0], data[:, 1])


def test_supported_and_unsupported_formats(tmp_path):
    assert can_play("x.wav") and can_play("x.FLAC") and can_play("x.mp3") and can_play("x.opus")
    assert not can_play("x.m4a") and not can_play("x.wma") and not can_play("x.aac")
    with pytest.raises(UnsupportedFile, match="system player"):
        decode(tmp_path / "song.m4a", FS)
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFFnope")
    with pytest.raises(UnsupportedFile):
        decode(broken, FS)


@pytest.fixture
def player():
    engine = AudioEngine(sample_rate=FS, block_size=512)
    engine.set_volume(1.0)
    engine.set_channel_mode(ChannelMode.BOTH)
    return SongPlayer(engine)


def _blocks(engine, n):
    return np.concatenate([engine.process_block(512) for _ in range(n)])


def test_plays_stereo_through_engine_pause_seek_and_end(player, tmp_path):
    song = player.load(_tones(tmp_path / "s.wav", rate=48000, seconds=0.5)).result(timeout=10)
    assert song.duration == pytest.approx(0.5, abs=0.01)
    player.play()
    out = _blocks(player.engine, 20)
    assert _amp(out[1000:9000, 0], 440) == pytest.approx(0.4, rel=0.02)
    assert _amp(out[1000:9000, 1], 1000) == pytest.approx(0.2, rel=0.02)
    assert _amp(out[1000:9000, 0], 1000) < 0.01  # no channel bleed
    player.pause()
    assert player.paused and not np.any(_blocks(player.engine, 2))
    position = player.position
    player.play()
    _blocks(player.engine, 1)
    assert player.position == pytest.approx(position + 512 / FS, abs=1e-6)
    player.seek(0.1)
    assert player.position == pytest.approx(0.1, abs=1 / FS)
    _blocks(player.engine, 60)
    assert player.finished and not np.any(_blocks(player.engine, 1))
    player.stop()
    assert not player.playing


def test_eq_ab_switches_without_restart(player, tmp_path):
    player.load(_tones(tmp_path / "s.wav", rate=48000, seconds=2.0)).result(timeout=10)
    player.set_eq_firs({"L": gain_fir(-6), "R": gain_fir(-6)}, {"L": gain_fir(0), "R": gain_fir(0)})
    player.set_eq_enabled(True)
    player.play()
    on = _blocks(player.engine, 30)[-8000:, 0]
    before = player.position
    player.set_eq_enabled(False)
    off = _blocks(player.engine, 30)[-8000:, 0]
    assert player.position > before  # kept playing, no restart
    assert _amp(off, 440) / _amp(on, 440) == pytest.approx(10 ** (6 / 20), rel=0.02)


def test_open_in_system_player_for_unsupported(player, tmp_path):
    opened = []
    player.opener = opened.append
    path = tmp_path / "song.m4a"
    path.write_bytes(b"")
    future = player.load(path)
    with pytest.raises(UnsupportedFile):
        future.result(timeout=10)
    player.open_externally(path)
    assert opened == [path]
