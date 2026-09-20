from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Iterable


VALID_ACTIONS = {"delete", "keep", "review"}
VALID_CATEGORIES = {
    "pre_roll", "post_roll", "mistake", "mistake_retake",
    "abandoned_sentence", "exact_repetition", "interruption",
    "technical_direction", "irrelevant_exchange", "empty_pause",
    "filler_word", "possible_tangent", "protected_pause", "other",
}


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: str | Path, data: Any) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_audio(media_path: str | Path, wav_path: str | Path, root: Path) -> None:
    ffmpeg = root / ".venv" / "bin" / "ffmpeg"
    if not ffmpeg.exists():
        raise FileNotFoundError("project-local ffmpeg not found; install imageio-ffmpeg first")
    command = [
        str(ffmpeg), "-y", "-nostdin", "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "audio extraction failed")


def transcribe_media(
    media_path: str | Path,
    output_path: str | Path,
    root: Path,
    model: str = "mlx-community/whisper-tiny",
    language: str = "Chinese",
) -> None:
    executable = root / ".venv" / "bin" / "mlx_whisper"
    if not executable.exists():
        raise FileNotFoundError("project-local mlx_whisper not found")
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable), str(media_path), "--model", model,
        "--language", language, "--word-timestamps", "True",
        "--output-format", "json", "--output-dir", str(output.parent),
        "--output-name", output.stem, "--verbose", "False",
    ]
    environment = dict(__import__("os").environ)
    environment["PATH"] = f"{root / '.venv' / 'bin'}:{environment.get('PATH', '')}"
    result = subprocess.run(command, cwd=root, env=environment)
    if result.returncode != 0:
        raise RuntimeError(f"local Whisper failed with exit code {result.returncode}")


def flatten_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in transcript.get("transcript", []):
        words.extend(segment.get("words", []))
    return words


def normalize_transcript(raw: dict[str, Any], media: dict[str, Any]) -> dict[str, Any]:
    """Normalize Whisper-like or canonical transcript JSON and assign stable word IDs."""
    if "transcript" in raw:
        source_segments = raw["transcript"]
        timestamp_unit = "seconds"
    elif isinstance(raw.get("result"), dict) and "utterances" in raw["result"]:
        source_segments = raw["result"]["utterances"]
        timestamp_unit = "milliseconds"
    else:
        source_segments = raw.get("segments", [])
        timestamp_unit = "seconds"

    normalized_segments: list[dict[str, Any]] = []
    word_counter = 1
    for segment_index, segment in enumerate(source_segments, start=1):
        normalized_words = []
        source_words = segment.get("words", [])
        for word in source_words:
            if timestamp_unit == "milliseconds":
                start_ms = word.get("start_time", -1)
                end_ms = word.get("end_time", -1)
                if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
                    continue
                if start_ms < 0 or end_ms < 0:
                    continue
                start = float(start_ms) / 1000
                end = float(end_ms) / 1000
            else:
                start = float(word.get("start_seconds", word.get("start", 0.0)))
                end = float(word.get("end_seconds", word.get("end", start)))
            word_text = str(word.get("text", word.get("word", ""))).strip()
            if not word_text:
                continue
            normalized_words.append({
                "word_id": f"w{word_counter:06d}",
                "text": word_text,
                "start_seconds": start,
                "end_seconds": end,
                "confidence": word.get("confidence", word.get("probability")),
            })
            word_counter += 1
        normalized_segments.append({
            "segment_id": f"s{segment_index:05d}",
            "speaker": segment.get("speaker", "speaker_1"),
            "text": str(segment.get("text", "")).strip(),
            "words": normalized_words,
        })

    result = {"media": media, "transcript": normalized_segments}
    errors = validate_transcript(result)
    if errors:
        raise ValueError("Invalid transcript: " + "; ".join(errors))
    return result


def validate_transcript(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    media = data.get("media", {})
    duration = float(media.get("duration_seconds", math.inf))
    seen: set[str] = set()
    previous_start = -1.0
    for word in flatten_words(data):
        word_id = word.get("word_id")
        start = word.get("start_seconds")
        end = word.get("end_seconds")
        if not word_id or word_id in seen:
            errors.append(f"duplicate or missing word_id: {word_id}")
        seen.add(word_id)
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            errors.append(f"invalid timestamps: {word_id}")
            continue
        if start < previous_start:
            errors.append(f"out-of-order timestamp: {word_id}")
        if start < 0 or end < start or end > duration + 0.001:
            errors.append(f"out-of-range timestamp: {word_id}")
        previous_start = start
    if not seen:
        errors.append("transcript has no word-level timestamps")
    return errors


def detect_silence_wav(
    wav_path: str | Path,
    threshold_db: float = -40.0,
    min_duration: float = 0.35,
    window_ms: int = 20,
) -> list[dict[str, Any]]:
    """Detect silence in uncompressed 16-bit PCM WAV using windowed RMS."""
    events: list[dict[str, Any]] = []
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        if sample_width != 2:
            raise ValueError("silence detector requires 16-bit PCM WAV")
        window_frames = max(1, int(sample_rate * window_ms / 1000))
        silent_start: float | None = None
        frame_cursor = 0
        event_index = 1
        while True:
            raw = wav.readframes(window_frames)
            if not raw:
                break
            samples = array.array("h")
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
            if channels > 1:
                mono = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples), channels)]
            else:
                mono = samples
            rms = math.sqrt(sum(float(v) * float(v) for v in mono) / max(1, len(mono)))
            db = 20 * math.log10(max(rms, 1.0) / 32768.0)
            window_start = frame_cursor / sample_rate
            window_end = (frame_cursor + len(mono)) / sample_rate
            if db <= threshold_db and silent_start is None:
                silent_start = window_start
            elif db > threshold_db and silent_start is not None:
                if window_start - silent_start >= min_duration:
                    events.append({
                        "event_id": f"a{event_index:05d}", "type": "silence",
                        "start_seconds": round(silent_start, 6),
                        "end_seconds": round(window_start, 6),
                        "duration_seconds": round(window_start - silent_start, 6),
                    })
                    event_index += 1
                silent_start = None
            frame_cursor += len(mono)
        final_time = frame_cursor / sample_rate
        if silent_start is not None and final_time - silent_start >= min_duration:
            events.append({
                "event_id": f"a{event_index:05d}", "type": "silence",
                "start_seconds": round(silent_start, 6),
                "end_seconds": round(final_time, 6),
                "duration_seconds": round(final_time - silent_start, 6),
            })
    return events


def chunk_transcript(data: dict[str, Any], max_words: int = 800, overlap_words: int = 100) -> list[dict[str, Any]]:
    words = flatten_words(data)
    if max_words <= overlap_words or max_words < 1 or overlap_words < 0:
        raise ValueError("max_words must be greater than overlap_words")
    chunks = []
    step = max_words - overlap_words
    for index, start in enumerate(range(0, len(words), step), start=1):
        selected = words[start:start + max_words]
        if not selected:
            break
        chunks.append({
            "chunk_id": f"c{index:04d}",
            "media": data["media"],
            "word_range": {"start_word_id": selected[0]["word_id"], "end_word_id": selected[-1]["word_id"]},
            "words": selected,
        })
        if start + max_words >= len(words):
            break
    return chunks


def validate_decisions(decisions: dict[str, Any], transcript: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    word_ids = {w["word_id"] for w in flatten_words(transcript)}
    order = {w["word_id"]: i for i, w in enumerate(flatten_words(transcript))}
    seen_decisions: set[str] = set()
    for decision in decisions.get("decisions", []):
        decision_id = decision.get("decision_id")
        action = decision.get("action")
        category = decision.get("category")
        start = decision.get("start_word_id")
        end = decision.get("end_word_id")
        if not decision_id or decision_id in seen_decisions:
            errors.append(f"duplicate or missing decision_id: {decision_id}")
        seen_decisions.add(decision_id)
        if action not in VALID_ACTIONS:
            errors.append(f"invalid action for {decision_id}: {action}")
        if category not in VALID_CATEGORIES:
            errors.append(f"invalid category for {decision_id}: {category}")
        if start not in word_ids or end not in word_ids:
            errors.append(f"unknown word range for {decision_id}: {start}..{end}")
        elif order[start] > order[end]:
            errors.append(f"reversed word range for {decision_id}")
        if not str(decision.get("reason", "")).strip():
            errors.append(f"missing reason for {decision_id}")
        confidence = decision.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"invalid confidence for {decision_id}")
        if action == "delete" and isinstance(confidence, (int, float)) and confidence < 0.9:
            errors.append(f"delete below automatic threshold for {decision_id}")
    return errors


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def merge_decisions(documents: Iterable[dict[str, Any]], transcript: dict[str, Any]) -> dict[str, Any]:
    words = flatten_words(transcript)
    order = {w["word_id"]: i for i, w in enumerate(words)}
    collected: list[dict[str, Any]] = []
    for document in documents:
        collected.extend(document.get("decisions", []))

    # Exact duplicates caused by overlapping chunks: keep the strongest one.
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in collected:
        key = (item["action"], item["category"], item["start_word_id"], item["end_word_id"])
        current = deduped.get(key)
        if current is None or item.get("confidence", 0) > current.get("confidence", 0):
            deduped[key] = dict(item)
    items = list(deduped.values())

    # Any overlap between a delete and a keep/review is unsafe and becomes review.
    for item in items:
        if item["action"] != "delete":
            continue
        item_range = (order[item["start_word_id"]], order[item["end_word_id"]])
        conflicts = []
        for other in items:
            if other is item or other["action"] == "delete":
                continue
            other_range = (order[other["start_word_id"]], order[other["end_word_id"]])
            if _ranges_overlap(item_range, other_range):
                conflicts.append(other["decision_id"])
        if conflicts:
            item["action"] = "review"
            item["reason"] += f"；与决策 {', '.join(conflicts)} 冲突，已转人工复核"
            item["confidence"] = min(float(item.get("confidence", 0)), 0.89)

    items.sort(key=lambda d: (order[d["start_word_id"]], order[d["end_word_id"]], d["action"]))
    for index, item in enumerate(items, start=1):
        item["decision_id"] = f"d{index:05d}"
    return {
        "schema_version": "edit-decisions-v0.1",
        "sop_version": "talking-head-v0.1",
        "media_id": transcript["media"]["media_id"],
        "summary": {
            "delete_count": sum(i["action"] == "delete" for i in items),
            "review_count": sum(i["action"] == "review" for i in items),
            "keep_count": sum(i["action"] == "keep" for i in items),
        },
        "decisions": items,
    }


def render_report(decisions: dict[str, Any], transcript: dict[str, Any]) -> str:
    word_map = {w["word_id"]: w for w in flatten_words(transcript)}
    lines = [
        "# 剪辑决策复核报告", "",
        f"- 素材：`{transcript['media']['filename']}`",
        f"- SOP：`{decisions.get('sop_version')}`",
        f"- 自动删除：{decisions.get('summary', {}).get('delete_count', 0)}",
        f"- 人工复核：{decisions.get('summary', {}).get('review_count', 0)}", "",
    ]
    for item in decisions.get("decisions", []):
        start_word = word_map[item["start_word_id"]]
        end_word = word_map[item["end_word_id"]]
        lines.extend([
            f"## {item['decision_id']} · {item['action']} · {item['category']}", "",
            f"- 内容：{item.get('content', '')}",
            f"- 词语范围：`{item['start_word_id']}–{item['end_word_id']}`",
            f"- 参考位置：{start_word['start_seconds']:.3f}s–{end_word['end_seconds']:.3f}s",
            f"- 原因：{item['reason']}",
            f"- 置信度：{item['confidence']:.2f}", "",
        ])
    return "\n".join(lines)


def evaluate(ai: dict[str, Any], human: dict[str, Any]) -> dict[str, Any]:
    def keys(doc: dict[str, Any], action: str) -> set[tuple[str, str]]:
        return {(d["start_word_id"], d["end_word_id"]) for d in doc.get("decisions", []) if d["action"] == action}
    ai_delete = keys(ai, "delete")
    human_delete = keys(human, "delete")
    correct = ai_delete & human_delete
    false_delete = ai_delete - human_delete
    missed = human_delete - ai_delete
    precision = len(correct) / len(ai_delete) if ai_delete else 1.0
    recall = len(correct) / len(human_delete) if human_delete else 1.0
    return {
        "correct_delete": len(correct),
        "false_delete": len(false_delete),
        "missed_delete": len(missed),
        "delete_precision": round(precision, 4),
        "delete_recall": round(recall, 4),
        "false_delete_ranges": sorted(false_delete),
        "missed_delete_ranges": sorted(missed),
    }


def build_prompt(chunk: dict[str, Any], audio_events: list[dict[str, Any]], root: Path) -> str:
    sop = (root / "单人口播自动粗剪_SOP_v0.1.md").read_text(encoding="utf-8")
    prompt_rules = (root / "剪辑决策提示词_v0.1.md").read_text(encoding="utf-8")
    output_rules = (root / "剪辑决策输出规范_v0.1.md").read_text(encoding="utf-8")
    return (
        f"{prompt_rules}\n\n--- SOP ---\n{sop}\n\n--- 输出规范 ---\n{output_rules}\n\n"
        f"--- 当前分块 ---\n{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
        f"--- 声音事件 ---\n{json.dumps(audio_events, ensure_ascii=False, indent=2)}\n"
    )


def run_codex_model(prompt_path: Path, schema_path: Path, output_path: Path, root: Path) -> None:
    prompt = prompt_path.read_text(encoding="utf-8")
    command = [
        "codex", "exec", "--ephemeral", "--sandbox", "read-only",
        "--skip-git-repo-check", "--output-schema", str(schema_path),
        "--output-last-message", str(output_path), "-",
    ]
    result = subprocess.run(command, input=prompt, text=True, cwd=root)
    if result.returncode != 0:
        raise RuntimeError(f"Codex decision model failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Talking-head edit decision pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("normalize-transcript")
    p.add_argument("raw"); p.add_argument("media"); p.add_argument("output")
    p = sub.add_parser("dev-transcribe-local", help="development-only MLX Whisper spike; not part of the production pipeline")
    p.add_argument("media"); p.add_argument("output"); p.add_argument("--model", default="mlx-community/whisper-tiny"); p.add_argument("--language", default="Chinese")
    p = sub.add_parser("extract-audio")
    p.add_argument("media"); p.add_argument("output")
    p = sub.add_parser("detect-silence")
    p.add_argument("wav"); p.add_argument("output"); p.add_argument("--threshold-db", type=float, default=-40.0); p.add_argument("--min-duration", type=float, default=0.35)
    p = sub.add_parser("chunk")
    p.add_argument("transcript"); p.add_argument("output"); p.add_argument("--max-words", type=int, default=800); p.add_argument("--overlap-words", type=int, default=100)
    p = sub.add_parser("validate")
    p.add_argument("decisions"); p.add_argument("transcript")
    p = sub.add_parser("merge")
    p.add_argument("transcript"); p.add_argument("output"); p.add_argument("decision_files", nargs="+")
    p = sub.add_parser("report")
    p.add_argument("decisions"); p.add_argument("transcript"); p.add_argument("output")
    p = sub.add_parser("evaluate")
    p.add_argument("ai"); p.add_argument("human"); p.add_argument("output")
    p = sub.add_parser("build-prompt")
    p.add_argument("chunk"); p.add_argument("audio_events"); p.add_argument("output")
    p = sub.add_parser("dev-run-codex", help="development-only model runner; production is locked to Doubao")
    p.add_argument("prompt"); p.add_argument("output")

    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.command == "normalize-transcript":
        save_json(args.output, normalize_transcript(load_json(args.raw), load_json(args.media)))
    elif args.command == "dev-transcribe-local":
        transcribe_media(args.media, args.output, root, args.model, args.language)
    elif args.command == "extract-audio":
        extract_audio(args.media, args.output, root)
    elif args.command == "detect-silence":
        save_json(args.output, {"audio_events": detect_silence_wav(args.wav, args.threshold_db, args.min_duration)})
    elif args.command == "chunk":
        save_json(args.output, {"chunks": chunk_transcript(load_json(args.transcript), args.max_words, args.overlap_words)})
    elif args.command == "validate":
        errors = validate_decisions(load_json(args.decisions), load_json(args.transcript))
        if errors:
            print("\n".join(errors), file=sys.stderr); raise SystemExit(1)
        print("valid")
    elif args.command == "merge":
        save_json(args.output, merge_decisions((load_json(p) for p in args.decision_files), load_json(args.transcript)))
    elif args.command == "report":
        Path(args.output).write_text(render_report(load_json(args.decisions), load_json(args.transcript)), encoding="utf-8")
    elif args.command == "evaluate":
        save_json(args.output, evaluate(load_json(args.ai), load_json(args.human)))
    elif args.command == "build-prompt":
        events_doc = load_json(args.audio_events)
        Path(args.output).write_text(build_prompt(load_json(args.chunk), events_doc.get("audio_events", []), root), encoding="utf-8")
    elif args.command == "dev-run-codex":
        run_codex_model(Path(args.prompt), root / "decision_pipeline" / "edit_decisions.schema.json", Path(args.output), root)


if __name__ == "__main__":
    main()
