import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from contract import (  # noqa: E402
    ContractViolation,
    apply_edit,
    approve,
    assert_valid,
    build_snapshot,
    expected_chunk_count,
    validate_snapshot,
    validate_source,
)


class PipelineAcceptanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
        cls.policy = cls.fixtures["policy"]
        cls.sources = {item["id"]: item for item in cls.fixtures["reference_sources"]}

    def snapshot(self, fixture_id="thirteen_minutes_42_seconds"):
        return build_snapshot(self.sources[fixture_id], self.policy)

    def test_reference_fixtures_freeze_expected_chunk_counts_and_durations(self):
        for fixture in self.fixtures["reference_sources"]:
            with self.subTest(fixture=fixture["id"]):
                snapshot = build_snapshot(fixture, self.policy)
                self.assertEqual(
                    expected_chunk_count(fixture["duration_s"], self.policy),
                    fixture["expected_chunk_count"],
                )
                self.assertEqual(
                    [chunk["duration_s"] for chunk in snapshot["chunks"]],
                    fixture["expected_chunk_durations_s"],
                )
                self.assertEqual(validate_snapshot(snapshot, self.policy), [])

    def test_five_to_nine_minutes_is_one_chunk_and_longer_sources_are_variable(self):
        self.assertEqual(len(self.snapshot("five_minutes")["chunks"]), 1)
        self.assertEqual(len(self.snapshot("nine_minutes")["chunks"]), 1)
        self.assertEqual(len(self.snapshot("ten_minutes")["chunks"]), 2)
        self.assertEqual(len(self.snapshot()["chunks"]), 3)
        self.assertEqual(len(self.snapshot("twenty_minutes")["chunks"]), 4)

    def test_invalid_source_boundaries_rights_resolution_and_corruption_are_blocked(self):
        baseline = self.snapshot()["source"]
        for invalid in self.fixtures["invalid_sources"]:
            source = deepcopy(baseline)
            source.update({key: value for key, value in invalid.items() if key not in {"id", "expected_error"}})
            errors = validate_source(source, self.policy)
            with self.subTest(case=invalid["id"]):
                self.assertTrue(
                    any(invalid["expected_error"] in error for error in errors),
                    errors,
                )

        unknown_rights = self.snapshot()
        unknown_rights["source"]["rights_status"] = "unknown"
        with self.assertRaises(ContractViolation):
            approve(unknown_rights, "revision-1", self.policy)

    def test_chunk_plan_is_contiguous_sentence_safe_and_complete(self):
        snapshot = self.snapshot()
        self.assertEqual([chunk["name"] for chunk in snapshot["chunks"]], ["part_1", "part_2", "part_3"])
        self.assertEqual(snapshot["chunks"][0]["start_s"], 0)
        self.assertEqual(snapshot["chunks"][-1]["end_s"], 822)
        self.assertTrue(all(chunk["ends_on_transcript_segment"] for chunk in snapshot["chunks"]))
        self.assertTrue(all(abs(chunk["boundary_shift_s"]) <= 15 for chunk in snapshot["chunks"]))

        broken = deepcopy(snapshot)
        broken["chunks"][1]["start_s"] += 1
        self.assertTrue(any("ordered" in error for error in validate_snapshot(broken, self.policy)))

    def test_zero_chunks_and_missing_required_assets_block_review(self):
        no_chunks = self.snapshot()
        no_chunks["chunks"] = []
        no_chunks["metadata"]["chunks"] = []
        no_chunks["assets"]["vertical"] = []
        no_chunks["targets"] = no_chunks["targets"][:1]
        self.assertTrue(any("at least one chunk" in error for error in validate_snapshot(no_chunks, self.policy)))

        missing_whole = self.snapshot()
        missing_whole["assets"]["whole"] = None
        self.assertTrue(any("1920x1080" in error for error in validate_snapshot(missing_whole, self.policy)))

    def test_metadata_contract_is_vietnamese_truthful_tone_and_platform_specific(self):
        snapshot = self.snapshot()
        assert_valid(snapshot, self.policy)
        self.assertEqual(snapshot["metadata"]["youtube"]["language"], "vi")
        self.assertEqual(len(snapshot["metadata"]["youtube"]["hashtags"]), 5)
        self.assertEqual(len(snapshot["metadata"]["chunks"]), 3)
        self.assertIn("facebook", snapshot["metadata"]["chunks"][0])
        self.assertIn("tiktok", snapshot["metadata"]["chunks"][0])

        too_many_emojis = deepcopy(snapshot)
        too_many_emojis["metadata"]["chunks"][0]["facebook"]["caption"] += " 😀 😃 😄"
        self.assertTrue(any("more than two emojis" in error for error in validate_snapshot(too_many_emojis, self.policy)))

    def test_one_brand_three_chunks_has_correct_assets_and_exactly_seven_targets(self):
        snapshot = self.snapshot()
        self.assertEqual((snapshot["assets"]["whole"]["width"], snapshot["assets"]["whole"]["height"]), (1920, 1080))
        self.assertEqual(len(snapshot["assets"]["vertical"]), 3)
        self.assertTrue(all((asset["width"], asset["height"]) == (1080, 1920) for asset in snapshot["assets"]["vertical"]))
        self.assertEqual(len(snapshot["targets"]), 7)
        self.assertEqual(sum(target["platform"] == "youtube" for target in snapshot["targets"]), 1)
        self.assertEqual(sum(target["platform"] == "facebook" for target in snapshot["targets"]), 3)
        self.assertEqual(sum(target["platform"] == "tiktok" for target in snapshot["targets"]), 3)

    def test_same_brand_facebook_and_tiktok_reuse_each_chunk_asset(self):
        snapshot = self.snapshot()
        for chunk in snapshot["chunks"]:
            targets = [target for target in snapshot["targets"] if target["content_item_id"] == chunk["id"]]
            self.assertEqual({target["platform"] for target in targets}, {"facebook", "tiktok"})
            self.assertEqual(len({target["asset_id"] for target in targets}), 1)

    def test_no_target_runs_before_one_revision_approval_and_replay_is_safe(self):
        snapshot = self.snapshot()
        self.assertTrue(all(target["status"] == "pending_approval" for target in snapshot["targets"]))
        premature = deepcopy(snapshot)
        premature["targets"][0]["status"] = "queued"
        self.assertTrue(any("no target may run" in error for error in validate_snapshot(premature, self.policy)))
        self.assertTrue(approve(snapshot, "revision-1", self.policy))
        self.assertTrue(all(target["status"] == "queued" for target in snapshot["targets"]))
        self.assertFalse(approve(snapshot, "revision-1", self.policy))
        self.assertEqual(len(snapshot["targets"]), 7)
        self.assertEqual(sum(event["event"] == "revision_approved" for event in snapshot["audit"]), 1)
        assert_valid(snapshot, self.policy)

    def test_stale_approval_is_rejected_and_edits_create_a_new_revision(self):
        snapshot = self.snapshot()
        approve(snapshot, "revision-1", self.policy)

        for edit_kind in ("post_metadata", "visual_text", "brand", "target"):
            with self.subTest(edit_kind=edit_kind):
                edited = apply_edit(snapshot, edit_kind)
                self.assertEqual(edited["revision"]["id"], "revision-2")
                self.assertEqual(edited["revision"]["approval_state"], "pending")
                self.assertTrue(all(target["status"] == "pending_approval" for target in edited["targets"]))
                with self.assertRaises(ContractViolation):
                    approve(edited, "revision-1", self.policy)

        metadata_edit = apply_edit(snapshot, "post_metadata")
        self.assertFalse(metadata_edit["assets"]["whole"]["stale"])
        self.assertTrue(approve(metadata_edit, "revision-2", self.policy))
        visual_edit = apply_edit(snapshot, "visual_text")
        self.assertTrue(visual_edit["assets"]["whole"]["stale"])
        self.assertTrue(all(asset["stale"] for asset in visual_edit["assets"]["vertical"]))
        with self.assertRaises(ContractViolation):
            approve(visual_edit, "revision-2", self.policy)

    def test_wrong_platform_asset_mapping_is_rejected(self):
        snapshot = self.snapshot()
        youtube_target = next(target for target in snapshot["targets"] if target["platform"] == "youtube")
        youtube_target["unit"] = "chunk"
        youtube_target["asset_id"] = snapshot["assets"]["vertical"][0]["id"]
        self.assertTrue(any("YouTube" in error for error in validate_snapshot(snapshot, self.policy)))

    def test_tiktok_success_is_draft_delivered_not_publicly_published(self):
        snapshot = self.snapshot()
        tiktok_target = next(target for target in snapshot["targets"] if target["platform"] == "tiktok")
        self.assertEqual(tiktok_target["accepted_outcome"], "draft_delivered")
        approve(snapshot, "revision-1", self.policy)
        tiktok_target["status"] = "published"
        errors = validate_snapshot(snapshot, self.policy)
        self.assertTrue(any("must not claim TikTok" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
