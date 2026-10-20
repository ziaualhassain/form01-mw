import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import Principal, get_current_principal
from app.api.v1.admin_customers import router
from app.db.base import Base
from app.db.models import CustomerProfile, Order, Role, User, UserRole
from app.db.session import get_db


class AdminCustomersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        now = datetime.now(timezone.utc)
        self.customers = [
            User(email=f"customer{i:02d}@example.test", password_hash="never-expose",
                 created_at=now - timedelta(minutes=i), status="ACTIVE" if i else "SUSPENDED")
            for i in range(25)
        ]
        self.admin = User(email="admin@example.test", password_hash="never-expose")
        self.owner = User(email="owner@example.test", password_hash="never-expose")
        roles = [Role(name="ADMIN"), Role(name="OWNER"), Role(name="CUSTOMER")]
        self.db.add_all([*self.customers, self.admin, self.owner, *roles])
        await self.db.flush()
        self.db.add_all([
            UserRole(user_id=self.admin.id, role_id=roles[0].id),
            UserRole(user_id=self.admin.id, role_id=roles[2].id),
            UserRole(user_id=self.owner.id, role_id=roles[1].id),
            CustomerProfile(user_id=self.customers[0].id, first_name="Zia", last_name="Hassain",
                            display_name="Studio Customer", phone="+919876543210"),
            CustomerProfile(user_id=self.customers[1].id, display_name=r"Literal_100%\Path"),
        ])
        for i in range(55):
            self.db.add(Order(
                order_number=f"CUSTOMER-{i:03d}", user_id=self.customers[0].id,
                contact_email=self.customers[0].email, currency="INR", total_minor=9900,
                payment_status="PAID" if i < 10 else "REFUNDED" if i < 12 else "UNPAID",
                placed_at=now - timedelta(days=i), created_at=now - timedelta(days=i),
            ))
        self.db.add(Order(order_number="OTHER-CUSTOMER", user_id=self.customers[1].id,
                          contact_email=self.customers[1].email, total_minor=100))
        self.db.add(Order(order_number="STAFF-ORDER", user_id=self.admin.id,
                          contact_email=self.admin.email, total_minor=100))
        self.db.add(Order(order_number="GUEST", user_id=None, contact_email=self.customers[0].email, total_minor=100))
        await self.db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/admin/customers")
        async def session():
            yield self.db
        self.app.dependency_overrides[get_db] = session
        self.principal = Principal(user_id=self.admin.id, email=self.admin.email, roles=["ADMIN"])
        self.app.dependency_overrides[get_current_principal] = lambda: self.principal
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()

    async def test_directory_is_paginated_stable_and_excludes_staff(self):
        first = (await self.client.get("/admin/customers")).json()
        second = (await self.client.get("/admin/customers?offset=20")).json()
        self.assertEqual((first["total"], first["limit"], first["offset"], len(first["items"])), (25, 20, 0, 20))
        self.assertEqual(len(second["items"]), 5)
        ids = [item["id"] for item in first["items"] + second["items"]]
        self.assertEqual(ids, [str(user.id) for user in self.customers])
        self.assertEqual(len(set(ids)), 25)
        customer = first["items"][0]
        self.assertEqual((customer["order_count"], customer["paid_order_count"]), (55, 10))
        self.assertEqual(customer["status"], "SUSPENDED")
        self.assertEqual(first["items"][2]["order_count"], 0)
        for item in first["items"] + second["items"]:
            self.assertNotIn("password_hash", item)
            self.assertNotIn("roles", item)
            self.assertNotIn("access_token", item)
        self.assertEqual((await self.client.get("/admin/customers?q=admin")).json()["total"], 0)

    async def test_search_matches_profile_email_phone_and_full_name(self):
        for q in ["STUDIO", "zia hassain", "987654", "customer00@", "  Hassain  "]:
            with self.subTest(q=q):
                response = await self.client.get("/admin/customers", params={"q": q})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 1)
                self.assertEqual(response.json()["items"][0]["id"], str(self.customers[0].id))
        self.assertEqual((await self.client.get("/admin/customers?q=no-match")).json()["items"], [])
        self.assertEqual((await self.client.get("/admin/customers", params={"q": "   "})).json()["total"], 25)

    async def test_search_wildcards_are_literal_not_pattern_injection(self):
        for q in ["_", "%", "Literal_100%", r"\Path"]:
            with self.subTest(q=q):
                data = (await self.client.get("/admin/customers", params={"q": q})).json()
                self.assertEqual(data["total"], 1)
                self.assertEqual(data["items"][0]["id"], str(self.customers[1].id))
        self.assertEqual((await self.client.get("/admin/customers", params={"q": "' OR 1=1 --"})).json()["total"], 0)

    async def test_equal_creation_dates_use_id_as_stable_pagination_tiebreaker(self):
        self.customers[23].created_at = self.customers[24].created_at
        await self.db.commit()
        expected = sorted([str(user.id) for user in self.customers[23:]], reverse=True)
        first = (await self.client.get("/admin/customers?limit=1&offset=23")).json()["items"][0]["id"]
        second = (await self.client.get("/admin/customers?limit=1&offset=24")).json()["items"][0]["id"]
        self.assertEqual([first, second], expected)

    async def test_profile_defaults_statistics_and_no_secrets(self):
        path = f"/admin/customers/{self.customers[0].id}"
        response = await self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        data = response.json()
        self.assertEqual((data["first_name"], data["last_name"], data["display_name"], data["phone"]),
                         ("Zia", "Hassain", "Studio Customer", "+919876543210"))
        self.assertEqual((data["order_count"], data["paid_order_count"]), (55, 10))
        self.assertNotIn("password_hash", data)
        self.assertNotIn("never-expose", response.text)
        self.assertIsNotNone(data["latest_order_at"])
        blank = (await self.client.get(f"/admin/customers/{self.customers[2].id}")).json()
        self.assertIsNone(blank["first_name"])
        self.assertIsNone(blank["phone"])
        self.assertIsNone(blank["latest_order_at"])
        self.assertEqual(blank["order_count"], 0)

    async def test_order_history_pages_include_older_than_latest_fifty_and_only_customer(self):
        path = f"/admin/customers/{self.customers[0].id}/orders"
        pages = [(await self.client.get(path, params={"offset": offset})).json() for offset in [0, 20, 40]]
        self.assertEqual([len(page["items"]) for page in pages], [20, 20, 15])
        self.assertTrue(all(page["total"] == 55 for page in pages))
        numbers = [item["order_number"] for page in pages for item in page["items"]]
        self.assertEqual(numbers, [f"CUSTOMER-{i:03d}" for i in range(55)])
        self.assertIn("REFUNDED", {item["payment_status"] for page in pages for item in page["items"]})
        self.assertEqual((await self.client.get(path, params={"offset": 60})).json()["items"], [])
        empty = await self.client.get(f"/admin/customers/{self.customers[2].id}/orders")
        self.assertEqual((empty.json()["total"], empty.json()["items"]), (0, []))

    async def test_unknown_and_staff_ids_return_404_and_invalid_inputs_return_422(self):
        for customer_id in [uuid4(), self.admin.id, self.owner.id]:
            for suffix in ["", "/orders"]:
                self.assertEqual((await self.client.get(f"/admin/customers/{customer_id}{suffix}")).status_code, 404)
        self.assertEqual((await self.client.get("/admin/customers/not-a-uuid")).status_code, 422)
        for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 151}]:
            self.assertEqual((await self.client.get("/admin/customers", params=params)).status_code, 422)
        for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}]:
            self.assertEqual((await self.client.get(f"/admin/customers/{self.customers[0].id}/orders", params=params)).status_code, 422)

    async def test_admin_owner_access_customer_denied_and_auth_required(self):
        paths = ["/admin/customers", f"/admin/customers/{self.customers[0].id}", f"/admin/customers/{self.customers[0].id}/orders"]
        self.principal = Principal(user_id=self.owner.id, email=self.owner.email, roles=["OWNER"])
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 200)
        self.principal = Principal(user_id=self.customers[0].id, email=self.customers[0].email, roles=["CUSTOMER"])
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 403)
        del self.app.dependency_overrides[get_current_principal]
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 401)

    async def test_directory_has_no_write_endpoints(self):
        self.assertEqual((await self.client.post("/admin/customers", json={})).status_code, 405)
        for method in ["PATCH", "PUT", "DELETE"]:
            self.assertEqual((await self.client.request(method, f"/admin/customers/{self.customers[0].id}", json={})).status_code, 405)
