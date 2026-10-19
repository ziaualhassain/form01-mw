import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.policies import router
from app.services.policies import POLICY_PACK_PATH, POLICY_SECTIONS


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.settings = SimpleNamespace(policy_pack_approved_sha256=None)
        self.settings_patch = patch("app.services.policies.get_settings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def test_public_index_contains_six_unpublished_policies_without_drafts(self):
        with TestClient(self.app) as client:
            response = client.get("/api/v1/policies")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual({item["slug"] for item in response.json()}, set(POLICY_SECTIONS))
            for item in response.json():
                self.assertFalse(item["published"])
                self.assertNotIn("content_markdown", item)
            for slug in POLICY_SECTIONS:
                detail = client.get(f"/api/v1/policies/{slug}")
                self.assertEqual(detail.status_code, 409)
                self.assertNotIn("content_markdown", detail.json())
                self.assertNotIn("Choose and publish", detail.text)

    def test_unknown_policy_returns_404(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/api/v1/policies/not-a-policy").status_code, 404)

    def test_approved_content_and_source_changes_require_reapproval(self):
        source = "**Last updated:** 19 October 2026\n\n"
        for number, title in POLICY_SECTIONS.values():
            source += f"## {number}. {title}\n\n### {number}.1 Approved terms\n\nFinal approved text.\n\n---\n\n"
        source += "## Website implementation checklist\n\nPrivate editorial notes."
        with TemporaryDirectory() as directory:
            path = Path(directory) / "policies.md"
            path.write_text(source, encoding="utf-8")
            self.settings.policy_pack_approved_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch("app.services.policies.POLICY_PACK_PATH", path), TestClient(self.app) as client:
                index = client.get("/api/v1/policies").json()
                self.assertTrue(all(item["published"] for item in index))
                for slug in POLICY_SECTIONS:
                    response = client.get(f"/api/v1/policies/{slug}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["last_updated"], "19 October 2026")
                    self.assertIn("Final approved text.", response.json()["content_markdown"])
                    self.assertNotIn("Private editorial notes", response.text)
                path.write_text(source + "\nChanged.", encoding="utf-8")
                self.assertEqual(client.get("/api/v1/policies/privacy").status_code, 409)

    def test_current_draft_cannot_be_published_even_if_hash_is_approved(self):
        self.settings.policy_pack_approved_sha256 = hashlib.sha256(POLICY_PACK_PATH.read_bytes()).hexdigest()
        with self.assertLogs("app.api.v1.policies", level="ERROR"), TestClient(self.app) as client:
            response = client.get("/api/v1/policies/privacy")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Full legal name", response.text)

    def test_missing_approved_source_reports_service_error(self):
        self.settings.policy_pack_approved_sha256 = "a" * 64
        with patch("app.services.policies.POLICY_PACK_PATH", Path("/nonexistent/form01-policies.md")):
            with self.assertLogs("app.api.v1.policies", level="ERROR"), TestClient(self.app) as client:
                self.assertEqual(client.get("/api/v1/policies").status_code, 503)
