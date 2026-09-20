import unittest

from decision_pipeline.timeline_engine import build_virtual_timeline


class TimelineEngineTests(unittest.TestCase):
    def setUp(self):
        self.media = {"media_id": "m1", "fps": 50, "total_frames": 500}
        self.transcript = {
            "transcript": [{"words": [
                {"word_id": "w1", "start_seconds": 0.20, "end_seconds": 0.50},
                {"word_id": "w2", "start_seconds": 2.00, "end_seconds": 2.30},
                {"word_id": "w3", "start_seconds": 4.00, "end_seconds": 4.30},
            ]}]
        }
        self.events = {"audio_events": [
            {"event_id": "a1", "start_seconds": 5.10, "end_seconds": 5.32}
        ]}

    def test_exact_override_has_priority_and_timeline_is_conserved(self):
        decisions = {"decisions": [{
            "decision_id": "d1", "action": "delete",
            "location": {"type": "audio_event_range", "start_event_id": "a1", "end_event_id": "a1"},
        }]}
        overrides = {"overrides": [{
            "decision_id": "d1", "start_frame": 255, "end_frame": 266,
            "source": "human_confirmed_boundary",
        }]}
        result = build_virtual_timeline(self.media, self.transcript, self.events, decisions, overrides)
        self.assertEqual((result["delete_ranges"][0]["start_frame"], result["delete_ranges"][0]["end_frame"]), (255, 266))
        self.assertEqual(result["timeline_duration_frames"], 489)
        self.assertTrue(result["quality_checks"]["frame_conservation"])

    def test_word_range_uses_join_handles_and_integer_frames(self):
        decisions = {"decisions": [{
            "decision_id": "d2", "action": "delete",
            "location": {"type": "word_range", "start_word_id": "w2", "end_word_id": "w2"},
        }]}
        result = build_virtual_timeline(self.media, self.transcript, self.events, decisions, {"overrides": []})
        item = result["delete_ranges"][0]
        self.assertEqual((item["start_frame"], item["end_frame"]), (35, 190))
        self.assertEqual(result["keep_segments"][1]["timeline_start"], 35)

    def test_inter_word_gap_preserves_speech_handles(self):
        decisions = {"decisions": [{
            "decision_id": "d3", "action": "delete",
            "location": {"type": "inter_word_gap", "after_word_id": "w1", "before_word_id": "w2"},
        }]}
        result = build_virtual_timeline(self.media, self.transcript, self.events, decisions, {"overrides": []})
        item = result["delete_ranges"][0]
        self.assertEqual((item["start_frame"], item["end_frame"]), (35, 90))
        self.assertEqual(item["boundary_methods"], ["inter_word_gap_with_join_handles"])

    def test_sentence_gap_is_compressed_to_exactly_fifteen_frames(self):
        decisions = {"decisions": [{
            "decision_id": "d_gap", "action": "delete",
            "location": {
                "type": "inter_word_gap", "after_word_id": "w1", "before_word_id": "w2",
                "target_remaining_frames": 15,
            },
        }]}
        result = build_virtual_timeline(self.media, self.transcript, self.events, decisions, {"overrides": []})
        item = result["delete_ranges"][0]
        original_gap = 100 - 25
        deleted = item["end_frame"] - item["start_frame"]
        self.assertEqual(original_gap - deleted, 15)
        self.assertEqual(item["boundary_methods"], ["sentence_gap_compressed_to_target_frames"])

    def test_waveform_refinement_records_adjustments_and_avoids_words(self):
        decisions = {"decisions": [{
            "decision_id": "d4", "action": "delete",
            "location": {"type": "inter_word_gap", "after_word_id": "w1", "before_word_id": "w2"},
        }]}
        levels = [-10.0] * 500
        levels[37] = -50.0
        levels[88] = -50.0
        profile = {
            "join_handle_seconds": 0.20, "tail_handle_seconds": 0.50,
            "waveform_search_seconds": 0.10, "speech_protection_seconds": 0.04,
            "quiet_threshold_dbfs": -36.0, "description": "test",
        }
        result = build_virtual_timeline(
            self.media, self.transcript, self.events, decisions, {"overrides": []},
            waveform_levels=levels, pace_profile=profile,
        )
        self.assertEqual((result["delete_ranges"][0]["start_frame"], result["delete_ranges"][0]["end_frame"]), (37, 88))
        self.assertEqual(result["boundary_diagnostics"][0]["start_adjustment_frames"], 2)


if __name__ == "__main__":
    unittest.main()
