import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.homepage import get_public_homepage, replace_homepage_sections


class HomepageCategoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_homepage_returns_category_cdn_configuration(self):
        content = {"items": [{"product_type": "SHIRT", "image": {"desktop_url": "https://cdn.example/shirt.webp"}}]}
        result = MagicMock()
        result.scalars.return_value.all.return_value = [
            SimpleNamespace(id="categories", section_type="category_grid", position=1, content_json=content),
        ]
        db = AsyncMock()
        db.execute.return_value = result
        response = await get_public_homepage(db)
        self.assertEqual(response["sections"][0]["content"], content)
        self.assertEqual(response["sections"][0]["type"], "category_grid")

    async def test_category_grid_saved_with_existing_hero(self):
        db = AsyncMock()
        db.add_all = MagicMock()
        sections = [
            dict(section_key="hero", section_type="hero", position=0, enabled=True, content={}),
            dict(section_key="categories", section_type="category_grid", position=1, enabled=True, content={"items": [
                {"product_type": "SHIRT", "image": {"desktop_url": "https://cdn.example/shirt.webp", "mobile_url": "/mobile.webp"}},
            ]}),
        ]
        result = await replace_homepage_sections(db, sections, None)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1].section_type, "category_grid")
        db.commit.assert_awaited_once()

    async def test_bad_category_config_rejected_before_existing_content_deleted(self):
        for content in [
            {},
            {"items": [None]},
            {"items": [{"product_type": " "}]},
            {"items": [{"product_type": "SHIRT", "image": {"desktop_url": "//unsafe.example/img"}}]},
            {"items": [{"product_type": "SHIRT", "image": {"desktop_url": 123}}]},
            {"items": [{"product_type": "SHIRT"}, {"product_type": "shirt"}]},
        ]:
            with self.subTest(content=content):
                db = AsyncMock()
                with self.assertRaises(ValueError):
                    await replace_homepage_sections(db, [dict(section_key="categories", section_type="category_grid", position=1, enabled=True, content=content)], None)
                db.execute.assert_not_awaited()
