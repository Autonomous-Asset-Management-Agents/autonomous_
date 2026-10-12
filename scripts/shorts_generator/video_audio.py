import logging
import math
import struct
import wave

logger = logging.getLogger(__name__)


def synthesize_jingle(filepath: str, duration: float = 20.3):
    """Synthesizes a piano jingle and background track in the minimalist style of Philip Glass (Glassworks / Opening).
    Features rolling piano arpeggios (triplets) with long sustain, creating a hypnotic, flowing acoustic feel.
    """
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    # Use a float buffer to mix notes
    buffer = [0.0] * num_samples

    # Define chord progression (F minor, Db major, Eb major, C minor, F minor)
    # Each chord lasts about 4.0 seconds
    chords = [
        # F minor: F3, C4, Ab4, C5, Ab4, C4
        [174.61, 261.63, 415.30, 523.25, 415.30, 261.63],
        # Db major: Db3, Ab3, F4, Ab4, F4, Ab3
        [138.59, 207.65, 349.23, 415.30, 349.23, 207.65],
        # Eb major: Eb3, Bb3, G4, Bb4, G4, Bb3
        [155.56, 233.08, 392.00, 466.16, 392.00, 233.08],
        # C minor: C3, G3, Eb4, G4, Eb4, G3
        [130.81, 196.00, 311.13, 392.00, 311.13, 196.00],
        # F minor: F3, C4, Ab4, C5, Ab4, C4
        [174.61, 261.63, 415.30, 523.25, 415.30, 261.63],
    ]

    # Note speed: one note every 0.22 seconds (slower, more relaxed arpeggio)
    note_interval = 0.22
    note_duration = 2.4  # Long sustain to simulate piano pedal

    total_notes = int(duration / note_interval)

    # Piano key synthesis helper
    def add_piano_note(freq, start_time):
        start_sample = int(start_time * sample_rate)
        if start_sample >= num_samples:
            return

        # Determine actual duration of this note
        dur = note_duration
        if start_time + dur > duration:
            dur = duration - start_time

        note_samples = int(dur * sample_rate)

        for k in range(note_samples):
            idx = start_sample + k
            if idx >= num_samples:
                break

            t = k / sample_rate

            # Piano-like envelope: instant attack, exponential decay
            # Attack over 0.003s
            if t < 0.003:
                env = t / 0.003
            else:
                env = math.exp(-(t - 0.003) / 0.5)

            # Dampen slightly towards the absolute end of the video
            if (start_time + t) > duration - 1.0:
                env *= (duration - (start_time + t)) / 1.0

            # Synthesize tone with harmonics to mimic a warm piano
            # Fundamental + 2nd, 3rd, 4th, 5th, 6th harmonics
            val = (
                math.sin(2.0 * math.pi * freq * t) * 1.0
                + math.sin(2.0 * math.pi * (freq * 2) * t) * 0.45
                + math.sin(2.0 * math.pi * (freq * 3) * t) * 0.25
                + math.sin(2.0 * math.pi * (freq * 4) * t) * 0.15
                + math.sin(2.0 * math.pi * (freq * 5) * t) * 0.08
                + math.sin(2.0 * math.pi * (freq * 6) * t) * 0.04
            )

            # Soft clipping / saturation for a warmer sound
            val = math.tanh(val * 0.7)

            buffer[idx] += val * env * 0.18

    # Generate rolling arpeggio
    for i in range(total_notes):
        start_time = i * note_interval
        chord_idx = min(int(start_time / 5.0), len(chords) - 1)
        chord = chords[chord_idx]
        note_in_chord = i % len(chord)
        freq = chord[note_in_chord]

        # Play note
        add_piano_note(freq, start_time)

        # Occasionally double the note an octave higher for highlights (minimalist melody)
        if i % 12 == 0:
            add_piano_note(freq * 2, start_time)
        elif i % 12 == 6:
            # Accent with another note in the chord
            accent_freq = chord[(note_in_chord + 2) % len(chord)] * 2
            add_piano_note(accent_freq, start_time)

    # Normalize audio buffer to avoid clipping and optimize volume
    max_val = max(abs(x) for x in buffer)
    if max_val > 0.0:
        scale = 0.85 / max_val
        buffer = [x * scale for x in buffer]

    # Convert float buffer to 16-bit signed PCM WAV data
    data = bytearray()
    for val in buffer:
        sample = int(val * 32767)
        data.extend(struct.pack("<h", sample))

    with wave.open(filepath, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    logger.info(
        "Synthesized Philip Glass-style piano track (Glassworks / Opening) to %s",
        filepath,
    )


# --- DATA EXTRACTION ---
