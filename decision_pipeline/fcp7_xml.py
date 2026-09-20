from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET


def add_text(parent: ET.Element, tag: str, value: object) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = str(value)
    return node


def add_rate(parent: ET.Element, fps: int) -> None:
    rate = ET.SubElement(parent, "rate")
    add_text(rate, "timebase", fps)
    add_text(rate, "ntsc", "FALSE")


def keep_ranges(total_frames: int, delete_ranges: list[dict]) -> list[tuple[int, int]]:
    normalized = sorted((int(x["start_frame"]), int(x["end_frame"])) for x in delete_ranges)
    merged: list[list[int]] = []
    for start, end in normalized:
        if not 0 <= start < end <= total_frames:
            raise ValueError(f"invalid delete range: {start}..{end}")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    result: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            result.append((cursor, start))
        cursor = end
    if cursor < total_frames:
        result.append((cursor, total_frames))
    return result


def add_file_description(file_node: ET.Element, media: dict, fps: int) -> None:
    add_text(file_node, "name", media["filename"])
    encoded_path = quote(Path(media["path"]).as_posix(), safe="/")
    add_text(file_node, "pathurl", f"file://localhost{encoded_path}")
    add_rate(file_node, fps)
    add_text(file_node, "duration", media["total_frames"])
    timecode = ET.SubElement(file_node, "timecode")
    add_rate(timecode, fps)
    add_text(timecode, "string", "00:00:00:00")
    add_text(timecode, "frame", 0)
    add_text(timecode, "displayformat", "NDF")
    file_media = ET.SubElement(file_node, "media")
    video = ET.SubElement(file_media, "video")
    characteristics = ET.SubElement(video, "samplecharacteristics")
    add_rate(characteristics, fps)
    add_text(characteristics, "width", media["width"])
    add_text(characteristics, "height", media["height"])
    add_text(characteristics, "anamorphic", "FALSE")
    add_text(characteristics, "pixelaspectratio", "square")
    add_text(characteristics, "fielddominance", "none")
    audio = ET.SubElement(file_media, "audio")
    audio_characteristics = ET.SubElement(audio, "samplecharacteristics")
    add_text(audio_characteristics, "depth", 16)
    add_text(audio_characteristics, "samplerate", media["audio_sample_rate"])
    add_text(audio, "channelcount", media["audio_channels"])


def add_clip(
    track: ET.Element,
    clip_id: str,
    name: str,
    source_in: int,
    source_out: int,
    timeline_start: int,
    timeline_end: int,
    total_frames: int,
    fps: int,
    media_type: str,
    track_index: int,
    media: dict,
    describe_file: bool = False,
) -> None:
    clip = ET.SubElement(track, "clipitem", {"id": clip_id})
    add_text(clip, "name", name)
    add_text(clip, "duration", total_frames)
    add_rate(clip, fps)
    add_text(clip, "start", timeline_start)
    add_text(clip, "end", timeline_end)
    add_text(clip, "in", source_in)
    add_text(clip, "out", source_out)
    file_node = ET.SubElement(clip, "file", {"id": "file-1"})
    if describe_file:
        add_file_description(file_node, media, fps)
    source_track = ET.SubElement(clip, "sourcetrack")
    add_text(source_track, "mediatype", media_type)
    add_text(source_track, "trackindex", track_index)


def generate(plan: dict) -> str:
    media = plan["media"]
    fps = int(media["fps"])
    total_frames = int(media["total_frames"])
    if plan.get("schema_version", "").startswith("virtual-timeline-"):
        keeps = [
            (int(item["source_in"]), int(item["source_out"]))
            for item in plan["keep_segments"]
        ]
    else:
        keeps = keep_ranges(total_frames, plan["delete_ranges"])
    sequence_duration = sum(end - start for start, end in keeps)

    root = ET.Element("xmeml", {"version": "4"})
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-ai-candidate"})
    add_text(sequence, "name", plan.get("sequence_name", "AI自动粗剪_虚拟时间线"))
    add_text(sequence, "duration", sequence_duration)
    add_rate(sequence, fps)
    timecode = ET.SubElement(sequence, "timecode")
    add_rate(timecode, fps)
    add_text(timecode, "string", "00:00:00:00")
    add_text(timecode, "frame", 0)
    add_text(timecode, "displayformat", "NDF")
    sequence_media = ET.SubElement(sequence, "media")

    video = ET.SubElement(sequence_media, "video")
    fmt = ET.SubElement(video, "format")
    characteristics = ET.SubElement(fmt, "samplecharacteristics")
    add_rate(characteristics, fps)
    add_text(characteristics, "width", media["width"])
    add_text(characteristics, "height", media["height"])
    add_text(characteristics, "anamorphic", "FALSE")
    add_text(characteristics, "pixelaspectratio", "square")
    add_text(characteristics, "fielddominance", "none")
    video_track = ET.SubElement(video, "track")

    timeline_cursor = 0
    for index, (source_in, source_out) in enumerate(keeps, start=1):
        length = source_out - source_in
        add_clip(
            video_track, f"v{index}", f"{media['filename']}_保留片段{index}",
            source_in, source_out, timeline_cursor, timeline_cursor + length,
            total_frames, fps, "video", 1, media, describe_file=index == 1,
        )
        timeline_cursor += length
    add_text(video_track, "enabled", "TRUE")
    add_text(video_track, "locked", "FALSE")

    audio = ET.SubElement(sequence_media, "audio")
    add_text(audio, "numOutputChannels", media["audio_channels"])
    audio_format = ET.SubElement(audio, "format")
    audio_characteristics = ET.SubElement(audio_format, "samplecharacteristics")
    add_text(audio_characteristics, "depth", 16)
    add_text(audio_characteristics, "samplerate", media["audio_sample_rate"])
    for channel in range(1, int(media["audio_channels"]) + 1):
        track = ET.SubElement(audio, "track")
        timeline_cursor = 0
        for index, (source_in, source_out) in enumerate(keeps, start=1):
            length = source_out - source_in
            add_clip(
                track, f"a{index}c{channel}", f"{media['filename']}_片段{index}_声道{channel}",
                source_in, source_out, timeline_cursor, timeline_cursor + length,
                total_frames, fps, "audio", channel, media,
            )
            timeline_cursor += length
        add_text(track, "enabled", "TRUE")
        add_text(track, "locked", "FALSE")
        add_text(track, "outputchannelindex", channel)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FCP7 XML from a virtual timeline")
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output.write_text(generate(plan), encoding="utf-8")


if __name__ == "__main__":
    main()
