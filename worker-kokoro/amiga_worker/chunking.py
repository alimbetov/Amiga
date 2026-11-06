"""Deterministic sentence-based chunk planning.

Character limit is a conservative *planning* guard; engine adapters must
separately enforce post-phonemization token limits before inference.
"""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata

CHUNKER_VERSION = "sentence-v1"
_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+")
_CLAUSE = re.compile(r"(?<=[,;:，；：])\s+")


def normalize(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return " ".join(unicodedata.normalize("NFC", text).split())


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts = _CLAUSE.split(text)
    if len(parts) == 1:
        parts = text.split(" ")
    result: list[str] = []
    current = ""
    for part in parts:
        if len(part) > max_chars:
            if current:
                result.append(current)
                current = ""
            # A word exceeding the bound cannot be split safely.
            raise ValueError("word/clause exceeds bound; requires engine-aware segmentation")
        candidate = f"{current} {part}".strip()
        if len(candidate) > max_chars:
            if current:
                result.append(current)
            current = part
        else:
            current = candidate
    if current:
        result.append(current)
    return result


def chunks(text: str, max_chars: int = 240) -> list[str]:
    if max_chars < 16:
        raise ValueError("max_chars too small")
    text = normalize(text)
    if not text:
        raise ValueError("empty text")
    sentences = _BOUNDARY.split(text)
    pieces = [part for sent in sentences for part in _split_long(sent, max_chars)]
    output: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if len(candidate) > max_chars and current:
            output.append(current)
            current = piece
        else:
            current = candidate
    if current:
        output.append(current)
    return output


def fingerprint(text: str, *, locale: str, engine: str,
                model_sha256: str, voice_sha256: str, voice: str,
                speed: str = "1.0", pipeline_version: str = "pcm-v1",
                encoder_config: str = "mp3-default", phonemizer_version: str = "unknown",
                max_chars: int = 240) -> str:
    canonical = {
        "text": normalize(text), "locale": locale,
        "engine": engine, "modelSha256": model_sha256,
        "voiceSha256": voice_sha256, "voice": voice,
        "speed": str(speed), "chunkerVersion": CHUNKER_VERSION,
        "maxChars": max_chars, "pipelineVersion": pipeline_version,
        "encoderConfig": encoder_config, "phonemizerVersion": phonemizer_version,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
