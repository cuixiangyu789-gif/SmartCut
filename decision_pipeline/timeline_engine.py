from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from decision_pipeline.boundary_refinement import DEFAULT_PROFILES, load_frame_levels, refine_boundaries


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def frame_floor(seconds: float, fps: int) -> int:
    return math.floor(seconds * fps + 1e-9)


def frame_ceil(seconds: float, fps: int) -> int:
    return math.ceil(seconds * fps - 1e-9)


def flatten_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    return [word for segment in transcript.get("transcript", []) for word in segment.get("words", [])]


def resolve_word_range(
    location: dict[str, Any], words: list[dict[str, Any]], fps: int, total_frames: int,
    join_handle_seconds: float, tail_handle_seconds: float,
) -> tuple[int, int, str]:
    order = {word["word_id"]: index for index, word in enumerate(words)}
    start_id = location["start_word_id"]
    end_id = location["end_word_id"]
    if start_id not in order or end_id not in order or order[start_id] > order[end_id]:
        raise ValueError(f"invalid word range: {start_id}..{end_id}")
    first_index = order[start_id]
    last_index = order[end_id]
    first = words[first_index]
    last = words[last_index]

    if first_index == 0:
        start_frame = 0
    elif float(first["start_seconds"]) - float(words[first_index - 1]["end_seconds"]) >= join_handle_seconds * 2:
        start_frame = frame_ceil(float(words[first_index - 1]["end_seconds"]) + join_handle_seconds, fps)
    else:
        start_frame = frame_floor(max(0.0, float(first["start_seconds"]) - 0.04), fps)

    if last_index == len(words) - 1:
        end_frame = total_frames
        previous_end = float(words[last_index - 1]["end_seconds"]) if last_index else 0.0
        start_frame = max(start_frame, frame_ceil(previous_end + tail_handle_seconds, fps))
    elif float(words[last_index + 1]["start_seconds"]) - float(last["end_seconds"]) >= join_handle_seconds * 2:
        end_frame = frame_floor(float(words[last_index + 1]["start_seconds"]) - join_handle_seconds, fps)
    else:
        end_frame = frame_ceil(float(last["end_seconds"]) + 0.04, fps)
    return start_frame, end_frame, "word_timestamp_with_join_handles"


def resolve_audio_range(
    location: dict[str, Any], events: list[dict[str, Any]], fps: int,
) -> tuple[int, int, str]:
    order = {event["event_id"]: index for index, event in enumerate(events)}
    start_id = location["start_event_id"]
    end_id = location["end_event_id"]
    if start_id not in order or end_id not in order or order[start_id] > order[end_id]:
        raise ValueError(f"invalid audio event range: {start_id}..{end_id}")
    selected = events[order[start_id]:order[end_id] + 1]
    return (
        frame_floor(float(selected[0]["start_seconds"]), fps),
        frame_ceil(float(selected[-1]["end_seconds"]), fps),
        "audio_event_boundary",
    )


def resolve_inter_word_gap(
    location: dict[str, Any], words: list[dict[str, Any]], fps: int,
    join_handle_seconds: float,
) -> tuple[int, int, str]:
    by_id = {word["word_id"]: word for word in words}
    after_id = location["after_word_id"]
    before_id = location["before_word_id"]
    if after_id not in by_id or before_id not in by_id:
        raise ValueError(f"invalid inter-word gap: {after_id}..{before_id}")
    after = by_id[after_id]
    before = by_id[before_id]
    after_end_frame = frame_ceil(float(after["end_seconds"]), fps)
    before_start_frame = frame_floor(float(before["start_seconds"]), fps)
    target_remaining_frames = location.get("target_remaining_frames")
    if target_remaining_frames is not None:
        target = int(target_remaining_frames)
        gap_frames = before_start_frame - after_end_frame
        if gap_frames <= target:
            raise ValueError(f"inter-word gap already within target: {after_id}..{before_id}")
        left_keep = target // 2
        right_keep = target - left_keep
        return (
            after_end_frame + left_keep,
            before_start_frame - right_keep,
            "sentence_gap_compressed_to_target_frames",
        )
    start_seconds = float(after["end_seconds"]) + join_handle_seconds
    end_seconds = float(before["start_seconds"]) - join_handle_seconds
    if start_seconds >= end_seconds:
        raise ValueError(f"inter-word gap too short after handles: {after_id}..{before_id}")
    return (
        frame_ceil(start_seconds, fps),
        frame_floor(end_seconds, fps),
        "inter_word_gap_with_join_handles",
    )


def merge_delete_ranges(ranges: list[dict[str, Any]], merge_gap_frames: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in sorted(ranges, key=lambda value: (value["start_frame"], value["end_frame"])):
        if merged and item["start_frame"] <= merged[-1]["end_frame"] + merge_gap_frames:
            merged[-1]["end_frame"] = max(merged[-1]["end_frame"], item["end_frame"])
            merged[-1]["decision_ids"].extend(item["decision_ids"])
            merged[-1]["boundary_methods"].extend(item["boundary_methods"])
        else:
            merged.append({
                "start_frame": item["start_frame"],
                "end_frame": item["end_frame"],
                "decision_ids": list(item["decision_ids"]),
                "boundary_methods": list(item["boundary_methods"]),
            })
    return merged


def build_keep_segments(total_frames: int, deletes: list[dict[str, Any]]) -> list[dict[str, int]]:
    segments: list[dict[str, int]] = []
    source_cursor = 0
    timeline_cursor = 0
    for item in deletes:
        if source_cursor < item["start_frame"]:
            length = item["start_frame"] - source_cursor
            segments.append({
                "segment_index": len(segments) + 1,
                "source_in": source_cursor,
                "source_out": item["start_frame"],
                "timeline_start": timeline_cursor,
                "timeline_end": timeline_cursor + length,
            })
            timeline_cursor += length
        source_cursor = item["end_frame"]
    if source_cursor < total_frames:
        length = total_frames - source_cursor
        segments.append({
            "segment_index": len(segments) + 1,
            "source_in": source_cursor,
            "source_out": total_frames,
            "timeline_start": timeline_cursor,
            "timeline_end": timeline_cursor + length,
        })
    return segments


def validate_timeline(total_frames: int, deletes: list[dict[str, Any]], keeps: list[dict[str, int]]) -> list[str]:
    errors: list[str] = []
    previous_end = 0
    for item in deletes:
        if not 0 <= item["start_frame"] < item["end_frame"] <= total_frames:
            errors.append(f"out-of-range delete: {item['start_frame']}..{item['end_frame']}")
        if item["start_frame"] < previous_end:
            errors.append("overlapping delete ranges remain after merge")
        previous_end = item["end_frame"]
    kept_frames = sum(item["source_out"] - item["source_in"] for item in keeps)
    deleted_frames = sum(item["end_frame"] - item["start_frame"] for item in deletes)
    if kept_frames + deleted_frames != total_frames:
        errors.append("frame conservation failed")
    for index, item in enumerate(keeps):
        if item["timeline_end"] - item["timeline_start"] != item["source_out"] - item["source_in"]:
            errors.append(f"duration mismatch in keep segment {index + 1}")
        if index and item["timeline_start"] != keeps[index - 1]["timeline_end"]:
            errors.append(f"timeline gap before keep segment {index + 1}")
    return errors


def build_virtual_timeline(
    media: dict[str, Any], transcript: dict[str, Any], audio_events_doc: dict[str, Any],
    decisions: dict[str, Any], overrides_doc: dict[str, Any],
    join_handle_seconds: float = 0.20, tail_handle_seconds: float = 0.50,
    merge_gap_frames: int = 1, waveform_levels: list[float] | None = None,
    pace_profile_name: str = "compact", pace_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fps = int(media["fps"])
    total_frames = int(media["total_frames"])
    words = flatten_words(transcript)
    events = audio_events_doc.get("audio_events", [])
    overrides = {item["decision_id"]: item for item in overrides_doc.get("overrides", [])}
    resolved: list[dict[str, Any]] = []
    profile = pace_profile or DEFAULT_PROFILES[pace_profile_name]
    boundary_diagnostics: list[dict[str, Any]] = []

    for decision in decisions.get("decisions", []):
        if decision.get("action") != "delete":
            continue
        decision_id = decision["decision_id"]
        if decision_id in overrides:
            override = overrides[decision_id]
            start_frame = int(override["start_frame"])
            end_frame = int(override["end_frame"])
            method = str(override.get("source", "human_override"))
            diagnostic = {
                "decision_id": decision_id,
                "refinement": "skipped_human_override",
                "proposed_start_frame": start_frame,
                "proposed_end_frame": end_frame,
                "refined_start_frame": start_frame,
                "refined_end_frame": end_frame,
            }
        else:
            location = decision.get("location", {})
            if location.get("type") == "word_range":
                start_frame, end_frame, method = resolve_word_range(
                    location, words, fps, total_frames, join_handle_seconds, tail_handle_seconds,
                )
            elif location.get("type") == "audio_event_range":
                start_frame, end_frame, method = resolve_audio_range(location, events, fps)
            elif location.get("type") == "inter_word_gap":
                start_frame, end_frame, method = resolve_inter_word_gap(
                    location, words, fps, join_handle_seconds,
                )
            else:
                raise ValueError(f"decision {decision_id} has no supported location")
            diagnostic = {
                "decision_id": decision_id,
                "refinement": "timestamp_only",
                "proposed_start_frame": start_frame,
                "proposed_end_frame": end_frame,
                "refined_start_frame": start_frame,
                "refined_end_frame": end_frame,
            }
            if waveform_levels is not None:
                start_frame, end_frame, waveform_diagnostic = refine_boundaries(
                    start_frame, end_frame, waveform_levels, words, fps, profile, total_frames,
                )
                method += "+waveform_safe_snap"
                diagnostic.update(waveform_diagnostic)
                diagnostic["refinement"] = "waveform_safe_snap"
        boundary_diagnostics.append(diagnostic)
        resolved.append({
            "start_frame": start_frame,
            "end_frame": end_frame,
            "decision_ids": [decision_id],
            "boundary_methods": [method],
        })

    deletes = merge_delete_ranges(resolved, merge_gap_frames)
    keeps = build_keep_segments(total_frames, deletes)
    errors = validate_timeline(total_frames, deletes, keeps)
    if errors:
        raise ValueError("; ".join(errors))
    duration_frames = keeps[-1]["timeline_end"] if keeps else 0
    return {
        "schema_version": "virtual-timeline-v0.1",
        "media_id": media["media_id"],
        "media": media,
        "fps": fps,
        "source_total_frames": total_frames,
        "timeline_duration_frames": duration_frames,
        "pace_profile": {"name": pace_profile_name, **profile},
        "delete_ranges": deletes,
        "keep_segments": keeps,
        "boundary_diagnostics": boundary_diagnostics,
        "quality_checks": {
            "valid": True,
            "frame_conservation": True,
            "continuous_timeline": True,
            "video_audio_share_segment_map": True
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic virtual edit timeline")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--audio-events", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequence-name", default=None)
    parser.add_argument("--audio-wav", type=Path)
    parser.add_argument("--profile", choices=sorted(DEFAULT_PROFILES), default="compact")
    args = parser.parse_args()
    profile = DEFAULT_PROFILES[args.profile]
    levels = load_frame_levels(args.audio_wav, int(load(args.media)["fps"])) if args.audio_wav else None
    result = build_virtual_timeline(
        load(args.media), load(args.transcript), load(args.audio_events),
        load(args.decisions), load(args.overrides),
        join_handle_seconds=float(profile["join_handle_seconds"]),
        tail_handle_seconds=float(profile["tail_handle_seconds"]),
        waveform_levels=levels,
        pace_profile_name=args.profile,
        pace_profile=profile,
    )
    if args.sequence_name:
        result["sequence_name"] = args.sequence_name
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
