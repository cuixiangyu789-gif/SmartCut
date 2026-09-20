import json
import tempfile
import unittest
import wave
from pathlib import Path

from decision_pipeline.pipeline import (
    chunk_transcript,
    detect_silence_wav,
    evaluate,
    merge_decisions,
    normalize_transcript,
    render_report,
    validate_decisions,
)


ROOT = Path(__file__).resolve().parent.parent


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.media = json.loads((ROOT / "fixtures/sample_media.json").read_text(encoding="utf-8"))
        raw = json.loads((ROOT / "fixtures/sample_whisper_transcript.json").read_text(encoding="utf-8"))
        self.transcript = normalize_transcript(raw, self.media)
        self.decisions = json.loads((ROOT / "fixtures/sample_decisions.json").read_text(encoding="utf-8"))

    def test_normalize_assigns_stable_word_ids(self):
        words = [w for s in self.transcript["transcript"] for w in s["words"]]
        self.assertEqual(words[0]["word_id"], "w000001")
        self.assertEqual(words[-1]["word_id"], "w000013")

    def test_chunk_overlap(self):
        chunks = chunk_transcript(self.transcript, max_words=6, overlap_words=2)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["words"][-2]["word_id"], chunks[1]["words"][0]["word_id"])

    def test_validate_decisions(self):
        self.assertEqual(validate_decisions(self.decisions, self.transcript), [])
        broken = json.loads(json.dumps(self.decisions))
        broken["decisions"][0]["start_word_id"] = "missing"
        self.assertTrue(validate_decisions(broken, self.transcript))

    def test_merge_turns_conflict_into_review(self):
        keep = json.loads(json.dumps(self.decisions))
        keep["decisions"][0].update({"decision_id": "k1", "action": "keep", "confidence": 0.95})
        merged = merge_decisions([self.decisions, keep], self.transcript)
        self.assertEqual(merged["summary"]["delete_count"], 0)
        self.assertEqual(merged["summary"]["review_count"], 1)

    def test_report_contains_reason_and_location(self):
        report = render_report(self.decisions, self.transcript)
        self.assertIn("错误表达后立即重新说出正确版本", report)
        self.assertIn("1.000s–2.750s", report)

    def test_evaluate(self):
        result = evaluate(self.decisions, self.decisions)
        self.assertEqual(result["delete_precision"], 1.0)
        self.assertEqual(result["delete_recall"], 1.0)

    def test_silence_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.wav"
            rate = 16000
            loud = (1000).to_bytes(2, "little", signed=True)
            silent = (0).to_bytes(2, "little", signed=True)
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
                wav.writeframes(loud * rate + silent * rate + loud * rate)
            events = detect_silence_wav(path, threshold_db=-40, min_duration=0.5)
            self.assertEqual(len(events), 1)
            self.assertAlmostEqual(events[0]["start_seconds"], 1.0, places=2)
            self.assertAlmostEqual(events[0]["end_seconds"], 2.0, places=2)


if __name__ == "__main__":
    unittest.main()

