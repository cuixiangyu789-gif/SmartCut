from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def append_feedback(path: Path, item: dict) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schema_version": "boundary-feedback-v0.1", "items": []
    }
    item = dict(item)
    item["start_error_frames"] = int(item["human_start_frame"]) - int(item["automatic_start_frame"])
    item["end_error_frames"] = int(item["human_end_frame"]) - int(item["automatic_end_frame"])
    item["recorded_at"] = datetime.now(timezone.utc).isoformat()
    item.setdefault("feedback_id", f"bf{len(doc['items']) + 1:04d}")
    doc["items"].append(item)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description="Append one reviewed boundary to the second-stage error log")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--media-id", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--automatic-start", type=int, required=True)
    parser.add_argument("--automatic-end", type=int, required=True)
    parser.add_argument("--human-start", type=int, required=True)
    parser.add_argument("--human-end", type=int, required=True)
    parser.add_argument("--issue-type", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--profile", default="compact")
    args = parser.parse_args()
    append_feedback(args.log, {
        "media_id": args.media_id,
        "decision_id": args.decision_id,
        "automatic_start_frame": args.automatic_start,
        "automatic_end_frame": args.automatic_end,
        "human_start_frame": args.human_start,
        "human_end_frame": args.human_end,
        "issue_type": args.issue_type,
        "reason": args.reason,
        "profile": args.profile,
    })


if __name__ == "__main__":
    main()
