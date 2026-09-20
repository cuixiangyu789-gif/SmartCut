#!/usr/bin/env python3
"""Minimal Doubao Seed-ASR 2.0 file transcription smoke test."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
RESOURCE_ID = "volc.seedasr.auc"
SUCCESS = "20000000"
PROCESSING_CODES = {"20000001", "20000002"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def _curl_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def post_json_with_system_curl(
    url: str, headers: dict[str, str], payload: dict
) -> tuple[dict, dict[str, str]]:
    """Use macOS curl so HTTPS follows the certificates trusted by the system."""
    with tempfile.TemporaryDirectory(prefix="doubao-asr-") as temp_dir:
        temp_path = Path(temp_dir)
        payload_path = temp_path / "request.json"
        headers_path = temp_path / "response.headers"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        config_lines = [
            'silent',
            'show-error',
            'request = "POST"',
            f'url = "{_curl_quote(url)}"',
            f'data-binary = "@{_curl_quote(str(payload_path))}"',
            f'dump-header = "{_curl_quote(str(headers_path))}"',
        ]
        config_lines.extend(
            f'header = "{_curl_quote(key)}: {_curl_quote(value)}"'
            for key, value in headers.items()
        )
        completed = subprocess.run(
            ["/usr/bin/curl", "--config", "-"],
            input="\n".join(config_lines) + "\n",
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"curl 连接失败：{completed.stderr.strip()}")
        raw_headers = headers_path.read_text(encoding="utf-8", errors="replace")
        response_headers: dict[str, str] = {}
        for line in raw_headers.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                response_headers[key.strip().lower()] = value.strip()
        response_text = completed.stdout
        return (json.loads(response_text) if response_text.strip() else {}), response_headers


def post_json(url: str, headers: dict[str, str], payload: dict) -> tuple[dict, dict[str, str]]:
    if sys.platform == "darwin" and Path("/usr/bin/curl").is_file():
        return post_json_with_system_curl(url, headers, payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response_body = response.read().decode("utf-8")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    return (json.loads(response_body) if response_body.strip() else {}), response_headers


def status_from(headers: dict[str, str], body: dict) -> tuple[str, str]:
    code = headers.get("x-api-status-code", "")
    message = headers.get("x-api-message", "")
    if not code and isinstance(body, dict):
        code = str(body.get("code", ""))
        message = str(body.get("message", message))
    return code, message


def extract_result(body: dict) -> dict:
    if isinstance(body.get("result"), dict):
        return body["result"]
    return body


def write_readable(result: dict, output_path: Path) -> None:
    lines: list[str] = []
    text = result.get("text")
    if text:
        lines.extend(["全文", str(text), "", "分句及逐字时间戳"])

    for utterance_index, utterance in enumerate(result.get("utterances", []), start=1):
        start = utterance.get("start_time", -1)
        end = utterance.get("end_time", -1)
        lines.append(f"\n[{utterance_index}] {start}ms--{end}ms  {utterance.get('text', '')}")
        for word in utterance.get("words", []):
            word_start = word.get("start_time", -1)
            word_end = word.get("end_time", -1)
            if word_start is None or word_end is None or word_start < 0 or word_end < 0:
                continue
            lines.append(f"  {word_start}ms--{word_end}ms  {word.get('text', '')}")

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def prepare_audio(media_path: Path, output_dir: Path) -> Path:
    if media_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return media_path

    ffmpeg_path = (
        Path(__file__).resolve().parent
        / "tools"
        / "ffmpeg"
        / "node_modules"
        / "ffmpeg-static"
        / "ffmpeg"
    )
    if not ffmpeg_path.is_file():
        raise RuntimeError("缺少项目内 FFmpeg，无法从视频提取音轨")

    audio_path = output_dir / "source_audio.mp3"
    print(f"正在从指定视频提取音轨：{media_path}")
    completed = subprocess.run(
        [
            str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "64k", str(audio_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"提取音轨失败：{completed.stderr.strip()}")
    print(f"音轨已提取：{audio_path}（{audio_path.stat().st_size / 1024:.0f}KB）")
    return audio_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Doubao recording-file ASR 2.0")
    parser.add_argument("media", type=Path, help="Local audio or video file")
    parser.add_argument("--format", dest="audio_format", help="Override media format")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-wait", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/doubao_asr"))
    args = parser.parse_args()

    api_key = os.environ.get("DOUBAO_ASR_API_KEY", "").strip()
    if not api_key:
        print("错误：当前终端没有 DOUBAO_ASR_API_KEY。", file=sys.stderr)
        return 2
    if not args.media.is_file():
        print(f"错误：找不到文件 {args.media}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    upload_media = prepare_audio(args.media, args.output_dir)
    media_format = (args.audio_format or upload_media.suffix.lstrip(".")).lower()

    print(f"正在读取：{upload_media}")
    encoded_audio = base64.b64encode(upload_media.read_bytes()).decode("ascii")
    task_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }
    payload = {
        "user": {"uid": "local-editing-pipeline"},
        "audio": {
            "data": encoded_audio,
            "format": media_format,
            "language": "zh-CN",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
        },
    }

    print(f"正在提交任务：{task_id}")
    submit_body, submit_headers = post_json(f"{BASE_URL}/submit", headers, payload)
    submit_code, submit_message = status_from(submit_headers, submit_body)
    if submit_code and submit_code != SUCCESS:
        raise RuntimeError(f"提交失败：{submit_code} {submit_message}")

    deadline = time.monotonic() + args.max_wait
    query_payload = {}
    while True:
        query_body, query_headers = post_json(f"{BASE_URL}/query", headers, query_payload)
        query_code, query_message = status_from(query_headers, query_body)
        if query_code == SUCCESS:
            break
        if query_code not in PROCESSING_CODES:
            raise RuntimeError(f"查询失败：{query_code or '未知状态'} {query_message}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待识别超过 {args.max_wait:.0f} 秒")
        print(f"识别处理中：{query_message or query_code}")
        time.sleep(args.poll_interval)

    raw_path = args.output_dir / "raw_result.json"
    readable_path = args.output_dir / "transcript_with_timestamps.txt"
    raw_path.write_text(json.dumps(query_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_readable(extract_result(query_body), readable_path)
    print(f"识别成功：{raw_path}")
    print(f"时间戳文本：{readable_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
