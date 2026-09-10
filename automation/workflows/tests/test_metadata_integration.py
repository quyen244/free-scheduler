import json
import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / "reup-pipeline.json"


class MetadataWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        exports = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.workflow = exports[0]
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.connections = cls.workflow["connections"]

    def targets(self, source, output=0):
        return [edge["node"] for edge in self.connections[source]["main"][output]]

    def test_canonical_workflow_remains_inactive_and_has_unique_identity(self):
        self.assertEqual(self.workflow["id"], "reupPipeline")
        self.assertEqual(self.workflow["name"], "video editing")
        self.assertFalse(self.workflow["active"])
        self.assertEqual(len(self.workflow["nodes"]), 36)
        ids = [node["id"] for node in self.workflow["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(self.nodes), len(self.workflow["nodes"]))

    def test_every_connection_references_a_real_node(self):
        for source, outputs in self.connections.items():
            self.assertIn(source, self.nodes)
            for branch in outputs["main"]:
                for edge in branch:
                    self.assertIn(edge["node"], self.nodes)

    def test_metadata_is_a_required_gate_before_voice(self):
        self.assertEqual(self.targets("Chunked"), ["Start metadata"])
        self.assertEqual(self.targets("Start metadata"), ["Wait for metadata"])
        self.assertEqual(self.targets("Wait for metadata"), ["Metadata valid?"])
        self.assertEqual(self.targets("Metadata valid?", 0), ["Metadata selected"])
        self.assertEqual(self.targets("Metadata valid?", 1), ["Metadata failed"])
        self.assertEqual(self.targets("Metadata selected"), ["Start voice"])
        self.assertEqual(self.targets("Metadata failed"), ["Metadata needs action"])

    def test_metadata_job_uses_internal_service_and_post_callback(self):
        start = self.nodes["Start metadata"]
        wait = self.nodes["Wait for metadata"]
        self.assertEqual(start["parameters"]["method"], "POST")
        self.assertEqual(
            start["parameters"]["url"],
            "http://metadata-service:8004/metadata/jobs",
        )
        body = {
            item["name"]: item["value"]
            for item in start["parameters"]["bodyParameters"]["parameters"]
        }
        self.assertIn("Chunked", body["video_id"])
        self.assertIn("http://n8n:5678/webhook-waiting/", body["callback_url"])
        self.assertEqual(wait["parameters"]["resume"], "webhook")
        self.assertEqual(wait["parameters"]["httpMethod"], "POST")

    def test_success_requires_done_job_and_selected_revision(self):
        conditions = self.nodes["Metadata valid?"]["parameters"]["conditions"]
        self.assertEqual(conditions["combinator"], "and")
        checks = {
            item["leftValue"]: item["rightValue"]
            for item in conditions["conditions"]
        }
        self.assertEqual(checks["={{ $json.body.state }}"], "done")
        self.assertEqual(checks["={{ $json.body.result.state }}"], "selected")

    def test_failure_message_uses_real_chat_and_explains_recovery(self):
        rejected = self.nodes["Send a text message"]
        failure = self.nodes["Metadata needs action"]
        self.assertIn("chatId", rejected["parameters"]["chatId"])
        self.assertNotIn("videoUrl", rejected["parameters"]["chatId"])
        self.assertIn("chatId", failure["parameters"]["chatId"])
        self.assertIn("Retry metadata stage", failure["parameters"]["text"])

    def test_workflow_contains_no_openai_secret_or_model_selection(self):
        serialized = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("sk-proj-", serialized)
        self.assertNotIn("gpt-5.6-luna", serialized)


if __name__ == "__main__":
    unittest.main()
