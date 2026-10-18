# FORM/01 Unified Middleware, API, Database & Commerce Requirements

**Document:** Unified engineering requirements
**Product:** FORM/01 — `form01.com` and `shop.form01.com`
**Version:** 1.0 — proposed baseline for engineering review
**Backend:** Python middleware/API
**Database:** PostgreSQL hosted on Aiven
**Customer UI:** Separate storefront consuming the middleware API
**Admin/support UI:** Separate protected interface consuming the same middleware API
**Architecture recommendation:** Modular monolith first; one backend service and one PostgreSQL database

---

## 1. Executive summary

FORM/01 requires a single Python backend/middleware that owns business logic, validation, authorization, database access, integrations, and workflows. Two independent frontends will consume it:

1. **Customer storefront** — product discovery, account/profile, cart, checkout, payment, order history, tracking, returns.
2. **Admin/support console** — catalogue management, collections/drops, inventory, order fulfilment, customer support, returns/refunds, CMS, analytics, configuration, staff permissions, and audit history.

Both interfaces must call the same backend API. Neither frontend may connect directly to PostgreSQL or implement authoritative business rules independently.

Use **one Aiven PostgreSQL database** for the initial system. Do not create separate customer and admin databases. Separate the backend into domain modules and use a shared schema with foreign keys and transactions. Separate databases or microservices can be evaluated later if a concrete scale, regulatory, team-ownership, or isolation requirement emerges.

This document consolidates the customer experience, admin platform, API contracts, database model, security, workflows, operations, testing, and delivery requirements. It is a requirements specification, not a claim that any feature is already implemented.

## 2. Goals and non-goals

### Goals
- Provide a dependable premium ecommerce experience for FORM/01.
- Support registered customers and, if enabled, guest checkout.
- Allow authorized staff to manage products, variants, collections, stock, orders, returns, content, and store settings.
- Centralize business logic in Python so multiple UIs can be built or replaced without duplicating rules.
- Keep catalogue, stock, order, payment, customer, and fulfilment data consistent.
- Use Aiven PostgreSQL with managed credentials, TLS, migrations, backups, and monitoring.
- Provide versioned, documented APIs with stable response formats.
- Make sensitive operations auditable and retry-safe.
- Build in phases, beginning with the reliable shopping and order lifecycle.

### Non-goals for the first release unless explicitly prioritized
- Splitting the backend into microservices.
- A separate customer database.
- Multi-warehouse optimization, multi-vendor marketplace, subscriptions, loyalty points, or complex promotions.
- Building a custom payment processor or storing card details.
- Giving the frontend direct database access.
- Implementing every optional feature before the first launch.

## 3. Architecture

```text
Customer UI (shop.form01.com)        Admin / Support UI (protected)
             |                                      |
             +------------------ HTTPS -------------+
                                |
                                v
                     Python API / Middleware
                   (FastAPI is recommended)
                                |
      +-------------------------+-------------------------+
      |                         |                         |
      v                         v                         v
 Authentication &        Domain services             Integrations
 Authorization            - Customers                - Payment provider
                          - Catalogue                - Email provider
                          - Cart / Pricing            - Shipping provider
                          - Inventory                 - Media/object storage
                          - Checkout / Orders         - Analytics/monitoring
                          - Payments / Refunds
                          - CMS / Reporting
                                |
                                v
                     SQLAlchemy + Alembic
                                |
                                v
                     Aiven PostgreSQL
```

### 3.1 Technology recommendations
- **Python:** use a currently supported Python release.
- **API framework:** FastAPI recommended for typed REST APIs, validation, OpenAPI documentation, dependency injection, and async support where appropriate.
- **Validation:** Pydantic request/response schemas.
- **Database access:** SQLAlchemy 2.x (or another deliberately selected ORM); use Alembic for migrations.
- **PostgreSQL:** Aiven-managed PostgreSQL with TLS.
- **Testing:** pytest; integration tests against PostgreSQL.
- **Background work:** start with a durable database-backed job/outbox pattern. Add a worker and optionally Redis/queue technology when needed. Do not rely on in-process background tasks for critical payment, email, or fulfilment jobs.
- **Configuration:** environment variables and deployment secret management; never commit credentials.
- **API documentation:** generated OpenAPI plus maintained examples.
- **Observability:** structured logs, metrics, tracing where practical, health/readiness endpoints, error reporting.
- **Deployment:** containerized Python service behind HTTPS/reverse proxy or managed app platform.

The frontend framework is independent of the backend choice. A Next.js, React, mobile, or future client should all consume the same documented API.

### 3.2 Code structure

```text
app/
  main.py
  api/
    dependencies.py
    errors.py
    v1/
      auth.py
      public_catalogue.py
      customer_profile.py
      addresses.py
      cart.py
      checkout.py
      customer_orders.py
      returns.py
      admin_products.py
      admin_collections.py
      admin_inventory.py
      admin_orders.py
      admin_customers.py
      admin_content.py
      admin_reports.py
      admin_users.py
      webhooks.py
  core/
    config.py
    security.py
    logging.py
    rate_limit.py
    permissions.py
  db/
    session.py
    base.py
    models/
    migrations/                 # Alembic migrations
  schemas/
    auth.py
    customer.py
    product.py
    cart.py
    checkout.py
    order.py
    payment.py
    inventory.py
    admin.py
  services/
    auth_service.py
    customer_service.py
    catalogue_service.py
    pricing_service.py
    cart_service.py
    inventory_service.py
    checkout_service.py
    order_service.py
    payment_service.py
    shipping_service.py
    return_service.py
    content_service.py
    notification_service.py
    reporting_service.py
  integrations/
    payments/
    shipping/
    email/
    media/
  jobs/
    worker.py
    handlers.py
  tests/
    unit/
    integration/
    api/
    security/
```

The exact folder names may change, but keep API routing, schemas, domain/business logic, database models, and third-party integrations separate. Route handlers should be thin.

## 4. Design principles

1. **Single source of truth:** the Python backend calculates price, discounts, tax, shipping, stock availability, order status, and permissions.
2. **Shared commerce domain:** customer and admin interfaces use the same product, variant, inventory, order, payment, and customer records.
3. **Explicit ownership:** every customer-owned resource is checked against the authenticated customer or a secure guest-access token.
4. **Least privilege:** admin roles grant only required actions.
5. **Immutable purchase snapshots:** order items and addresses retain the values used at purchase time.
6. **Money-safe arithmetic:** use integer minor currency units or a rigorously specified Decimal approach; never binary floats for money.
7. **Idempotent workflows:** retries must not create duplicate orders, charges, refunds, stock movements, or webhook effects.
8. **Transactions for invariants:** database transactions and locking/conditional updates protect stock, order totals, coupon limits, and state transitions.
9. **No secret leakage:** credentials, session tokens, reset tokens, payment secrets, and sensitive provider payloads must not appear in frontend code or logs.
10. **Backward-compatible APIs:** version public contracts and avoid breaking clients without a planned migration.
11. **Migrations only:** database schema changes are version-controlled and applied through Alembic.
12. **Privacy by design:** collect only necessary customer data and define retention/deletion rules.

## 5. Customer-facing functional requirements

### 5.1 Public catalogue
Customers can:
- Browse homepage, products, categories, and collections/drops such as EST.01.
- Search by product name and supported tags.
- Filter by category, collection, size, colour, price, and availability where those attributes exist.
- Sort by featured, newest, price low-to-high, and price high-to-low.
- View only products that are published and available for public sale.
- See product imagery, descriptions, pricing, options/variants, sizing, care instructions, shipping/return policy, and availability.
- Use responsive pages with loading, empty, not-found, unavailable, and error states.
- Share canonical product/collection URLs and receive correct SEO metadata.

### 5.2 Product details and variants
- A product can have one or more variants (for example size and colour).
- A variant has a unique SKU, option combination, price override where supported, status, and inventory relationship.
- Variant selection is required before adding a variant-based item to cart.
- The API validates publication status, price, and stock on add-to-cart and again during checkout.
- Product detail must not expose internal cost, supplier information, staff notes, or hidden inventory data.
- Product deletion/archive must not break historical orders.

### 5.3 Registration, authentication, and profile
- Support sign-up, sign-in, sign-out, email verification, forgot/reset password, session expiry, and revocation.
- Select a maintained authentication implementation; do not store plain-text passwords or invent a weak token/session system.
- Require verified email before exposing protected account order history and sensitive account operations.
- Do not reveal whether an arbitrary email exists in sign-in/reset responses.
- Customer profile includes name/display name, email, optional phone, optional locale, timestamps, and verification state.
- Email changes require verification.
- Marketing consent is separate from account creation.
- Support account deletion request subject to legal/tax/order-retention requirements.
- Guest checkout can be enabled without requiring account registration.

### 5.4 Address book
- Create, edit, set default, and delete saved shipping/billing addresses.
- Capture recipient, phone if required, address lines, locality, city, state/region, postal code, country code, optional delivery instructions.
- Validate inputs server-side.
- Changing/deleting a saved address must not modify a historical order address snapshot.
- Enforce at most one default shipping and one default billing address per customer.

### 5.5 Cart
- Add/remove cart items and change quantities.
- Persist carts for signed-in customers and guests.
- Guest cart uses an opaque secure identifier; store a hash if the token is persisted.
- On sign-in, merge guest and account carts with a defined policy and revalidate stock, price, variant status, and duplicates.
- Show subtotal, discount, estimated shipping/tax where possible, and total.
- A cart is not a stock reservation unless explicit reservation logic is implemented.
- Never trust client-supplied price or total values.

### 5.6 Checkout
- Collect contact email, shipping address, optional billing address, and shipping option.
- Recalculate product prices, discount eligibility, shipping, tax, and total on the server.
- Verify inventory before order placement.
- Create an order with pending/unpaid state, immutable order-item snapshots, address snapshots, and payment attempt.
- Create payment sessions through the chosen payment provider.
- A browser redirect alone must never confirm payment; use verified provider responses/webhooks.
- Prevent duplicate checkout and payment with idempotency keys.
- Handle pending, successful, failed, cancelled, timed-out, and ambiguous payment states.
- Return a confirmation page only from persisted server-side order/payment state.

### 5.7 Customer order history
- Registered customer sees only their own orders, paginated and sorted by date.
- Order detail includes order number, date, line-item snapshots, quantities, option labels, totals, payment status, fulfilment status, shipping address summary, and tracking information.
- Guest order access requires a secure random token or verified email-link flow; order numbers alone are not authorization.
- Customer can request cancellation only when policy/state allows.
- Historical records remain accurate if current product names, images, prices, or addresses change.

### 5.8 Shipping, returns, and refunds
- Show shipment carrier, tracking number/link, shipment status, and tracking events where available.
- Support cancellation requests, return requests, reasons, status updates, and optional evidence uploads.
- Eligibility is determined by server-side policy and current order/fulfilment state.
- Return request, inspection/restock decision, refund, and payment provider confirmation are distinct records/states.
- Refunds can be partial or full and must not exceed captured amount less previous successful refunds.
- Restock only according to configured policy and inspection outcome.
- Notify customer of order confirmation, payment outcomes, shipment, delivery, cancellation, refund, and return changes.

### 5.9 Optional retention features
- Wishlist.
- Verified-purchaser reviews and moderation.
- Restock alerts.
- Newsletter opt-in/unsubscribe.
- Reorder and recommendations.
These can be phased after the core purchase flow.

## 6. Admin and support functional requirements

All admin operations are performed through the same Python API, but require staff authentication, RBAC, audit logging, and stricter controls.

### 6.1 Dashboard
- Show orders today, gross/net sales as defined, pending fulfilment, low-stock items, returns needing review, payment failures, and recent activity.
- Define metrics precisely; avoid treating created orders as paid revenue.
- Filter by date range and relevant status.
- Dashboard is operational, not a substitute for an accounting ledger.

### 6.2 Product catalogue
Authorized staff can:
- Create, edit, preview, publish, unpublish, archive, and duplicate products.
- Manage title, slug, description, SEO metadata, status, product type, category, tags, collection assignments, images, and product attributes.
- Manage variants/options such as size and colour, SKU, price, compare-at price, weight where relevant, and status.
- Validate required fields before publishing.
- Avoid duplicate slugs/SKUs.
- See where a product appears and whether it is purchasable.
- Preserve order snapshots when a product is changed or archived.

### 6.3 Collections, categories, and drops
- Create/edit/archive collections and categories.
- Assign products and control display order.
- Configure collection slug, description, imagery, SEO metadata, active dates, and publication state.
- Support EST.01 and future drops without code changes.
- Optionally schedule publication/unpublication.

### 6.4 Media
- Upload and manage product/collection/content media through a selected object storage/media provider.
- Store media metadata and URLs in PostgreSQL, not large binary files.
- Validate file type/size, generate safe variants/thumbnails where supported, and support alt text/sort order.
- Do not accept arbitrary server filesystem paths from clients.

### 6.5 Inventory
- View stock by variant and location if multiple locations are needed.
- Adjust stock with reason, actor, timestamp, and audit trail.
- Record movements for receipts, corrections, returns, reservations, releases, and sales.
- Configure low-stock thresholds.
- Prevent overselling through transaction-safe updates/reservations.
- Do not let admin edits silently rewrite past inventory movement history.

### 6.6 Orders and fulfilment
- Search/filter orders by order number, customer email, status, payment state, fulfilment state, and date.
- View order items, address snapshots, payment attempts, refunds, shipment, status timeline, and safe customer details.
- Fulfil all or part of an order; create shipments and attach tracking.
- Cancel orders only through permitted state transitions.
- Retry or reconcile payment only through safe provider workflows.
- Print/export invoices or packing slips if required.
- Never allow staff to mark an order paid without a valid verified payment or authorized reconciliation procedure.

### 6.7 Customer support
- Search customers by verified email, name, or order number with access restrictions.
- View profile, consent state, addresses, and order history only for staff roles that need it.
- Support notes, if implemented, must be clearly marked internal and never leaked to customer API responses.
- Staff must not see passwords, raw auth tokens, full card data, or other unnecessary secrets.
- Avoid customer impersonation. If support-assisted actions are necessary, record actor, reason, target customer, timestamp, and actions.

### 6.8 Returns, refunds, and promotions
- Review/approve/reject return requests according to role and policy.
- Record inspection and restock decision.
- Initiate refunds through provider integration and reconcile provider state.
- Manage coupons with status, date range, currency, discount type/value, limits, product/collection eligibility, minimum spend, per-customer limit, and combinability.
- Prevent coupon usage-limit races and duplicate redemptions.

### 6.9 CMS and store settings
- Manage homepage sections, banners, editorial pages, announcement bar, navigation/footer links, policies, SEO metadata, and product/collection presentation.
- Support draft/preview/publish workflow.
- Manage store name, currency, tax/shipping settings, return policy, contact details, and integrations with restricted permissions.
- Do not store secrets as ordinary visible settings; use secret management and masked integration status.

### 6.10 Staff, permissions, audit
- Support staff roles and permissions.
- Suggested roles: `OWNER`, `ADMIN`, `CATALOG_MANAGER`, `INVENTORY_MANAGER`, `ORDER_MANAGER`, `SUPPORT_AGENT`, `CONTENT_MANAGER`, `ANALYST`.
- Enforce least privilege on every admin endpoint, not just by hiding UI buttons.
- Log sensitive actions: price changes, publication, stock changes, order cancellation, refunds, role changes, settings/integration changes, and support access to personal data.
- Audit records are append-only to ordinary users and include actor, action, entity, entity ID, timestamp, and redacted before/after values where appropriate.

## 7. API standards

### 7.1 Base paths
- Versioned API: `/api/v1`
- Provider webhooks: `/api/webhooks/{provider}`
- Liveness: `/health/live`
- Readiness: `/health/ready`
- Metrics endpoint should be private or protected according to deployment.
- OpenAPI documentation should be available in development and protected/disabled as appropriate in production.

### 7.2 Response contract
Successful responses should use a consistent shape, for example:

```json
{
  "data": {},
  "meta": {
    "request_id": "request-correlation-id"
  }
}
```

Errors should use a consistent shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request could not be processed.",
    "details": [],
    "request_id": "request-correlation-id"
  }
}
```

Do not expose stack traces, SQL errors, secret values, internal provider payloads, or personal data in generic errors. Use appropriate HTTP statuses: `200`, `201`, `202`, `204`, `400`, `401`, `403`, `404`, `409`, `422`, `429`, and `500` as applicable.

### 7.3 General API rules
- Validate all input using typed schemas.
- Use pagination for lists (`limit`, `cursor` or stable page strategy).
- Bound pagination and text input sizes.
- Normalize emails and country/currency codes.
- Use UTC timestamps in storage and ISO 8601 in APIs.
- Use idempotency keys on checkout, payment/refund initiation, and other retry-prone mutations.
- Add request IDs and structured logs.
- Use rate limits for auth, password reset, email verification, coupon validation, guest-order lookup, and sensitive actions.
- Do not accept client-authored state transitions such as `paymentStatus=PAID`.
- Return only fields the current actor is allowed to see.
- Use stable error codes for frontend handling.

## 8. Endpoint catalogue

Exact payloads should be defined in OpenAPI and Pydantic schemas. The routes below are a required logical surface; naming can be adjusted consistently before implementation.

### 8.1 Public catalogue
- `GET /api/v1/products`
- `GET /api/v1/products/{slug}`
- `GET /api/v1/categories`
- `GET /api/v1/collections`
- `GET /api/v1/collections/{slug}`
- `GET /api/v1/search?q=...`
- `GET /api/v1/storefront/config` for public non-secret settings

### 8.2 Authentication and customer profile
- Authentication provider/library routes for sign-up, sign-in, sign-out, verification, and password reset.
- `GET /api/v1/me`
- `PATCH /api/v1/me/profile`
- `POST /api/v1/me/email-change`
- `POST /api/v1/me/account-deletion-request`
- `GET /api/v1/me/addresses`
- `POST /api/v1/me/addresses`
- `PATCH /api/v1/me/addresses/{address_id}`
- `DELETE /api/v1/me/addresses/{address_id}`
- `POST /api/v1/me/addresses/{address_id}/set-default`
- `GET /api/v1/me/consents`
- `PUT /api/v1/me/consents/{consent_type}`

### 8.3 Cart and checkout
- `GET /api/v1/cart`
- `POST /api/v1/cart/items`
- `PATCH /api/v1/cart/items/{item_id}`
- `DELETE /api/v1/cart/items/{item_id}`
- `POST /api/v1/cart/merge`
- `POST /api/v1/checkout/quote`
- `POST /api/v1/checkout`
- `GET /api/v1/checkout/{checkout_id}` if resumable checkout is supported

### 8.4 Customer orders, shipments, returns
- `GET /api/v1/me/orders`
- `GET /api/v1/me/orders/{order_number}`
- `POST /api/v1/me/orders/{order_number}/cancel-request`
- `POST /api/v1/me/orders/{order_number}/payment-retry` if supported
- `GET /api/v1/guest-orders/{secure_token}`
- `GET /api/v1/me/returns`
- `POST /api/v1/me/orders/{order_number}/returns`
- `GET /api/v1/me/returns/{request_number}`
- Optional wishlist endpoints under `/api/v1/me/wishlist`

### 8.5 Admin catalogue and content
- `GET/POST /api/v1/admin/products`
- `GET/PATCH/DELETE /api/v1/admin/products/{id}` where delete is safe; otherwise archive.
- `POST /api/v1/admin/products/{id}/publish`
- `POST /api/v1/admin/products/{id}/unpublish`
- `POST /api/v1/admin/products/{id}/media`
- `GET/POST/PATCH /api/v1/admin/collections`
- `GET/POST/PATCH /api/v1/admin/categories`
- `GET/POST/PATCH /api/v1/admin/content/pages`
- `GET/POST/PATCH /api/v1/admin/content/homepage-sections`
- `GET/POST/PATCH /api/v1/admin/content/banners`
- `GET/PATCH /api/v1/admin/settings`

### 8.6 Admin inventory
- `GET /api/v1/admin/inventory`
- `GET /api/v1/admin/inventory/variants/{variant_id}`
- `POST /api/v1/admin/inventory/adjustments`
- `GET /api/v1/admin/inventory/movements`
- `GET /api/v1/admin/inventory/low-stock`
- Any stock mutation must include quantity/delta, reason, and idempotency/reference where applicable.

### 8.7 Admin orders, customers, returns
- `GET /api/v1/admin/orders`
- `GET /api/v1/admin/orders/{order_number}`
- `POST /api/v1/admin/orders/{order_number}/cancel`
- `POST /api/v1/admin/orders/{order_number}/shipments`
- `PATCH /api/v1/admin/shipments/{shipment_id}`
- `GET /api/v1/admin/customers`
- `GET /api/v1/admin/customers/{id}`
- `GET /api/v1/admin/returns`
- `POST /api/v1/admin/returns/{id}/approve`
- `POST /api/v1/admin/returns/{id}/reject`
- `POST /api/v1/admin/returns/{id}/inspection`
- `POST /api/v1/admin/payments/{payment_id}/refund`

### 8.8 Staff, audit, reports, integrations
- `GET/POST/PATCH /api/v1/admin/staff`
- `GET/POST/PATCH /api/v1/admin/roles`
- `GET /api/v1/admin/audit-logs`
- `GET /api/v1/admin/reports/sales`
- `GET /api/v1/admin/reports/products`
- `GET /api/v1/admin/reports/inventory`
- `GET /api/v1/admin/reports/orders`
- `GET/PATCH /api/v1/admin/integrations/{provider}` using masked configuration/status
- `POST /api/webhooks/payments/{provider}`
- `POST /api/webhooks/shipping/{provider}` if shipping integration supports webhooks

## 9. Database design on Aiven PostgreSQL

### 9.1 Database strategy
Use one PostgreSQL database for the initial release. Keep clear domain boundaries in code. PostgreSQL schemas such as `public` or logical namespaces can be considered, but avoid unnecessary schema complexity before the model stabilizes.

Aiven provides the managed database host; it does not replace schema design, access control, migrations, monitoring, backups, or application security. Configure:
- TLS/SSL for database connections.
- A dedicated application database user with least privilege.
- Separate migration credentials or deployment job where feasible.
- Strong secrets outside source control.
- Private networking/IP allowlists where supported by the chosen Aiven plan/deployment.
- Automated backups and documented restore testing.
- Connection pooling/limits appropriate to the Python deployment.
- Staging and production environments with separate databases/credentials.
- Database metrics, disk/connection monitoring, and alerting.

### 9.2 Naming and common field conventions
- Use consistent snake_case table/column names in PostgreSQL, unless the ORM convention is deliberately different.
- Internal primary keys: UUID recommended.
- `created_at`, `updated_at` timestamps in UTC.
- Use explicit foreign keys and indexes.
- Use soft deletion only where required; define how it affects uniqueness and relationships.
- Use enums or constrained values for finite states.
- Add check constraints for positive quantities and non-negative amounts where practical.
- Use unique constraints for emails, SKUs, slugs, provider event IDs, order numbers, and idempotency keys within their scope.
- Use `NUMERIC` or integer minor units for money, never `FLOAT`/`REAL`.
- Do not cascade-delete legally required orders/payments/refunds.

### 9.3 Identity and staff tables

#### `users`
Shared identity table for customers and staff, where the selected auth design supports it:
- `id` UUID PK
- `email` normalized, unique where present
- `email_verified_at` nullable
- `password_hash` nullable if identity provider handles credentials
- `status` (`PENDING_VERIFICATION`, `ACTIVE`, `LOCKED`, `DISABLED`, `DELETION_PENDING`)
- `last_login_at` nullable
- `created_at`, `updated_at`

Do not store plaintext passwords. Use the chosen auth library/provider's supported identity/session model.

#### `auth_identities` (if external/passwordless identities are used)
- `id` UUID PK
- `user_id` FK → `users.id`
- `provider`
- `provider_subject`
- `created_at`
Unique (`provider`, `provider_subject`).

#### `customer_profiles`
- `id` UUID PK
- `user_id` FK → `users.id`, unique
- `first_name`, `last_name`, `display_name`
- `phone`, `phone_verified_at`
- `locale`
- `created_at`, `updated_at`

#### `staff_profiles`
- `id` UUID PK
- `user_id` FK → `users.id`, unique
- `display_name`
- `status`
- `created_at`, `updated_at`

#### `roles`, `permissions`, `user_roles`, `role_permissions`
- `roles`: `id`, unique `name`, description
- `permissions`: `id`, unique `code`, description
- `user_roles`: `user_id` FK, `role_id` FK; unique pair
- `role_permissions`: `role_id` FK, `permission_id` FK; unique pair

Do not assume that hiding an admin button is security. Check permission in the backend for each protected operation.

#### `auth_tokens` / `sessions`
Only create custom tables if the selected authentication implementation requires them. Store token hashes rather than raw one-time tokens, with purpose, expiry, used/revoked timestamps. Prefer a maintained auth library's recommended adapter/schema.

### 9.4 Customer addresses and consent

#### `addresses`
- `id` UUID PK
- `user_id` FK → `users.id`
- `label`
- `recipient_name`, `phone`
- `line1`, `line2`, `locality`
- `city`, `state_or_region`, `postal_code`, `country_code`
- `delivery_instructions`
- `is_default_shipping`, `is_default_billing`
- `created_at`, `updated_at`, optional `deleted_at`

#### `customer_consents`
- `id` UUID PK
- nullable `user_id` FK
- nullable `email` for guest signup flows
- `consent_type` (`MARKETING_EMAIL`, `MARKETING_SMS`, etc.)
- `status` (`GRANTED`, `WITHDRAWN`)
- `source`, `recorded_at`, nullable `withdrawn_at`

Keep consent history when needed for compliance; signup is not automatically marketing consent.

### 9.5 Catalogue tables

#### `products`
- `id` UUID PK
- `title`, unique `slug`
- `description` and optional short description
- `status` (`DRAFT`, `ACTIVE`, `ARCHIVED`)
- `product_type`
- `seo_title`, `seo_description`
- `published_at`, optional `archived_at`
- `created_by` FK → users, nullable if system-generated
- `created_at`, `updated_at`

#### `product_variants`
- `id` UUID PK
- `product_id` FK → products
- `sku` unique
- `title`/variant label
- `price_minor`, optional `compare_at_price_minor`
- `currency`
- `status`
- `weight_grams` or explicit shipping weight unit, nullable
- `created_at`, `updated_at`

#### `product_options`
- `id`, `product_id` FK, `name`, `position`
#### `product_option_values`
- `id`, `option_id` FK, `value`, `position`
#### `variant_option_values`
- `variant_id` FK, `option_value_id` FK; unique pair
#### `product_media`
- `id`, `product_id` FK, optional `variant_id` FK, `media_asset_id` FK, `alt_text`, `position`
#### `media_assets`
- `id`, `provider`, `object_key`, `url`, `mime_type`, `size_bytes`, `width`, `height`, `alt_text`, `created_at`
#### `categories`
- `id`, nullable `parent_id` self-FK, `name`, unique `slug`, description, `status`, `position`
#### `product_categories`
- `product_id` FK, `category_id` FK; unique pair
#### `tags`, `product_tags`
- normalized tags and product/tag join table
#### `collections`
- `id`, `name`, unique `slug`, description, image/media reference, status, `starts_at`, `ends_at`, SEO fields, created/updated timestamps
#### `collection_products`
- `collection_id` FK, `product_id` FK, `position`; unique pair
#### `url_redirects`
- `id`, `source_path` unique, `destination_path`, `status_code`, timestamps
#### `scheduled_publications` (optional)
- entity type/reference, scheduled action/time, status, creator, timestamps. Prefer explicit foreign keys when feasible.

Store media files in object storage/media service; store metadata and references in PostgreSQL. Never store image binaries in ordinary product rows.

### 9.6 Inventory tables

#### `inventory_locations`
- `id`, `name`, `code` unique, `status`, address/metadata as needed
#### `inventory_levels`
- `id`, `variant_id` FK, `location_id` FK
- `on_hand_quantity`, `reserved_quantity`, optional `reorder_threshold`
- `updated_at`
Unique (`variant_id`, `location_id`).

Available quantity should be derived consistently (for example on-hand minus active reservations) and updated transaction-safely.

#### `inventory_movements`
- `id`, `variant_id` FK, `location_id` FK
- `movement_type` (`RECEIPT`, `ADJUSTMENT`, `SALE`, `RETURN_RESTOCK`, `RESERVATION`, `RELEASE`)
- `quantity_delta`
- `reason`, `reference_type`, `reference_id`
- `actor_user_id` nullable FK
- `idempotency_key` where appropriate
- `created_at`

#### `inventory_reservations`
- `id`, `variant_id` FK, nullable `order_id` FK, nullable `cart_id` FK
- `quantity`
- `status` (`ACTIVE`, `COMMITTED`, `RELEASED`, `EXPIRED`)
- `expires_at`, `created_at`, `updated_at`

Prefer explicit nullable order/cart references with constraints over an unchecked polymorphic foreign key. Define the policy for when reservation begins and how it is released.

### 9.7 Cart tables

#### `carts`
- `id` UUID PK
- nullable `user_id` FK → users
- nullable `anonymous_token_hash`
- `status` (`ACTIVE`, `CONVERTED`, `ABANDONED`, `EXPIRED`)
- `currency`
- `expires_at`, `created_at`, `updated_at`

A cart must have an authenticated owner or a valid anonymous identity. Avoid publicly exposing sequential IDs.

#### `cart_items`
- `id` UUID PK
- `cart_id` FK → carts
- `variant_id` FK → product_variants
- `quantity` positive integer
- `created_at`, `updated_at`
Unique (`cart_id`, `variant_id`) if each variant appears once in a cart.

### 9.8 Orders and purchase snapshots

#### `orders`
- `id` UUID PK
- `order_number` unique customer-facing identifier
- nullable `user_id` FK → users for guest orders
- `contact_email`, optional `contact_phone`
- `status` (`PENDING`, `CONFIRMED`, `PROCESSING`, `PARTIALLY_FULFILLED`, `FULFILLED`, `CANCELLED`, `COMPLETED`)
- `payment_status` (`UNPAID`, `PENDING`, `AUTHORIZED`, `PAID`, `PARTIALLY_REFUNDED`, `REFUNDED`, `FAILED`)
- `fulfilment_status` (`UNFULFILLED`, `PARTIALLY_FULFILLED`, `FULFILLED`, `SHIPPED`, `DELIVERED`, `EXCEPTION`, `RETURNED`)
- `currency`
- `subtotal_minor`, `discount_minor`, `shipping_minor`, `tax_minor`, `total_minor`
- `coupon_code_snapshot` nullable
- `customer_note` nullable
- `placed_at`, `cancelled_at`, `created_at`, `updated_at`

Order status, payment status, and fulfilment status are separate dimensions. Validate allowed transitions in service code and, where appropriate, database constraints.

#### `order_items`
- `id` UUID PK
- `order_id` FK → orders
- nullable `product_id` FK and `variant_id` FK for historical retention
- `product_name_snapshot`
- `variant_label_snapshot`
- `sku_snapshot`
- `product_slug_snapshot`
- `image_url_snapshot`
- `unit_price_minor`, `quantity`, `discount_minor`, `tax_minor`, `line_total_minor`
- `created_at`

Snapshot fields are the historical source of truth; do not recalculate an old order from current catalogue data.

#### `order_addresses`
- `id` UUID PK
- `order_id` FK → orders
- `address_type` (`SHIPPING`, `BILLING`)
- recipient, phone, address lines, locality, city, region, postal code, country code, delivery instruction snapshots
- `created_at`
Unique (`order_id`, `address_type`).

#### `order_status_events`
- `id`, `order_id` FK
- `from_status`, `to_status`, `message`
- `actor_type`, nullable `actor_user_id` FK
- `is_customer_visible`
- `created_at`

### 9.9 Payments, payment events, refunds

#### `payments`
- `id` UUID PK
- `order_id` FK → orders
- `provider`
- `provider_order_id`, `provider_payment_id` nullable
- `status` (`CREATED`, `PENDING`, `AUTHORIZED`, `CAPTURED`, `FAILED`, `CANCELLED`, `PARTIALLY_REFUNDED`, `REFUNDED`)
- `amount_minor`, `currency`
- `idempotency_key`
- `failure_code`, sanitized `failure_message`
- `created_at`, `updated_at`, `captured_at`
Use uniqueness for provider IDs when present and idempotency keys within the correct scope.

#### `payment_events`
- `id`, `provider`, `provider_event_id`
- nullable `payment_id` FK
- `event_type`, `payload_hash` or carefully redacted payload
- `received_at`, `processed_at`, `processing_status`, `error_code`
Unique (`provider`, `provider_event_id`).

#### `refunds`
- `id`, `payment_id` FK, optionally `order_id` FK
- `provider_refund_id`
- `amount_minor`, `currency`
- `status` (`PENDING`, `SUCCEEDED`, `FAILED`, `CANCELLED`)
- `reason`, `created_at`, `updated_at`, `completed_at`

Never store full payment card numbers, CVV, PIN, or raw payment credentials.

### 9.10 Shipment and returns tables

#### `shipments`
- `id`, `order_id` FK
- `carrier`, `tracking_number`, `tracking_url`
- `status`, `shipped_at`, `delivered_at`, `created_at`, `updated_at`
#### `shipment_items`
- `id`, `shipment_id` FK, `order_item_id` FK, `quantity`
#### `shipment_events`
- `id`, `shipment_id` FK, nullable provider event ID, status, description, occurred/received timestamps

#### `return_requests`
- `id`, `order_id` FK, nullable `user_id` FK for guest flows
- `request_number` unique
- `status` (`REQUESTED`, `UNDER_REVIEW`, `APPROVED`, `REJECTED`, `IN_TRANSIT`, `RECEIVED`, `INSPECTED`, `REFUND_PENDING`, `REFUNDED`, `CLOSED`)
- `reason`, `customer_comment`
- `requested_at`, `reviewed_at`, `closed_at`, `created_at`, `updated_at`

#### `return_items`
- `id`, `return_request_id` FK, `order_item_id` FK
- `quantity`, optional `condition`, optional `inspection_notes`
- `restock_decision` (`PENDING`, `RESTOCK`, `DO_NOT_RESTOCK`)
- timestamps

A return request does not automatically mean a refund was issued or inventory was restocked.

### 9.11 Promotions, CMS, and operational tables

#### `coupons`
- `id`, unique normalized `code`, status, discount type/value, currency
- `starts_at`, `ends_at`, `minimum_spend_minor`
- `usage_limit`, `per_customer_limit`, `combinable`
- eligibility rules or normalized join tables for products/collections
- timestamps

#### `coupon_usages`
- `id`, `coupon_id` FK, `order_id` FK, nullable `user_id` FK, normalized customer key where guest policy requires it, discount amount, `created_at`
Use constraints/transactions to enforce limits and avoid duplicate redemption.

#### `content_pages`
- `id`, title, unique slug, body/content representation, SEO fields, status, published time, creator, timestamps
#### `content_revisions`
- `id`, page/content reference, revision number, content snapshot, editor, created time
#### `homepage_sections`
- `id`, section type, title, configuration JSON with strict schema validation, position, status, start/end time, timestamps
#### `banners`
- `id`, title, media reference, destination URL/path, alt text, status, position, scheduling fields
#### `store_settings`
- `id`, unique key, typed/validated value, description, updated by, updated time
Do not store raw secrets in general settings rows.

#### `audit_logs`
- `id`, actor user ID, action, entity type, entity ID, request ID, redacted before/after values, source/IP metadata subject to privacy policy, created time
Append-only for ordinary application users.

#### `notifications`
- `id`, nullable user/order references, channel, type, recipient, template key, status, provider message ID, attempts, last error, created/sent times

#### `background_jobs` or `outbox_events`
- `id`, event/job type, payload with no unnecessary secrets, status, attempts, next attempt time, processed time, last error, created time
Use this for durable email, webhook follow-up, and other retryable side effects.

#### `integration_configurations`
- `id`, provider, status, non-secret settings, secret reference (not raw secret), last successful check, updated by/time

### 9.12 Optional tables
- `wishlists`, `wishlist_items`
- `reviews`, `review_moderation_events`
- `restock_subscriptions`
- `search_events`/analytics events, with privacy-conscious retention
Add only when a feature is in scope.

## 10. Core relationships

```text
users 1 ---- 0..1 customer_profiles
users 1 ---- many addresses
users many ---- many roles via user_roles
roles many ---- many permissions via role_permissions

products 1 ---- many product_variants
products many ---- many categories via product_categories
products many ---- many collections via collection_products
products 1 ---- many product_media
product_variants 1 ---- many inventory_levels / inventory_movements / reservations

users 1 ---- many carts
carts 1 ---- many cart_items ---- 1 product_variant

users 0..1 ---- many orders
orders 1 ---- many order_items
orders 1 ---- 1..2 order_addresses
orders 1 ---- many payments ---- many payment_events
payments 1 ---- many refunds
orders 1 ---- many shipments ---- many shipment_items ---- order_items
orders 1 ---- many return_requests ---- many return_items ---- order_items
orders 1 ---- many order_status_events
```

Guest orders are supported by allowing `orders.user_id` to be null while requiring a valid contact email and order snapshots. Guest access must use a secure token/verification flow.

## 11. Critical business workflows

### 11.1 Product publish
1. Authenticate staff and check catalogue permission.
2. Validate product fields, slug uniqueness, media, variants, SKU uniqueness, prices, and required publication data.
3. In a transaction, update publication state and audit record.
4. Public catalogue endpoints only return products meeting publication/sale rules.
5. Cache invalidation/search indexing, if added, runs through a retry-safe event/job.

### 11.2 Inventory adjustment
1. Authenticate and authorize inventory action.
2. Validate variant, location, adjustment quantity, reason, and idempotency/reference.
3. Lock/update inventory safely in a transaction.
4. Insert immutable inventory movement and audit log.
5. Return new on-hand/reserved/available quantities.
6. Reject invalid negative stock unless a specific policy allows it.

### 11.3 Checkout/order placement
1. Resolve authenticated user or guest cart/contact identity.
2. Re-read current product/variant status and prices.
3. Validate coupon, shipping method, tax rules, currency, address, and quantities.
4. Calculate all totals on the server using integer minor units or exact Decimal arithmetic.
5. Use a transaction to create order, order-item snapshots, order-address snapshots, payment attempt, and reservation as designed.
6. Create/confirm provider payment session with idempotency.
7. Do not keep a database transaction open while waiting on slow external HTTP calls.
8. Verify payment from signed webhook/provider verification and compare amount/currency.
9. Atomically update payment/order state and commit or release reservation.
10. Queue confirmation/fulfilment notifications through an outbox/job.
11. Support retry/reconciliation for ambiguous provider outcomes.

### 11.4 Payment webhook
1. Read raw request body and verify provider signature before trusting payload.
2. Insert provider event under unique (`provider`, `provider_event_id`).
3. A duplicate event must not repeat side effects.
4. Match the event to a known payment/order.
5. Verify expected amount and currency.
6. Apply only allowed state transitions in a transaction.
7. Commit/release reservation as appropriate.
8. Enqueue notification and fulfilment tasks.
9. Record processing result and allow safe retries.

### 11.5 Refund
1. Check staff permission, order/payment state, captured balance, and refund policy.
2. Create a pending refund record with idempotency.
3. Call provider outside a long database transaction.
4. Reconcile provider result/webhook.
5. Update refund/payment/order states and audit log.
6. Do not refund more than the eligible captured amount.
7. Notify customer only when the state is persisted and the message is appropriate.

### 11.6 Return
1. Verify customer/guest ownership and eligibility.
2. Create request and status event.
3. Staff reviews according to policy.
4. Record receipt/inspection and restock decision.
5. Create refund through the payment workflow if approved.
6. Keep request, refund, and inventory states distinct.

## 12. State machines

Use explicit state transition functions in domain services. Never allow clients to patch statuses directly.

### Order
Typical path: `PENDING -> CONFIRMED -> PROCESSING -> PARTIALLY_FULFILLED/FULFILLED -> COMPLETED`; cancellation is allowed only from defined states. Exact transitions must be documented and tested.

### Payment
Typical path: `CREATED -> PENDING -> AUTHORIZED/CAPTURED`, or `FAILED/CANCELLED`. Refund transitions are tracked via refund records and aggregate payment status. Delayed and duplicate webhooks must be safe.

### Inventory reservation
`ACTIVE -> COMMITTED`, `RELEASED`, or `EXPIRED`; only one terminal transition should win under concurrency.

### Return
`REQUESTED -> UNDER_REVIEW -> APPROVED/REJECTED -> IN_TRANSIT -> RECEIVED -> INSPECTED -> REFUND_PENDING -> REFUNDED/CLOSED`, with valid branches defined by policy.

## 13. Security requirements

- Enforce HTTPS in production and TLS for Aiven PostgreSQL connections.
- Store secrets in environment/secret management, not Git, source files, or general database settings.
- Use strong password hashing through the selected auth library; never store raw passwords.
- Use secure session/token design, expiry, revocation, and cookie flags where cookie auth is used.
- Validate authorization at the API/service layer for every protected request.
- Verify customer ownership for addresses, carts, orders, returns, wishlists, and consent records.
- Enforce RBAC on every admin route and mutation.
- Verify payment/shipping webhook signatures and deduplicate events.
- Rate-limit login, verification, reset, guest order lookup, coupons, and other abuse-prone endpoints.
- Apply request-size limits and validate file uploads.
- Use parameterized ORM queries; do not concatenate SQL from user input.
- Protect against CSRF where relevant to cookie-based authentication; configure CORS for known frontend origins only.
- Avoid leaking stack traces, SQL details, internal notes, or sensitive provider responses.
- Redact tokens, credentials, payment information, and unnecessary personal data from logs.
- Never store full card number, CVV, PIN, or payment credentials.
- Use least-privilege Aiven users and private networking/allowlists where available.
- Define retention, deletion/anonymization, backup, restore, and incident response procedures.

## 14. Aiven PostgreSQL operations

Before production:
1. Create separate staging and production Aiven databases/projects or equivalent isolated resources.
2. Configure TLS and validate certificate/connection settings.
3. Create a least-privilege application user and separate migration/deployment credentials where feasible.
4. Store connection strings in deployment secrets; redact them from logs.
5. Configure SQLAlchemy connection pool limits to match the app's replica count and Aiven connection limits.
6. Use Alembic migrations reviewed in pull requests.
7. Run migrations as a controlled deployment step; do not run destructive migrations automatically on every API worker startup.
8. Configure backups and periodically test restore into a non-production environment.
9. Monitor connection count, query latency, CPU/memory/disk, storage growth, failed connections, and backup status.
10. Add indexes based on expected queries and verify expensive queries with `EXPLAIN ANALYZE`.
11. Use expand/contract migrations for changes that need zero/minimal downtime.
12. Never make production schema changes manually without recording them in migrations.

## 15. Required indexes and constraints

At minimum:
- Unique normalized `users.email` where present.
- Unique `customer_profiles.user_id` and `staff_profiles.user_id`.
- Unique `product_variants.sku`, `products.slug`, category/collection slugs.
- Index `product_variants.product_id`, join table foreign keys, publication status, and collection order.
- Unique (`variant_id`, `location_id`) for inventory levels.
- Index inventory movements by variant/location/time and reservations by status/expiry.
- Index cart by user/status/expiry; unique cart item (`cart_id`, `variant_id`) where appropriate.
- Unique `orders.order_number`; indexes (`user_id`, `created_at`), status, payment status, contact email.
- Index order items by order; unique (`order_id`, `address_type`) on order addresses.
- Unique payment idempotency key within its scope; unique provider identifiers where non-null.
- Unique (`provider`, `provider_event_id`) on payment events.
- Index shipments by order and tracking reference as appropriate.
- Unique return request number and index by order/status.
- Unique normalized coupon code; indexes on active date/status.
- Foreign keys, check constraints for positive quantities/non-negative amounts, and constraints that protect valid relationships.

Review index usefulness against actual query plans; every index adds write/storage cost.

## 16. API documentation and examples

Every endpoint must define:
- Method and path.
- Authentication requirements.
- Required permission/scope.
- Request schema and validation rules.
- Success response schema.
- Error codes/statuses.
- Pagination/filter/sort behavior.
- Idempotency behavior for mutations.
- Side effects and audit behavior.
- Rate-limit policy where relevant.
- Example requests/responses in OpenAPI.

Example cart mutation (illustrative only):

```json
{
  "variant_id": "uuid",
  "quantity": 1
}
```

The client must not submit an authoritative price. The backend looks up the variant and calculates the current cart totals.

Example checkout quote response (illustrative only):

```json
{
  "data": {
    "currency": "INR",
    "subtotal_minor": 499900,
    "discount_minor": 0,
    "shipping_minor": 0,
    "tax_minor": 0,
    "total_minor": 499900,
    "items": []
  },
  "meta": {
    "request_id": "request-correlation-id"
  }
}
```

All amounts must have documented units; for INR, minor units are paise. Do not use example values as actual product pricing.

## 17. Observability and operational reliability

- Structured JSON logs with request ID, route, status, latency, and safe actor/entity identifiers.
- Do not log secrets or full sensitive request bodies.
- Metrics: request rate, latency, errors, database pool saturation, job queue depth/age, payment webhook processing, checkout failures, inventory conflicts, and email failures.
- Liveness checks process health; readiness checks required dependencies without exposing credentials.
- Alert on repeated webhook failures, stuck jobs, database connection saturation, payment/order state mismatches, low disk, and failed backups.
- Provide an administrative reconciliation procedure for ambiguous payments and inventory/order inconsistencies.
- Use durable jobs/outbox for email and other critical side effects; retry with backoff and dead-letter/failed state.
- Define timeouts and retry policies for every provider call.

## 18. Testing requirements

### Unit tests
- Price/discount/shipping/tax calculations.
- Role/permission checks.
- Order/payment/return state transitions.
- Coupon eligibility and limits.
- Quantity and inventory availability rules.
- Address and input validation.

### Integration tests
- PostgreSQL schema, foreign keys, uniqueness, and transactions.
- Checkout creates consistent order/item/address/payment/reservation records.
- Payment webhook signature, deduplication, and state transition.
- Refund accounting and duplicate refund requests.
- Inventory race/overselling protection.
- Guest cart merge and guest order verification.
- Alembic migration upgrade path from an empty database and the previous release.

### API/security tests
- Unauthenticated calls receive the correct response.
- Customer A cannot read or modify Customer B's profile, address, cart, order, return, or wishlist.
- Support staff cannot perform admin-only refunds/role changes without permission.
- Browser cannot set payment/order state directly.
- Malformed and oversized input is rejected.
- Rate limits apply to abuse-prone routes.
- Sensitive data is not returned to unauthorized roles or written to logs.

### End-to-end scenarios
1. Admin creates a product with variants and stock, assigns it to EST.01, and publishes it.
2. Customer browses product, adds correct variant to cart, checks out, and pays.
3. Verified payment webhook confirms order once, even if delivered repeatedly.
4. Admin fulfils order and adds tracking; customer sees shipment status.
5. Customer requests a return; staff reviews; refund and inventory restock are separately recorded.
6. Guest checkout works and guest order details cannot be accessed by guessing an order number.
7. Payment failure/timeout does not result in a falsely paid order or permanently leaked stock reservation.

## 19. Delivery plan

### Phase 0 — foundation
- Review current repository and existing schema.
- Confirm auth, payment, shipping, media, email, tax, currency, and policy decisions.
- Set up Python service, configuration, logging, OpenAPI, SQLAlchemy, Alembic, Aiven staging DB, CI, and tests.
- Establish role/permission model and migration process.

### Phase 1 — catalogue and customer basics
- Public catalogue, product details, categories/collections.
- Admin product/variant/media/collection CRUD and publish workflow.
- Customer authentication/profile/address book.
- Inventory levels and auditable stock adjustments.
- Initial customer/admin frontend contracts.

### Phase 2 — cart, checkout, and payment
- Guest/account cart.
- Server-side pricing and checkout quote.
- Orders, order snapshots, inventory reservations.
- Payment provider integration, verified webhooks, idempotency, reconciliation.
- Customer order confirmation/history and admin order views.

### Phase 3 — fulfilment and support
- Shipments/tracking.
- Transactional notifications and durable job worker.
- Customer support lookup and staff permissions.
- Cancellation, returns, refunds, inspection/restock.
- Audit logs and operational reports.

### Phase 4 — polish and growth
- CMS/homepage sections, banners, SEO, scheduled publication.
- Coupons/promotions, wishlist, reviews, restock alerts.
- More advanced analytics and search.
- Performance optimization based on measured usage.

## 20. Decisions required before schema freeze

Product and engineering must explicitly decide:
1. Authentication provider/library and password vs magic-link/social login.
2. Guest checkout policy.
3. Payment provider (Razorpay is a candidate), payment methods, and webhook requirements.
4. Supported currency/currencies and money representation.
5. Tax/GST and invoice rules.
6. Shipping provider, shipping zones, rate calculation, and tracking.
7. Return/cancellation/exchange policy and eligible products.
8. Media storage provider.
9. Email provider and transactional templates.
10. Whether inventory is single-location at launch.
11. Whether coupons, wishlist, reviews, and marketing features are MVP.
12. Data retention, privacy, consent, and deletion policy.
13. Deployment platform, networking, CORS, domains, and allowed origins.
14. Existing tables/models and migration history that must be preserved.

Do not guess these business policies in code. Make them explicit configuration or documented decisions.

## 21. Definition of done

- [ ] Customer UI and admin/support UI consume the Python API; neither accesses PostgreSQL directly.
- [ ] Public and admin routes are separated and protected appropriately.
- [ ] All business rules are implemented in backend domain services.
- [ ] OpenAPI schemas cover all implemented endpoints.
- [ ] Aiven TLS, least-privilege users, staging/production separation, backups, and restore procedure are configured.
- [ ] Alembic migrations are reviewed, repeatable, and tested.
- [ ] Catalogue, customer, cart, order, payment, inventory, and return models have correct relationships and constraints.
- [ ] Checkout and webhook handling are idempotent and transaction-safe.
- [ ] Customer ownership and staff RBAC are tested.
- [ ] Order items and addresses are snapshotted.
- [ ] Payment secrets and card data are never stored inappropriately.
- [ ] Logs, metrics, alerts, and durable background jobs are configured.
- [ ] Unit, integration, API/security, and end-to-end tests pass.
- [ ] Error handling and recovery/reconciliation procedures are documented.
- [ ] Privacy, terms, shipping, payment, and return policy pages are available before public launch.

## 22. Final engineering direction

Build **one Python modular-monolith middleware/API** as the authority for FORM/01's business logic and database access. Use **one Aiven PostgreSQL database** initially. Build the customer storefront and admin/support console as separate clients that consume the same versioned API, with distinct authentication/authorization policies.

Before implementing migrations, engineers must inspect the current codebase and database, map existing tables to this logical schema, identify gaps, and propose a safe migration plan. This specification describes the intended target; it must not be applied blindly over an existing database.
