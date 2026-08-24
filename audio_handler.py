import sounddevice as sd
import numpy as np
import wave
import tempfile
import os
from faster_whisper import WhisperModel

print("Initializing Jarvis systems (Ears online)...")
# Using 'cuda' and 'float16' for RTX 4070. The model will be downloaded once on the first run.
model = WhisperModel("base", device="cuda", compute_type="float16")

def record_audio(duration=5, fs=16000):
    """Records audio from the microphone for a specified duration."""
    print(f"\n[JARVIS LISTENING - Speak for {duration} seconds]...")
    
    # Record 1-channel (Mono) audio at 16kHz
    recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
    sd.wait() # Wait until the recording is finished
    
    print("[RECORDING COMPLETE - Transcribing...]")
    return recording, fs

def save_temp_audio(recording, fs):
    """Saves the recorded audio array to a temporary WAV file."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    with wave.open(temp_file.name, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(fs)
        wf.writeframes(recording.tobytes())
    return temp_file.name

def transcribe():
    """Records audio and returns the transcribed text using faster-whisper."""
    recording, fs = record_audio(duration=5)
    temp_path = save_temp_audio(recording, fs)
    
    # Transcribe the audio file (beam_size=5 improves accuracy)
    segments, info = model.transcribe(temp_path, beam_size=5)
    
    text = "".join([segment.text for segment in segments])
    
    # Clean up the temporary file to prevent clutter
    os.remove(temp_path)
    
    return text.strip()

if __name__ == "__main__":
    result = transcribe()
    print(f"\nJarvis Heard: {result}")