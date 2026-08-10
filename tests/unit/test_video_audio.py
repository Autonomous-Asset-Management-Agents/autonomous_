# R6-2c (#1674): synthesize_jingle() was 100% non-functional on main —
# NameError on `struct` (never imported), then on `logger` (never defined).
# Regression test: it must run and write a valid mono 16-bit WAV.

import wave

from scripts.shorts_generator.video_audio import synthesize_jingle


def test_synthesize_jingle_writes_valid_wav(tmp_path):
    out = tmp_path / "jingle.wav"
    synthesize_jingle(str(out), duration=0.3)  # short duration keeps the test fast
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 44100
        assert w.getnframes() > 0
