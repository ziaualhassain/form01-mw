import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import Settings


class CorsTests(unittest.TestCase):
    def test_default_origins_allow_deployed_frontends_and_local_development(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        with TestClient(app) as client:
            for origin in [
                "http://localhost:3000",
                "http://localhost:3001",
                "https://form01-admin.vercel.app",
                "https://form01-store-xi.vercel.app",
            ]:
                with self.subTest(origin=origin):
                    response = client.options("/api/v1/products", headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "authorization,content-type",
                    })
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    self.assertEqual(response.headers["access-control-allow-credentials"], "true")
            response = client.options("/api/v1/products", headers={
                "Origin": "https://unapproved.example",
                "Access-Control-Request-Method": "GET",
            })
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("access-control-allow-origin", response.headers)

    def test_environment_overrides_default_origins(self):
        with patch.dict(os.environ, {
            "CORS_ORIGINS": '["https://custom.example"]',
        }, clear=True):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.cors_origins, ["https://custom.example"])
