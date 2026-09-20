from __future__ import annotations

import math
import sys
import wave
from array import array
from pathlib import Path
from typing import Any


DEFAULT_PROFILES = {
    "natural": {
        "max_sentence_gap_frames": 15,
        "join_handle_seconds": 0.30,
        "tail_handle_seconds": 0.55,
        "waveform_search_seconds": 0.20,
        "speech_protection_seconds": 0.05,
        "quiet_threshold_dbfs": -38.0,
        "description": "保留较充足呼吸和句间节奏",
    },
    "compact": {
        "max_sentence_gap_frames": 15,
        "join_handle_seconds": 0.20,
        "tail_handle_seconds": 0.50,
        "waveform_search_seconds": 0.16,
        "speech_protection_seconds": 0.04,
        "quiet_threshold_dbfs": -36.0,
        "description": "自然紧凑，当前口播默认",
    },
    "energetic": {
        "max_sentence_gap_frames": 15,
        "join_handle_seconds": 0.12,
        "tail_handle_seconds": 0.35,
        "waveform_search_seconds": 0.12,
        "speech_protection_seconds": 0.035,
        "quiet_threshold_dbfs": -34.0,
        "description": "强节奏，仅适合明确要求的短视频",
    },
}


def pcm_rms(raw: bytes, width: int) -> float:
    if not raw:
        return 0.0
    if width == 1:
        samples = [value - 128 for value in raw]
    elif width == 2:
        values = array("h")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        samples = values
    elif width in (3, 4):
        samples = [
            int.from_bytes(raw[index:index + width], "little", signed=True)
            for index in range(0, len(raw) - width + 1, width)
        ]
    else:
        raise ValueError(f"unsupported PCM sample width: {width}")
    return math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples)))


def load_frame_levels(wav_path: Path, video_fps: int) -> list[float]:
    """Return one dBFS RMS value per video frame."""
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames_per_video_frame = sample_rate / video_fps
        levels: list[float] = []
        cursor = 0.0
        while wav.tell() < wav.getnframes():
            next_cursor = cursor + frames_per_video_frame
            sample_count = max(1, round(next_cursor) - round(cursor))
            raw = wav.readframes(sample_count)
            if not raw:
                break
            rms = pcm_rms(raw, width)
            maximum = float((1 << (8 * width - 1)) - 1)
            levels.append(-96.0 if rms == 0 else 20.0 * math.log10(rms / maximum))
            cursor = next_cursor
        return levels


def protected_frames(words: list[dict[str, Any]], fps: int, padding_seconds: float) -> set[int]:
    protected: set[int] = set()
    padding = max(0, math.ceil(padding_seconds * fps))
    for word in words:
        start = max(0, math.floor(float(word["start_seconds"]) * fps) - padding)
        end = math.ceil(float(word["end_seconds"]) * fps) + padding
        protected.update(range(start, end + 1))
    return protected


def nearest_quiet_frame(
    proposed: int, levels: list[float], protected: set[int], search_frames: int,
    threshold_dbfs: float,
) -> tuple[int, float | None]:
    if not levels:
        return proposed, None
    for distance in range(search_frames + 1):
        candidates = [proposed] if distance == 0 else [proposed - distance, proposed + distance]
        valid = [
            frame for frame in candidates
            if 0 <= frame < len(levels) and frame not in protected and levels[frame] <= threshold_dbfs
        ]
        if valid:
            selected = min(valid, key=lambda frame: levels[frame])
            return selected, levels[selected]
    return proposed, levels[proposed] if 0 <= proposed < len(levels) else None


def refine_boundaries(
    start_frame: int, end_frame: int, levels: list[float], words: list[dict[str, Any]],
    fps: int, profile: dict[str, Any], total_frames: int,
) -> tuple[int, int, dict[str, Any]]:
    protected = protected_frames(words, fps, float(profile["speech_protection_seconds"]))
    search = round(float(profile["waveform_search_seconds"]) * fps)
    threshold = float(profile["quiet_threshold_dbfs"])
    refined_start, start_dbfs = nearest_quiet_frame(start_frame, levels, protected, search, threshold)
    refined_end, end_dbfs = nearest_quiet_frame(end_frame, levels, protected, search, threshold)
    if refined_start >= refined_end:
        refined_start, refined_end = start_frame, end_frame
    refined_start = max(0, min(refined_start, total_frames))
    refined_end = max(0, min(refined_end, total_frames))
    return refined_start, refined_end, {
        "proposed_start_frame": start_frame,
        "proposed_end_frame": end_frame,
        "refined_start_frame": refined_start,
        "refined_end_frame": refined_end,
        "start_adjustment_frames": refined_start - start_frame,
        "end_adjustment_frames": refined_end - end_frame,
        "start_dbfs": start_dbfs,
        "end_dbfs": end_dbfs,
        "speech_protection_seconds": profile["speech_protection_seconds"],
        "waveform_search_seconds": profile["waveform_search_seconds"],
        "quiet_threshold_dbfs": threshold,
    }
