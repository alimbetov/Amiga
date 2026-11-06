"""Ordered speech chunk assembly and MP3 encoding for Amiga dev HTTP Worker.

The engine callback renders individual, independently validated PCM WAV files.
Exactly one final MP3 is encoded from joined PCM, never by concatenating MP3 bytes.
This local adapter is not the production durable chunk-executor.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

from amiga_worker.chunking import chunks

MAX_CHUNKS = 64
MAX_AUDIO_SECONDS = 600
MAX_TEXT_CHARS = 2000
BITRATE = "96k"


class AudioPipelineError(RuntimeError):
    pass


def assemble_wav(parts: list[Path], output: Path, silence_ms: int = 120) -> dict:
    if not parts or len(parts) > MAX_CHUNKS:
        raise AudioPipelineError("invalid chunk count")
    if not 0 <= silence_ms <= 1000:
        raise AudioPipelineError("invalid pause")
    # Validate before opening wave writer: closing a writer without headers
    # would mask our original missing-chunk error.
    for path in parts:
        if not path.is_file() or path.stat().st_size < 100:
            raise AudioPipelineError("missing or empty chunk")
    output.parent.mkdir(parents=True, exist_ok=True)
    spec = None
    total_frames = 0
    temporary = output.with_suffix(".assembling.wav")
    try:
        with wave.open(str(temporary), "wb") as target:
            for index, path in enumerate(parts):
                if not path.is_file() or path.stat().st_size < 100:
                    raise AudioPipelineError("missing or empty chunk")
                with wave.open(str(path), "rb") as source:
                    current = (source.getnchannels(), source.getsampwidth(),
                               source.getframerate(), source.getcomptype())
                    if current[0] not in (1, 2) or current[1] != 2 or current[2] < 8000 or current[3] != "NONE":
                        raise AudioPipelineError("unsupported PCM format")
                    if spec is None:
                        spec = current
                        target.setnchannels(current[0])
                        target.setsampwidth(current[1])
                        target.setframerate(current[2])
                    elif current != spec:
                        raise AudioPipelineError("PCM format mismatch")
                    frames = source.getnframes()
                    if frames <= 0:
                        raise AudioPipelineError("empty PCM chunk")
                    if index:
                        pause_frames = round(spec[2] * silence_ms / 1000)
                        target.writeframesraw(bytes(pause_frames * spec[0] * spec[1]))
                        total_frames += pause_frames
                    while block := source.readframes(16384):
                        target.writeframesraw(block)
                    total_frames += frames
                    if total_frames > MAX_AUDIO_SECONDS * spec[2]:
                        raise AudioPipelineError("audio duration exceeds limit")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        return {"sampleRate": spec[2], "channels": spec[0],
                "durationSeconds": round(total_frames / spec[2], 3),
                "chunkCount": len(parts)}
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def encode_mp3(wav_path: Path, target: Path, ffmpeg: str = "ffmpeg",
               ffprobe: str = "ffprobe") -> dict:
    if not wav_path.is_file():
        raise AudioPipelineError("assembled WAV missing")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part.mp3")
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                        "-i", str(wav_path), "-vn", "-codec:a", "libmp3lame",
                        "-b:a", BITRATE, "-f", "mp3", str(temp)],
                       check=True, capture_output=True, timeout=120)
        if not temp.is_file() or temp.stat().st_size <= 100:
            raise AudioPipelineError("empty encoded MP3")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
             "-of", "json", str(temp)],
            check=True, capture_output=True, timeout=30, text=True)
        parsed = json.loads(result.stdout)
        stream = parsed.get("streams", [{}])[0]
        duration = float(parsed.get("format", {}).get("duration", 0))
        if stream.get("codec_name") != "mp3" or not math.isfinite(duration) or not 0 < duration <= MAX_AUDIO_SECONDS + 1:
            raise AudioPipelineError("MP3 verification failed")
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, target)
        return {"codec": "mp3", "durationSeconds": round(duration, 3),
                "sampleRate": int(stream["sample_rate"]),
                "channels": int(stream["channels"])}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioPipelineError("MP3 encoding/verification failed") from exc
    finally:
        temp.unlink(missing_ok=True)


def render_mp3(text: str, destination: Path, render_chunk,
               max_chars: int = 240, silence_ms: int = 120,
               remove_intermediates: bool = True) -> dict:
    """render_chunk(text: str, output_wav: Path) -> None."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise AudioPipelineError("invalid text")
    if not isinstance(max_chars, int) or not 32 <= max_chars <= 400:
        raise AudioPipelineError("invalid chunk size")
    planned = chunks(text, max_chars=max_chars)
    if len(planned) > MAX_CHUNKS:
        raise AudioPipelineError("too many chunks")
    work = destination.parent / (destination.name + ".chunks")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    paths = []
    try:
        for index, piece in enumerate(planned):
            path = work / f"chunk-{index:04d}.wav"
            render_chunk(piece, path)
            paths.append(path)
        joined = work / "joined.wav"
        stats = assemble_wav(paths, joined, silence_ms=silence_ms)
        mp3 = encode_mp3(joined, destination)
        return {**mp3, "chunkCount": stats["chunkCount"],
                "pcmDurationSeconds": stats["durationSeconds"]}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if remove_intermediates:
            shutil.rmtree(work, ignore_errors=True)
