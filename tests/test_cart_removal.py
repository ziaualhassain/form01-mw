import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.dependencies import Principal, get_current_principal
from app.api.v1.cart import remove_cart_item, router
from app.db.session import get_db


class CartRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_removes_item_only_from_current_users_cart(self):
        cart_id, item_id = uuid4(), uuid4()
        principal = Principal(user_id=uuid4(), email="fixture@example.com", roles=[])
        item = SimpleNamespace(id=item_id, cart_id=cart_id)
        result = MagicMock()
        result.scalar_one_or_none.return_value = item
        db = SimpleNamespace(execute=AsyncMock(return_value=result), delete=AsyncMock(), commit=AsyncMock())
        with patch("app.api.v1.cart.get_or_create_cart", AsyncMock(return_value=SimpleNamespace(id=cart_id))) as get_cart:
            await remove_cart_item(item_id, principal, db)
        get_cart.assert_awaited_once_with(db, principal.user_id)
        statement = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        self.assertEqual(set(statement.params.values()), {item_id, cart_id})
        self.assertIn("cart_items.cart_id =", str(statement))
        self.assertIn("cart_items.id =", str(statement))
        db.delete.assert_awaited_once_with(item)
        db.commit.assert_awaited_once()

    async def test_missing_or_other_cart_item_is_not_deleted(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db = SimpleNamespace(execute=AsyncMock(return_value=result), delete=AsyncMock(), commit=AsyncMock())
        principal = Principal(user_id=uuid4(), email="fixture@example.com", roles=[])
        with patch("app.api.v1.cart.get_or_create_cart", AsyncMock(return_value=SimpleNamespace(id=uuid4()))):
            with self.assertRaises(HTTPException) as error:
                await remove_cart_item(uuid4(), principal, db)
        self.assertEqual(error.exception.status_code, 404)
        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()


class CartRemovalHTTPTests(unittest.TestCase):
    def test_route_requires_authentication(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.delete(f"/cart/items/{uuid4()}")
        self.assertEqual(response.status_code, 401)

    def test_success_returns_empty_204_response(self):
        app = FastAPI()
        app.include_router(router)
        cart_id, item_id = uuid4(), uuid4()
        item = SimpleNamespace(id=item_id, cart_id=cart_id)
        result = MagicMock()
        result.scalar_one_or_none.return_value = item
        db = SimpleNamespace(execute=AsyncMock(return_value=result), delete=AsyncMock(), commit=AsyncMock())
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_principal] = lambda: Principal(user_id=uuid4(), email="fixture@example.com", roles=[])
        with patch("app.api.v1.cart.get_or_create_cart", AsyncMock(return_value=SimpleNamespace(id=cart_id))):
            with TestClient(app) as client:
                response = client.delete(f"/cart/items/{item_id}")
                invalid = client.delete("/cart/items/not-a-uuid")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(invalid.status_code, 422)
        db.delete.assert_awaited_once_with(item)
