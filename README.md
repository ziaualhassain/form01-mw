# FORM/01 Unified Middleware

FastAPI-based commerce middleware for the FORM/01 storefront and admin UI.

## What is included

- Shared API for customer and admin clients
- PostgreSQL-backed SQLAlchemy models
- JWT authentication scaffold
- Public catalogue, cart, checkout, and admin product routes
- `POST /api/v1/cart/items` sets the absolute quantity for a variant (1–100).
  `DELETE /api/v1/cart/items/{item_id}` removes an item from the authenticated
  customer's active cart, returning 204 or 404 if it is not in that cart.
- Aiven-friendly configuration via environment variables
- Alembic migration entrypoint

## Admin time-based order queue

`GET /api/v1/admin/order-queue` is ADMIN/OWNER-only and private/no-store.
The older `GET /admin/orders` list remains backward-compatible for recent-order
dashboard consumers; the live Orders screen now uses this new queue:

- `period=today|7d|30d|all|custom` (default Today, Asia/Kolkata calendar dates).
- Custom `date_from` / `date_to` are inclusive ISO dates, 1970–9998, both required.
  Non-custom ranges reject date arguments. 7/30 days include today.
- Optional `q` (up to 150 characters) searches order reference/product snapshots,
  case-insensitively with literal wildcard characters.
- `stage` (including `incoming` for RECEIVED/CONFIRMED), `payment` and `fulfilment`
  accept the UI's enumerated statuses, defaulting to `all`.
- `limit=20` (1–100), `offset=0` (0–1,000,000), optional aware `snapshot_at`.

Responses contain `items`, `total`, `limit`, `offset`, `snapshot_at`, `new_orders`,
`server_today`, `range_start` and `range_end`. Summary rows include statuses,
currency/total, dates, first product and total item quantity. Full order details
remain at `GET /admin/orders/{order_number}`. Search/status/date filtering happens
before pagination, and related item summaries are batched, not per-order queries.

Sort is placed time (creation fallback), then creation time and ID descending.
Reusing the returned snapshot excludes later-created arrivals from pagination and
counts them in `new_orders` instead. Poll this same page/snapshot every 15 seconds
and compare its rows/count to announce changes without replacing the visible page.
Omit the snapshot to refresh; relative date windows are anchored to the snapshot,
and `server_today` lets the UI detect an IST midnight rollover. This cutoff is not
a transactional database snapshot: status/placed-time changes and delayed commits
can change membership, so refresh the queue after operational changes.

No migration is needed. Isolated date-boundary, >50-order, auth and arrival tests:
`python -m unittest discover -s tests -p test_admin_order_queue.py -v`.

## Admin customer directory

ADMIN/OWNER-only, read-only endpoints:

- `GET /api/v1/admin/customers?q=&limit=20&offset=0`
- `GET /api/v1/admin/customers/{customer_id}`
- `GET /api/v1/admin/customers/{customer_id}/orders?limit=20&offset=0`

List responses contain `items`, `total`, `limit`, and `offset`. The page size
is 1–100 (default 20), with nonnegative offsets up to 1,000,000. Search is
case-insensitive across email, profile names, full name and profile phone;
wildcard characters are treated literally. Results have a stable newest-first
creation-date/ID order. No extra database migration is required.

The directory includes non-staff registered users even without profiles/orders.
Users with ADMIN or OWNER roles are excluded from all three routes. Profiles
expose only account/contact fields, verification/sign-in dates and order
statistics—not password hashes, tokens, roles or payment credentials. Responses
are private/no-store. Unknown/staff IDs return 404, invalid query/UUIDs 422;
unauthenticated/non-admin requests return 401/403.

Order history is scoped strictly by `Order.user_id`, never matched by contact
email, and paginates all statuses (including pending/refunded), not just the
latest 50 orders. Counts are total orders and orders whose current payment status
is PAID, not lifetime spend or net revenue. Guest checkout records without a
registered user are not customer accounts.

Focused tests use an isolated in-memory SQLite database:
`python -m unittest discover -s tests -p test_admin_customers.py -v`.

## Draft GST settings and draft order PDFs

Migration `c0d3456e7f88` adds immutable GST configuration versions and nullable
order document snapshots. Install the updated project dependencies (including
ReportLab), then run `alembic upgrade head` on the approved target database
before starting this middleware version. Deploy admin/store afterward.

This is deliberately **draft only**: registration is unknown, no GST rates are
seeded, prices are planned as GST-exclusive, and GST collection remains disabled.
The API rejects attempts to activate collection. Seller defaults are Shaik Ziaual
Hassain, Telangana state code 36; address and GSTIN are unconfirmed. An accountant
must verify registration, classifications, current law, place-of-supply rules
and legal numbering before a separate activation implementation is approved.
The Razorpay article is only an editable research citation, not a legal authority.

- ADMIN/OWNER: `GET` / `PUT /api/v1/admin/gst`. Each PUT appends a version.
- ADMIN/OWNER: `POST /api/v1/admin/gst/preview`. Simulates editor-supplied draft
  settings without saving or changing checkout.
- ADMIN/OWNER: `GET /api/v1/admin/orders/{order_number}/draft-invoice`.
- Customer owner: `GET /api/v1/me/orders/{order_number}/draft-invoice`.

Rules match normalized product types and hold an HSN, inclusive start date,
exclusive optional end date, and ordered per-unit slabs with inclusive upper
bounds. The final slab is unlimited. Rates use integer basis points (500 = 5%),
amounts use integer paise. Same-type effective windows cannot overlap. The
simulation allocates discounts proportionally using largest remainders, applies
the configured before/after-discount threshold basis, and rounds each line tax
half up to paise. It shows CGST/SGST for same-state domestic examples and IGST
otherwise; odd local paise goes to SGST in this illustrative model. It does not
support UTGST, exports or special place-of-supply cases. Missing rules are errors,
not silent zero-tax defaults.

New orders snapshot seller details, configuration version, delivery address,
item prices and actual totals at checkout. Later edits never rewrite those
snapshots. Draft rules are not used to charge taxes or label zero-rated goods.
After confirmed payment (also after refunds), both interfaces offer an
authenticated PDF marked **DRAFT - NOT A TAX INVOICE on every page**. These
documents are not receipts, legal invoices, refund statements or credit notes;
no legal invoice number, IRN, GST filing or email is generated. Old orders
without a snapshot return 409 rather than inventing historical details.
Downloads are private/no-store. The initial built-in PDF font supports WinAnsi
text; unsupported scripts return an explicit 422 rather than replacing names.

## Coupons and payment-confirmed cart cleanup

Apply migration `a8b1234c5d66` with `alembic upgrade head` before deploying the
updated API, then deploy admin/store. It adds coupons, usage reservations, and
order cart snapshots. Existing orders have no cart snapshot and are not
retroactively cleared.

ADMIN/OWNER endpoints:

- `GET /api/v1/admin/coupons`
- `POST /api/v1/admin/coupons`
- `PUT /api/v1/admin/coupons/{id}` (full rule replacement)
- `DELETE /api/v1/admin/coupons/{id}` (archive, returns 204)

Archiving hides the coupon from the admin list and blocks new use without
deleting redemption or order history. Archived codes remain reserved and cannot
be reused or edited; existing pending payments retain their discounted total.
Repeated archive requests are safe. Migration `b9c2345d6e77` adds the archive
timestamp; run `alembic upgrade head` before starting the updated middleware.
An end date already blocks new use automatically at that instant; expiration
does not delete/archive the record and requires no scheduled job.

Example: 10% off, capped at INR 100, minimum subtotal INR 500:

```json
{
  "code": "WELCOME10",
  "discount_type": "PERCENTAGE",
  "value": 10,
  "max_discount_minor": 10000,
  "min_subtotal_minor": 50000,
  "starts_at": null,
  "ends_at": null,
  "usage_limit": 100,
  "per_customer_limit": 1,
  "enabled": true
}
```

`FIXED` values and monetary fields are integer paise; percentage values are
whole percentages (1-100). Dates require timezone offsets and the end is
exclusive. Codes are case-insensitive and normalized to uppercase. One coupon
applies to the entire product subtotal; offers cannot stack. Discounts round
down to paise and are limited so at least INR 1 remains payable through Razorpay.
Shipping/tax remain zero under the existing pricing rules.

Authenticated quote and checkout requests accept optional `coupon_code`.
The quote returns `coupon_code`, `discount_minor`, and the discounted
`total_minor`. Checkout recalculates from database prices/rules, snapshots the
code/discount on the order, and passes that total to Razorpay. Clients cannot
supply discount amounts. A quote does not reserve usage.

Checkout locks the coupon row before checking total/per-customer limits and
reserving usage in the order transaction. Pending payments occupy usage slots.
Payment verification and webhooks share idempotent completion: successful
payment redeems the slot, consumes stock, and removes purchased quantities from
the original cart item IDs. Later additions and removed/re-added lines are
preserved. Failures, payment expiry, and order cancellation release reserved
coupon uses and stock without clearing the cart. Refunded successful uses remain
counted; disabling/editing a coupon does not change existing pending order totals.
Closing Razorpay without paying leaves the pending reservation until expiry.
Payments received after local failure/expiry require explicit reconciliation
(409), not automatic re-use of released stock/coupon slots.

Cart reads and quotes no longer silently delete/reduce items when stock changes,
including stock held by a pending checkout. Availability is still returned;
checkout stock reservation remains authoritative.

Run `python -m unittest discover -s tests -v`. Coupon lifecycle tests use an
isolated SQLite database and mocked provider calls, not the live database or
Razorpay. PostgreSQL row-lock concurrency must also be exercised in staging.

## Order status updates

`PATCH /api/v1/admin/orders/{order_number}/status` commits the order's `status`
and its status event to the database. Customer list/detail endpoints expose that
same `status`, separately from `payment_status`, `fulfilment_status`, and
individual shipment statuses. The event's `is_customer_visible` flag controls
event visibility, not whether the current order status is returned.

Marking an order `FULFILLED` or `DELIVERED` also sets its `fulfilment_status`
to `FULFILLED`. Re-saving either status repairs older inconsistent records
without creating a duplicate status event. This does not create a shipment,
mark a payment paid, or infer delivery from fulfilment.

Run the focused regression tests with:
`python -m unittest discover -s tests -p test_order_status.py -v`.

## Public policies

- `GET /api/v1/policies` lists the six policy slugs, titles, and publication status.
- `GET /api/v1/policies/{slug}` returns `slug`, `title`, `published`,
  `last_updated`, and `content_markdown` for an approved document.
- Unapproved policies return **409**, unknown slugs **404**, and an unreadable
  or invalid approved source **503**. These endpoints are public and need no
  account or database connection.

The source is `FORM01_Ecommerce_Policy_Pack.md`. It remains an unpublished draft:
no draft body or placeholder text is exposed by the API. Finalize the legal and
operational details, remove drafting instructions, replace all placeholders,
set the last-updated date, and obtain approval before publication.

After approval, set `POLICY_PACK_APPROVED_SHA256` to the exact SHA-256 digest of
the finalized UTF-8 file (`shasum -a 256 FORM01_Ecommerce_Policy_Pack.md`) and
restart the middleware. Leave it unset to withhold all policies. Any subsequent
file change automatically withholds the pack until its new digest is approved;
matching the digest alone does not bypass unresolved-placeholder validation.
Keep the policy pack beside `app/` in deployments; the wheel includes it there.
This approval covers the whole pack, not individual sections.

The storefront footer links these six policy pages; Contact us and Grievance
redressal use the same contact/grievance document. Finalization is a content/legal
approval step, not a migration or a customer-facing write endpoint.

## Environment

The service expects a PostgreSQL connection from one of these sources:

- `DATABASE_URL`
- `AIVEN_DATABASE_URL`
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE`

Recommended app variables:

- `APP_NAME`
- `ENVIRONMENT`
- `SECRET_KEY`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `CORS_ORIGINS`
- `PASSWORD_HASH_SCHEME`
- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`

Default CORS origins include local ports 3000/3001 and the deployed admin/store:
`https://form01-admin.vercel.app` and `https://form01-store-xi.vercel.app`.
An explicit `CORS_ORIGINS` environment variable replaces those defaults.
For Render, set it to the following JSON array (without trailing slashes) and
redeploy:

```json
["https://form01-admin.vercel.app","https://form01-store-xi.vercel.app"]
```

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies from `pyproject.toml`.
3. Set environment variables.
4. Run the API with Uvicorn.

## Commands

### Install dependencies

```bash
python -m pip install -e .
```

### Start the API

```bash
uvicorn app.main:app --reload
```

### Run database migrations

```bash
alembic upgrade head
```

### Create a new migration

```bash
alembic revision --autogenerate -m "describe change"
```

### Run tests

```bash
pytest
```

### Format and lint checks

If you add formatting or linting tools later, document them here so both UIs and the middleware follow the same workflow.

## API endpoints

All frontend clients should use the versioned API under `/api/v1`. Public endpoints are available without a bearer token, while customer and admin endpoints require authentication.

### Public endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/products` | List public products |
| `GET` | `/api/v1/products/{slug}` | Get product detail by slug |
| `GET` | `/api/v1/storefront/config` | Get public storefront config |
| `GET` | `/api/v1/storefront/home` | Get the published, ordered homepage story and resolved product sections |

### Authentication endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/register` | Register a customer account |
| `POST` | `/api/v1/auth/login` | Log in and receive a bearer token |
| `GET` | `/api/v1/auth/me` | Get authenticated user context |

### Customer endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/me/profile` | Get current customer profile |
| `PATCH` | `/api/v1/me/profile` | Update current customer profile |
| `GET` | `/api/v1/me/addresses` | List saved addresses |
| `POST` | `/api/v1/me/addresses` | Create a saved address |
| `DELETE` | `/api/v1/me/addresses/{address_id}` | Delete a saved address |
| `GET` | `/api/v1/me/orders` | List customer orders |
| `GET` | `/api/v1/me/orders/{order_number}` | Get customer order detail |
| `POST` | `/api/v1/me/orders/{order_number}/cancel-request` | Request order cancellation |
| `GET` | `/api/v1/cart` | Get the active cart |
| `POST` | `/api/v1/cart/items` | Add or update a cart item |
| `POST` | `/api/v1/checkout/quote` | Calculate checkout totals |
| `POST` | `/api/v1/checkout` | Create an order from the cart and initialize a payment provider order |
| `POST` | `/api/v1/payments/razorpay/verify` | Verify a successful Razorpay payment from the client |

Catalogue variants expose `on_hand`, `reserved`, and `available`. Cart and checkout quote items expose `available_quantity`. The storefront should disable quantities above `available`; checkout still performs the authoritative row-locked stock check because availability can change between display and purchase.

### Admin endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/admin/products` | List admin products |
| `POST` | `/api/v1/admin/products` | Create a product |
| `POST` | `/api/v1/admin/products/{product_id}/publish` | Publish a product |
| `GET` | `/api/v1/admin/orders` | List all orders |
| `GET` | `/api/v1/admin/orders/{order_number}` | Get an order by order number |
| `POST` | `/api/v1/admin/orders/{order_number}/shipments` | Create a shipment |
| `POST` | `/api/v1/admin/payments/{payment_id}/refund` | Create a refund |
| `POST` | `/api/v1/admin/inventory/adjust` | Add or remove stock for a variant |
| `GET` | `/api/v1/admin/homepage` | Review homepage sections and scheduling |
| `PUT` | `/api/v1/admin/homepage` | Replace the ordered homepage content configuration |

### Homepage content model

The homepage is a shared, backend-owned composition for web, mobile, and app clients. Use `GET /api/v1/storefront/home` for one stable response containing ordered sections such as `hero`, `editorial`, `product_collection`, `statement`, and `announcement`. Product collection sections resolve current active product data; they do not duplicate prices or inventory.

Admin content can be replaced with a JSON array through `PUT /api/v1/admin/homepage`:

```json
[
	{
		"section_key": "hero-est-01",
		"section_type": "hero",
		"position": 1,
		"enabled": true,
		"content": {
			"eyebrow": "FORM/01 — EST. 01",
			"title": "Built beyond trends.",
			"subtitle": "A more thoughtful tomorrow.",
			"image": {
				"desktop_url": "https://cdn.example.com/est01-hero.webp",
				"mobile_url": "https://cdn.example.com/est01-hero-mobile.webp",
				"alt": "FORM/01 EST. 01 hoodie in a minimal studio"
			},
			"cta": { "label": "Explore the first form", "href": "/products" },
			"overlay": "dark"
		}
	},
	{
		"section_key": "featured-products",
		"section_type": "product_collection",
		"position": 2,
		"enabled": true,
		"content": {
			"title": "The first form.",
			"product_limit": 4,
			"product_slugs": ["utility-shell", "signal-knit-02"]
		}
	}
]
```

### Webhook endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/webhooks/{provider}` | Receive provider webhook events |

### Auth rules

- Customer routes require a valid `Authorization: Bearer <token>` header.
- Admin routes require a valid token and an `ADMIN` or `OWNER` role.
- Public routes do not require authentication.

### Getting an admin bearer token

New registrations receive `CUSTOMER` access. Promote an existing account before using the admin UI:

```bash
cd /Users/ziasmacm2/Documents/Work-Space/form01-mw
source .venv/bin/activate
python scripts/promote_admin.py your-email@example.com ADMIN
```

Then log in through `POST /api/v1/auth/login` with the account email and password. The response contains `access_token`; send it to protected routes as:

```http
Authorization: Bearer <access_token>
```

### Response shape

- Successful APIs generally return data in a `data` envelope when the endpoint uses the shared contract.
- Errors use a consistent `error` envelope with a code, message, details, and request ID.

## Frontend Razorpay Checkout

The checkout endpoint creates the internal order and Razorpay order. The response includes `provider_order_id` and the public `provider_key_id`; only the key ID may be used in browser code. Never expose `RAZORPAY_KEY_SECRET` or `RAZORPAY_WEBHOOK_SECRET` to the frontend.

Load Razorpay Checkout once in the storefront HTML or app layout:

```html
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
```

Example browser flow:

```javascript
async function startRazorpayCheckout(token) {
	const checkoutResponse = await fetch("http://localhost:8000/api/v1/checkout", {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Authorization: `Bearer ${token}`,
		},
		body: JSON.stringify({
			payment_provider: "razorpay",
			idempotency_key: crypto.randomUUID(),
		}),
	});

	if (!checkoutResponse.ok) {
		throw new Error("Unable to create checkout");
	}

	const checkout = await checkoutResponse.json();
	const options = {
		key: checkout.provider_key_id,
		amount: checkout.total_minor,
		currency: "INR",
		name: "FORM/01",
		order_id: checkout.provider_order_id,
		handler: async function (response) {
			const verificationResponse = await fetch("http://localhost:8000/api/v1/payments/razorpay/verify", {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					Authorization: `Bearer ${token}`,
				},
				body: JSON.stringify({
					razorpay_order_id: response.razorpay_order_id,
					razorpay_payment_id: response.razorpay_payment_id,
					razorpay_signature: response.razorpay_signature,
				}),
			});

			if (!verificationResponse.ok) {
				throw new Error("Payment verification failed");
			}

			window.location.assign(`/orders/${checkout.order_number}`);
		},
		modal: {
			ondismiss: function () {
				console.info("Razorpay checkout dismissed");
			},
		},
	};

	const razorpay = new window.Razorpay(options);
	razorpay.open();
}
```

The frontend success callback is only a user-facing confirmation path. Razorpay webhooks remain the authoritative retry-safe payment update path, so configure the webhook URL as:

```text
https://your-api.example.com/api/v1/webhooks/razorpay
```

## Customer and admin workflow flows

These flows show how endpoints chain together to produce a transaction or state change. They also highlight areas that still need to be completed for a full production system.

### Customer flow: sign up, browse, cart, checkout, order

| Step | Endpoint | What happens | Transaction boundary | Gaps to close |
| --- | --- | --- | --- | --- |
| 1 | `POST /api/v1/auth/register` | Creates the customer user and profile | Single DB transaction | Email verification flow is not yet implemented |
| 2 | `POST /api/v1/auth/login` | Returns bearer token | Read-only auth check | Refresh tokens and session revocation are not yet implemented |
| 3 | `GET /api/v1/products` / `GET /api/v1/products/{slug}` | Loads public catalogue | No write transaction | Product search, filters, and stock display still need expansion |
| 4 | `GET /api/v1/cart` | Loads active cart | No write transaction | Guest cart support and merge logic are not yet implemented |
| 5 | `POST /api/v1/cart/items` | Adds or updates cart line | Single write transaction | Inventory validation and pricing snapshots are not yet enforced |
| 6 | `POST /api/v1/checkout/quote` | Calculates totals from current cart data | Read-only calculation | Shipping, tax, coupon, and promotion logic are not yet wired in |
| 7 | `POST /api/v1/checkout` | Locks available stock, creates the order, order items, and a Razorpay order | Single transaction for reservation and order creation | Reservation expiry for abandoned checkouts is still needed |
| 8 | `POST /api/v1/payments/razorpay/verify` | Verifies the signature, provider order, amount, currency, and payment state | Separate payment confirmation transaction | No known partial gap in the implemented Razorpay verification path |
| 9 | `POST /api/v1/webhooks/{provider}` | Verifies, deduplicates, validates, and processes payment events | Separate payment transaction | Additional provider event types can be added as needed |

### Admin flow: create product, publish, manage fulfilment

| Step | Endpoint | What happens | Transaction boundary | Gaps to close |
| --- | --- | --- | --- | --- |
| 1 | `POST /api/v1/auth/login` | Admin signs in and gets bearer token | Read-only auth check | Admin seeding and role assignment are not yet included |
| 2 | `POST /api/v1/admin/products` | Creates a draft product | Single write transaction | Variants, media, categories, and validation are not yet implemented |
| 3 | `POST /api/v1/admin/products/{product_id}/publish` | Marks product as active | Single write transaction | Publish rules, audit history, and completeness checks are not yet implemented |
| 4 | `GET /api/v1/admin/products` | Lists admin catalogue | No write transaction | Admin product detail/edit/archive endpoints are missing |
| 5 | `GET /api/v1/admin/orders` | Views placed orders | No write transaction | Admin order endpoints and fulfilment actions are not yet implemented |
| 6 | `POST /api/v1/admin/orders/{order_number}/shipments` | Creates shipment records | Single write transaction | Shipment endpoints and tracking integration are missing |
| 7 | `POST /api/v1/admin/payments/{payment_id}/refund` | Creates refund record | Separate refund transaction | Refund provider integration and reconciliation are not yet implemented |

### Transaction chain summary

| Chain | Endpoint sequence | Expected atomic part |
| --- | --- | --- |
| Customer order placement | `auth/register` -> `auth/login` -> `cart/items` -> `checkout/quote` -> `checkout` | `checkout` must atomically create order, items, addresses, and payment intent record |
| Payment confirmation | `webhooks/{provider}` -> payment state update | Webhook processing must be idempotent and safe to retry |
| Admin product launch | `admin/products` -> `admin/products/{id}/publish` | Product creation and publish should each be transactional and auditable |
| Fulfilment | `admin/orders` -> `admin/orders/{order_number}/shipments` | Shipment creation and order status update must be transactional |

### Current gaps in the codebase

- Checkout now creates an order and a Razorpay order when Razorpay credentials are configured.
- Razorpay payment verification validates the signature, provider order, charged amount, currency, and payment status; repeated successful verification is idempotent.
- Webhooks verify Razorpay signatures, validate payment totals, deduplicate events, record processing timestamps, and update payment/order state.
- Inventory levels, row-locked checkout reservations, payment-success consumption, payment-failure release, and admin stock adjustments are implemented.
- Reservation expiry for abandoned unpaid checkouts and scheduled cleanup are still needed.
- Razorpay refunds now call the provider API and persist the provider refund ID after local eligibility validation.
- Guest checkout, guest cart merge, and order recovery flows are still not built.
- Audit logging is defined in the model but not wired into every sensitive mutation.
- Role assignment for staff/admin users is still not seeded or managed through an admin UI.
- Product variants, inventory, shipping rates, tax, coupons, returns, and carrier tracking still need full business-rule coverage.

These gaps are useful checkpoints for the next implementation phase.

## Next steps

- Add Alembic migrations for the initial schema
- Add reservation expiry and scheduled cleanup for abandoned checkouts
- Add carrier tracking integration once a shipping provider is selected
- Expand inventory, order fulfilment, returns, and reporting workflows
- Connect the two UIs to the versioned `/api/v1` routes

## Database tables and relations

### Core identity and access

| Table | Purpose | Key relations |
| --- | --- | --- |
| `users` | Shared identity record for customers and staff | Parent of `customer_profiles`, `addresses`, `carts`, `orders`, `audit_logs` |
| `roles` | Role definitions such as `CUSTOMER`, `ADMIN`, `OWNER` | Linked to `users` through `user_roles` |
| `user_roles` | Join table between users and roles | `user_id` -> `users.id`, `role_id` -> `roles.id` |
| `customer_profiles` | Customer-only profile data | `user_id` -> `users.id` |
| `staff_profiles` | Staff-only profile data | `user_id` -> `users.id` |

### Customer data

| Table | Purpose | Key relations |
| --- | --- | --- |
| `addresses` | Saved shipping and billing addresses | `user_id` -> `users.id` |
| `customer_consents` | Marketing and consent history | Optional `user_id` -> `users.id` |
| `carts` | Active shopping carts | Optional `user_id` -> `users.id` |
| `cart_items` | Items in a cart | `cart_id` -> `carts.id`, `variant_id` -> `product_variants.id` |

### Catalogue and content

| Table | Purpose | Key relations |
| --- | --- | --- |
| `products` | Product master record | Parent of `product_variants`, `product_media`, `product_categories`, `product_tags`, `collection_products` |
| `product_variants` | Variant-level SKU and pricing | `product_id` -> `products.id` |
| `product_options` | Option groups like size or colour | `product_id` -> `products.id` |
| `product_option_values` | Values inside an option group | `option_id` -> `product_options.id` |
| `variant_option_values` | Variant-to-option-value mapping | `variant_id` -> `product_variants.id`, `option_value_id` -> `product_option_values.id` |
| `product_media` | Product media placement | `product_id` -> `products.id`, optional `variant_id` -> `product_variants.id`, `media_asset_id` -> `media_assets.id` |
| `media_assets` | Stored media metadata | Referenced by `product_media`, `collections`, `banners` |
| `categories` | Catalogue categories | Self-relation through `parent_id` |
| `product_categories` | Product-category join table | `product_id` -> `products.id`, `category_id` -> `categories.id` |
| `tags` | Normalised tag names | Linked to `products` through `product_tags` |
| `product_tags` | Product-tag join table | `product_id` -> `products.id`, `tag_id` -> `tags.id` |
| `collections` | Drops, launches, and curated collections | Linked to `products` through `collection_products` |
| `collection_products` | Collection-product join table | `collection_id` -> `collections.id`, `product_id` -> `products.id` |
| `content_pages` | CMS pages | Versioned by `content_revisions` |
| `content_revisions` | Historical page revisions | `content_page_id` -> `content_pages.id` |
| `homepage_sections` | Homepage blocks and modules | Often references media/assets and store settings |
| `banners` | Promotional banner records | May reference `media_assets` |

### Inventory and fulfilment

| Table | Purpose | Key relations |
| --- | --- | --- |
| `inventory_locations` | Warehouses or stock locations | Parent of `inventory_levels` and `inventory_movements` |
| `inventory_levels` | Current stock by variant and location | `variant_id` -> `product_variants.id`, `location_id` -> `inventory_locations.id` |
| `inventory_movements` | Immutable stock movement log | `variant_id` -> `product_variants.id`, `location_id` -> `inventory_locations.id` |
| `inventory_reservations` | Reserved stock for carts or orders | `variant_id` -> `product_variants.id`, optional `order_id` -> `orders.id`, optional `cart_id` -> `carts.id` |
| `shipments` | Shipment headers | `order_id` -> `orders.id` |
| `shipment_items` | Shipment line items | `shipment_id` -> `shipments.id`, `order_item_id` -> `order_items.id` |
| `shipment_events` | Shipment tracking history | `shipment_id` -> `shipments.id` |

### Orders, payment, and returns

| Table | Purpose | Key relations |
| --- | --- | --- |
| `orders` | Order header and totals | `user_id` -> `users.id` when registered customer exists |
| `order_items` | Snapshotted purchased items | `order_id` -> `orders.id`, optional `product_id` -> `products.id`, optional `variant_id` -> `product_variants.id` |
| `order_addresses` | Snapshotted shipping/billing addresses | `order_id` -> `orders.id` |
| `order_status_events` | Order status history | `order_id` -> `orders.id` |
| `payments` | Payment attempts and status | `order_id` -> `orders.id` |
| `payment_events` | Webhook and provider event log | Optional `payment_id` -> `payments.id` |
| `refunds` | Refund attempts | `payment_id` -> `payments.id`, optional `order_id` -> `orders.id` |
| `return_requests` | Customer return cases | `order_id` -> `orders.id`, optional `user_id` -> `users.id` |
| `return_items` | Items inside a return request | `return_request_id` -> `return_requests.id`, `order_item_id` -> `order_items.id` |

### Promotions, settings, and audit

| Table | Purpose | Key relations |
| --- | --- | --- |
| `coupons` | Discount code definitions | Referenced by `coupon_usages` |
| `coupon_usages` | Coupon redemption records | `coupon_id` -> `coupons.id`, `order_id` -> `orders.id`, optional `user_id` -> `users.id` |
| `store_settings` | Store-level configuration | Used by API and admin UI |
| `audit_logs` | Append-only audit trail | Optional `actor_user_id` -> `users.id` |
| `notifications` | Outbound notification records | May reference users or orders |
| `background_jobs` / `outbox_events` | Durable async work queue | Used for emails, webhooks, and retries |
| `integration_configurations` | Provider configuration metadata | Used by payment, shipping, email, and media providers |

### Main relationship map

| Parent | Child |
| --- | --- |
| `users` | `customer_profiles`, `addresses`, `carts`, `orders`, `audit_logs` |
| `roles` | `user_roles` |
| `products` | `product_variants`, `product_media`, `product_categories`, `product_tags`, `collection_products` |
| `product_variants` | `cart_items`, `order_items`, `inventory_levels`, `inventory_movements`, `inventory_reservations` |
| `carts` | `cart_items` |
| `orders` | `order_items`, `order_addresses`, `payments`, `shipments`, `return_requests`, `order_status_events` |
| `payments` | `payment_events`, `refunds` |
| `return_requests` | `return_items` |
| `collections` | `collection_products` |
