import os
import json
import re
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

_test_db = tempfile.TemporaryDirectory(prefix="nimbus-tests-")
os.environ["NIMBUS_DB_PATH"] = os.path.join(_test_db.name, "test.sqlite3")
os.environ["NIMBUS_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-tests"
os.environ["NIMBUS_ADMIN_EMAIL"] = "admin@example.test"

import app as nimbus


class NimbusSecurityAndBillingTests(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.TemporaryDirectory(prefix="nimbus-case-")
        nimbus.DB_PATH = Path(self.database.name) / "case.sqlite3"
        nimbus.initialize_db()
        self.codes = {}
        self.original_send_code = nimbus.send_login_code
        nimbus.send_login_code = lambda email, code: self.codes.update({email: code})
        self.original_admin_notice = nimbus.send_admin_notification
        self.original_customer_notice = nimbus.send_customer_payment_email
        nimbus.send_admin_notification = lambda subject, body: None
        nimbus.send_customer_payment_email = lambda email, subject, body: None
        self.admin = self.sign_in("admin@example.test")

    def tearDown(self):
        nimbus.send_login_code = self.original_send_code
        nimbus.send_admin_notification = self.original_admin_notice
        nimbus.send_customer_payment_email = self.original_customer_notice
        self.database.cleanup()

    def sign_in(self, email):
        client = nimbus.app.test_client()
        response = client.post("/api/auth/request-otp", json={"email": email})
        self.assertEqual(response.status_code, 200)
        response = client.post(
            "/api/auth/verify-otp",
            json={"email": email, "code": self.codes[email]},
        )
        self.assertEqual(response.status_code, 201)
        return client, response.get_json()["csrf_token"]

    def test_dns_must_be_public_and_connections_use_pinned_ip(self):
        nimbus.normalize_crawl_url("https://example.com")
        with self.assertRaises(ValueError):
            nimbus.normalize_crawl_url("http://127.0.0.1")
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 0)),
        ]
        with patch.object(nimbus.socket, "getaddrinfo", return_value=records):
            with self.assertRaisesRegex(ValueError, "private or reserved"):
                nimbus.ensure_public_host("https://example.com/")

        connection = nimbus.PinnedHTTPConnection("example.com", "8.8.8.8", 80, 8)
        fake_socket = Mock()
        with patch.object(nimbus.socket, "create_connection", return_value=fake_socket) as connect:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 80))
        connection.close()

    def test_crawler_rejects_redirects_to_another_hostname(self):
        response = Mock(status=302)
        response.getheader.return_value = "https://other.example.test/private"
        connection = Mock()
        connection.getresponse.return_value = response
        with (
            patch.object(nimbus, "ensure_public_host", return_value=["8.8.8.8"]),
            patch.object(nimbus, "PinnedHTTPConnection", return_value=connection),
        ):
            with self.assertRaisesRegex(ValueError, "redirected to another domain"):
                nimbus.fetch_public_page(None, "http://example.test/")
        connection.close.assert_called_once()

    def test_expired_subscription_reverts_to_free_site_allowance(self):
        customer, _csrf = self.sign_in("expired@example.test")
        first_site = customer.get("/api/sites").get_json()[0]
        with nimbus.connect_db() as connection:
            owner_id = connection.execute(
                "SELECT owner_id FROM sites WHERE id = ?", (first_site["id"],)
            ).fetchone()["owner_id"]
            connection.execute(
                """
                UPDATE users
                SET plan = 'pro', website_limit = 2, subscription_expires_at = ?
                WHERE id = ?
                """,
                (
                    (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).isoformat(sep=" "),
                    owner_id,
                ),
            )
            connection.execute(
                "INSERT INTO sites (id, name, owner_id) VALUES ('second-site', 'Second', ?)",
                (owner_id,),
            )
        first = nimbus.get_site(first_site["id"])
        second = nimbus.get_site("second-site")
        request_id, _usage, limit, reason = nimbus.start_chat_request(first, "visitoridentifier123")
        self.assertIsNotNone(request_id)
        self.assertEqual(limit, nimbus.FREE_REQUESTS_PER_MONTH or None)
        self.assertIsNone(reason)
        _request_id, _usage, _limit, reason = nimbus.start_chat_request(
            second, "visitoridentifier456"
        )
        self.assertEqual(reason, "website_limit")

    def test_admin_can_delete_customer_account(self):
        customer, _csrf = self.sign_in("delete-me@example.test")
        admin_client, admin_csrf = self.admin
        with nimbus.connect_db() as connection:
            user = connection.execute(
                "SELECT id, email FROM users WHERE email = 'delete-me@example.test'"
            ).fetchone()
            site_id = customer.get("/api/sites").get_json()[0]["id"]
            connection.execute(
                """
                INSERT INTO chat_requests (id, owner_id, site_id, visitor_id, status)
                VALUES ('delete-user-chat', ?, ?, 'visitoridentifier123', 'success')
                """,
                (user["id"], site_id),
            )
            connection.execute(
                """
                INSERT INTO plan_requests (id, user_id, requested_plan)
                VALUES ('delete-user-plan', ?, 'pro')
                """,
                (user["id"],),
            )
            connection.execute(
                """
                INSERT INTO payment_orders
                    (id, user_id, requested_plan, term_months, requested_websites,
                     amount_usd, network, wallet_address, transaction_hash)
                VALUES ('delete-user-payment', ?, 'pro', 1, 1, 5, 'TRC20', ?, ?)
                """,
                (user["id"], "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "delete-user-transaction"),
            )
            user_id = user["id"]
            email = user["email"]
        response = admin_client.delete(
            f"/api/admin/users/{user_id}",
            headers={"X-CSRF-Token": admin_csrf},
        )
        self.assertEqual(response.status_code, 200)
        unauthenticated = customer.get("/api/auth/me")
        self.assertEqual(unauthenticated.status_code, 200)
        self.assertEqual(unauthenticated.get_json(), {"authenticated": False})
        with nimbus.connect_db() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sites WHERE owner_id = ?", (user_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_requests WHERE owner_id = ? OR site_id = ?",
                    (user_id, site_id),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM plan_requests WHERE user_id = ?", (user_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM payment_orders WHERE user_id = ?", (user_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM login_codes WHERE email = ? COLLATE NOCASE",
                    (email,),
                ).fetchone()[0],
                0,
            )

    def test_admin_workspace_and_metrics_apis_hide_customer_website_details(self):
        customer, customer_csrf = self.sign_in("private-site@example.test")
        customer_site = customer.get("/api/sites").get_json()[0]
        updated = customer.put(
            f"/api/sites/{customer_site['id']}",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "name": "private-customer-name-84721",
                "website": "https://private-customer-domain.example.test",
                "knowledge": "private customer knowledge marker 84721",
            },
        )
        self.assertEqual(updated.status_code, 200)

        admin_client, admin_csrf = self.admin
        own_site = admin_client.post(
            "/api/sites",
            headers={"X-CSRF-Token": admin_csrf},
            json={"name": "Admin's own workspace", "website": ""},
        )
        self.assertEqual(own_site.status_code, 201)
        admin_sites = admin_client.get("/api/sites").get_json()
        self.assertNotIn(customer_site["id"], [site["id"] for site in admin_sites])
        self.assertTrue(admin_sites)
        self.assertEqual(
            admin_client.get(f"/api/sites/{admin_sites[0]['id']}").status_code,
            200,
        )
        self.assertEqual(admin_client.get(f"/api/sites/{customer_site['id']}").status_code, 404)
        metrics_response = admin_client.get("/api/admin/metrics")
        self.assertEqual(metrics_response.status_code, 200)
        metrics = metrics_response.get_json()
        self.assertNotIn("websites", metrics)
        self.assertNotIn("recent_requests", metrics)
        serialized = json.dumps(metrics)
        self.assertNotIn("private-customer-name-84721", serialized)
        self.assertNotIn("private-customer-domain.example.test", serialized)
        self.assertNotIn("private customer knowledge marker 84721", serialized)

    def test_new_account_never_claims_unowned_legacy_workspaces(self):
        with nimbus.connect_db() as connection:
            connection.execute(
                """
                INSERT INTO sites (id, name, website, knowledge, owner_id)
                VALUES ('legacy-unowned-site', 'Legacy workspace', 'https://legacy.example.test',
                        'legacy knowledge', NULL)
                """
            )
        customer, _csrf = self.sign_in("new-owner@example.test")
        customer_sites = customer.get("/api/sites").get_json()
        admin_client, _admin_csrf = self.admin
        admin_sites = admin_client.get("/api/sites").get_json()
        self.assertEqual(len(customer_sites), 1)
        self.assertNotEqual(customer_sites[0]["id"], "legacy-unowned-site")
        self.assertNotIn("legacy-unowned-site", [site["id"] for site in admin_sites])
        with nimbus.connect_db() as connection:
            owner_id = connection.execute(
                "SELECT owner_id FROM sites WHERE id = 'legacy-unowned-site'"
            ).fetchone()["owner_id"]
        self.assertIsNone(owner_id)

    def test_existing_empty_account_stays_empty_after_next_sign_in(self):
        with nimbus.connect_db() as connection:
            admin_id = connection.execute(
                "SELECT id FROM users WHERE email = 'admin@example.test'"
            ).fetchone()["id"]
            connection.execute("DELETE FROM chat_requests")
            connection.execute("DELETE FROM sites WHERE owner_id = ?", (admin_id,))
            connection.execute("DELETE FROM login_codes")

        admin_client, _csrf = self.sign_in("admin@example.test")
        self.assertEqual(admin_client.get("/api/sites").get_json(), [])

    def test_custom_plan_requires_two_websites_in_orders_and_account_edits(self):
        customer, customer_csrf = self.sign_in("custom-minimum@example.test")
        invalid_order = customer.post(
            "/api/billing/orders",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "plan": "custom",
                "term_months": 1,
                "websites": 1,
                "network": "TRC20",
                "transaction_hash": "T" + "a" * 63,
            },
        )
        self.assertEqual(invalid_order.status_code, 400)

        with nimbus.connect_db() as connection:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = 'custom-minimum@example.test'"
            ).fetchone()["id"]
        admin_client, admin_csrf = self.admin
        update_url = f"/api/admin/users/{user_id}"
        too_few = admin_client.patch(
            update_url,
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "plan": "custom",
                "status": "active",
                "website_limit": 1,
                "subscription_expires_at": None,
            },
        )
        self.assertEqual(too_few.status_code, 400)
        accepted = admin_client.patch(
            update_url,
            headers={"X-CSRF-Token": admin_csrf},
            json={
                "plan": "custom",
                "status": "active",
                "website_limit": 2,
                "subscription_expires_at": None,
            },
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(customer.get("/api/billing").get_json()["website_limit"], 2)
        created = customer.post(
            "/api/sites",
            headers={"X-CSRF-Token": customer_csrf},
            json={"name": "Second custom site", "website": ""},
        )
        self.assertEqual(created.status_code, 201)
        with nimbus.connect_db() as connection:
            connection.execute(
                """
                INSERT INTO payment_orders
                    (id, user_id, requested_plan, term_months, requested_websites,
                     amount_usd, network, wallet_address, transaction_hash)
                VALUES ('custom-minimum-order', ?, 'custom', 1, 2, 8, 'TRC20', ?, ?)
                """,
                (user_id, "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "custom-minimum-transaction"),
            )
        under_approved = admin_client.post(
            "/api/admin/payment-orders/custom-minimum-order/review",
            headers={"X-CSRF-Token": admin_csrf},
            json={"approve": True, "activated_websites": 1},
        )
        self.assertEqual(under_approved.status_code, 400)

    def test_chat_hides_upstream_model_service_error_details(self):
        customer, customer_csrf = self.sign_in("chat-error@example.test")
        site = customer.get("/api/sites").get_json()[0]
        saved = customer.put(
            f"/api/sites/{site['id']}",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "name": "Chat error test",
                "website": "",
                "knowledge": "Business support details.",
            },
        )
        self.assertEqual(saved.status_code, 200)
        upstream = Mock()
        upstream.iter_lines.return_value = [
            json.dumps({"error": "model secret-model-name-123 is not installed"}).encode()
        ]
        with patch.object(nimbus.requests, "post", return_value=upstream):
            response = nimbus.app.test_client().post(
                "/api/chat",
                json={
                    "site_id": site["id"],
                    "visitor_id": "visitoridentifier123",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The assistant is temporarily unavailable.", response.data)
        self.assertNotIn(b"secret-model-name-123", response.data)

    def test_response_latency_measures_time_to_first_text_not_stream_completion(self):
        customer, customer_csrf = self.sign_in("first-response@example.test")
        site = customer.get("/api/sites").get_json()[0]
        saved = customer.put(
            f"/api/sites/{site['id']}",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "name": "First response test",
                "website": "",
                "knowledge": "Business support details.",
            },
        )
        self.assertEqual(saved.status_code, 200)
        upstream = Mock()

        def delayed_stream():
            import time

            time.sleep(0.03)
            yield json.dumps({"message": {"content": "First answer text"}}).encode()
            time.sleep(0.08)
            yield json.dumps({"done": True}).encode()

        upstream.iter_lines.return_value = delayed_stream()
        with patch.object(nimbus.requests, "post", return_value=upstream):
            response = nimbus.app.test_client().post(
                "/api/chat",
                json={
                    "site_id": site["id"],
                    "visitor_id": "visitoridentifier123",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            response.data

        with nimbus.connect_db() as connection:
            timing = connection.execute(
                """
                SELECT first_response_ms, response_ms
                FROM chat_requests WHERE site_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (site["id"],),
            ).fetchone()
        self.assertIsNotNone(timing["first_response_ms"])
        self.assertGreater(timing["response_ms"], timing["first_response_ms"])
        self.assertGreater(timing["response_ms"] - timing["first_response_ms"], 50)

    def test_flask_serves_built_homepage(self):
        response = nimbus.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Nimbus", response.data)
        self.assertIn(b"AI Chatbot for Retail Websites", response.data)
        self.assertIn(b"<noscript>", response.data)
        self.assertIn(b"ecommerce website", response.data)
        embedded_plans = re.search(
            rb"window\.NIMBUS_PUBLIC_PLANS=(\{.*?\});",
            response.data,
            re.DOTALL,
        )
        self.assertIsNotNone(embedded_plans)
        self.assertEqual(json.loads(embedded_plans.group(1))["pro"]["price_per_month"], 5)
        self.assertIn(b'rel="canonical"', response.data)
        expected_base = nimbus.PUBLIC_URL or "http://localhost"
        self.assertIn(f'href="{expected_base}/"'.encode(), response.data)
        self.assertNotIn(b"__NIMBUS_PUBLIC_URL__", response.data)
        structured = re.search(
            rb'<script type="application/ld\+json">(.*?)</script>',
            response.data,
            re.DOTALL,
        )
        self.assertIsNotNone(structured)
        schema = json.loads(structured.group(1))
        self.assertEqual(schema["@type"], "SoftwareApplication")
        self.assertIn("retail", schema["description"].lower())
        response.close()

    def test_robots_and_sitemap_only_expose_public_homepage(self):
        client = nimbus.app.test_client()
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")
        self.assertEqual(robots.status_code, 200)
        self.assertIn(b"Disallow: /api/", robots.data)
        expected_base = nimbus.PUBLIC_URL or "http://localhost"
        self.assertIn(f"Sitemap: {expected_base}/sitemap.xml".encode(), robots.data)
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(f"<loc>{expected_base}/</loc>".encode(), sitemap.data)
        self.assertNotIn(b"/api/", sitemap.data)
        self.assertEqual(sitemap.mimetype, "application/xml")

    def test_admin_can_set_configurable_pricing_wallets_and_discounts(self):
        client, csrf = self.admin
        wallets = {
            "TRC20": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
            "ERC20": "0x" + "a" * 40,
            "BEP20": "0x" + "b" * 40,
        }
        invalid_wallets = {**wallets, "TRC20": "T" + "1" * 33}
        invalid = client.put(
            "/api/admin/settings",
            headers={"X-CSRF-Token": csrf},
            json={
                "plans": {
                    "pro": {"name": "Pro Plus", "price_per_site_month": 7.25},
                    "custom": {"name": "Custom", "price_per_site_month": 4.5},
                },
                "term_discounts": {"1": 0, "2": 10, "6": 15, "12": 25},
                "wallets": invalid_wallets,
            },
        )
        self.assertEqual(invalid.status_code, 400)
        response = client.put(
            "/api/admin/settings",
            headers={"X-CSRF-Token": csrf},
            json={
                "plans": {
                    "pro": {"name": "Pro Plus", "price_per_site_month": 7.25},
                    "custom": {"name": "Custom", "price_per_site_month": 4.5},
                },
                "term_discounts": {"1": 0, "2": 10, "6": 15, "12": 25},
                "wallets": wallets,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/admin/settings").get_json()["billing"]["wallets"], wallets)

    def test_public_plans_show_original_limits_and_current_admin_prices(self):
        admin_client, csrf = self.admin
        response = admin_client.put(
            "/api/admin/settings",
            headers={"X-CSRF-Token": csrf},
            json={
                "plans": {
                    "pro": {"name": "Pro", "price_per_site_month": 6.25},
                    "custom": {"name": "Custom", "price_per_site_month": 3.5},
                },
                "term_discounts": {"1": 0, "2": 0, "6": 10, "12": 20},
                "wallets": {"TRC20": "", "ERC20": "", "BEP20": ""},
            },
        )
        self.assertEqual(response.status_code, 200)

        public_response = nimbus.app.test_client().get("/api/public/plans")
        self.assertEqual(public_response.status_code, 200)
        plans = public_response.get_json()
        self.assertEqual(plans["free"]["monthly_requests"], nimbus.FREE_REQUESTS_PER_MONTH or None)
        self.assertEqual(plans["free"]["websites"], 1)
        self.assertEqual(plans["pro"]["price_per_month"], 6.25)
        self.assertEqual(plans["pro"]["websites"], 1)
        self.assertIsNone(plans["pro"]["monthly_requests"])
        self.assertEqual(plans["custom"]["price_per_site_month"], 3.5)
        self.assertEqual(plans["custom"]["minimum_websites"], 2)
        self.assertNotIn("wallets", plans)
        self.assertEqual(public_response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_payment_review_activates_admin_selected_site_allowance_and_response(self):
        admin_client, admin_csrf = self.admin
        settings = {
            "plans": {
                "pro": {"name": "Pro", "price_per_site_month": 7.25},
                "custom": {"name": "Custom", "price_per_site_month": 4.5},
            },
            "term_discounts": {"1": 0, "2": 10, "6": 15, "12": 25},
            "wallets": {
                "TRC20": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
                "ERC20": "0x" + "a" * 40,
                "BEP20": "0x" + "b" * 40,
            },
        }
        self.assertEqual(
            admin_client.put(
                "/api/admin/settings",
                headers={"X-CSRF-Token": admin_csrf},
                json=settings,
            ).status_code,
            200,
        )
        customer, customer_csrf = self.sign_in("customer@example.test")
        tx_hash = "0x" + "A" * 64
        response = customer.post(
            "/api/billing/orders",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "plan": "pro",
                "term_months": 2,
                "websites": 3,
                "network": "ERC20",
                "transaction_hash": tx_hash,
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["amount_usdt"], 39.15)
        duplicate = customer.post(
            "/api/billing/orders",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "plan": "pro",
                "term_months": 2,
                "websites": 3,
                "network": "ERC20",
                "transaction_hash": tx_hash.lower(),
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        order_id = response.get_json()["order_id"]
        reviewed = admin_client.post(
            f"/api/admin/payment-orders/{order_id}/review",
            headers={"X-CSRF-Token": admin_csrf},
            json={"approve": True, "activated_websites": 2, "admin_note": "Payment verified."},
        )
        self.assertEqual(reviewed.status_code, 200)
        billing = customer.get("/api/billing").get_json()
        self.assertEqual(billing["plan"], "pro")
        self.assertEqual(billing["website_limit"], 2)
        orders = customer.get("/api/billing/orders").get_json()
        self.assertEqual(orders[0]["admin_note"], "Payment verified.")
        self.assertEqual(orders[0]["activated_websites"], 2)

    def test_admin_role_is_removed_when_configured_email_changes(self):
        with nimbus.connect_db() as connection:
            connection.execute(
                "INSERT INTO users (id, email, role, plan) VALUES ('old-admin', 'old@example.test', 'admin', 'custom')"
            )
        original_email = os.environ.get("NIMBUS_ADMIN_EMAIL")
        try:
            os.environ["NIMBUS_ADMIN_EMAIL"] = "new-admin@example.test"
            nimbus.initialize_db()
            with nimbus.connect_db() as connection:
                old = connection.execute(
                    "SELECT role, plan FROM users WHERE id = 'old-admin'"
                ).fetchone()
            self.assertEqual(tuple(old), ("user", "free"))
        finally:
            if original_email is None:
                os.environ.pop("NIMBUS_ADMIN_EMAIL", None)
            else:
                os.environ["NIMBUS_ADMIN_EMAIL"] = original_email
            nimbus.initialize_db()

    def test_non_admin_cannot_read_admin_settings(self):
        customer, _csrf = self.sign_in("customer@example.test")
        response = customer.get("/api/admin/settings")
        self.assertEqual(response.status_code, 403)

    def test_suspended_customer_cannot_chat_and_legacy_plan_upgrade_is_disabled(self):
        customer, customer_csrf = self.sign_in("suspended@example.test")
        site_id = customer.get("/api/sites").get_json()[0]["id"]
        site = customer.get(f"/api/sites/{site_id}").get_json()
        self.assertEqual(
            customer.put(
                f"/api/sites/{site_id}",
                headers={"X-CSRF-Token": customer_csrf},
                json={"name": site["name"], "website": "", "knowledge": "Business support details."},
            ).status_code,
            200,
        )
        self.assertEqual(
            customer.post(
                "/api/billing/request-plan",
                headers={"X-CSRF-Token": customer_csrf},
                json={"plan": "pro"},
            ).status_code,
            410,
        )
        client, csrf = self.admin
        with nimbus.connect_db() as connection:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = 'suspended@example.test'"
            ).fetchone()["id"]
        changed = client.patch(
            f"/api/admin/users/{user_id}",
            headers={"X-CSRF-Token": csrf},
            json={
                "plan": "free",
                "status": "suspended",
                "website_limit": 1,
                "subscription_expires_at": None,
            },
        )
        self.assertEqual(changed.status_code, 200)
        chat = nimbus.app.test_client().post(
            "/api/chat",
            json={
                "site_id": site_id,
                "visitor_id": "visitoridentifier123",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        self.assertEqual(chat.status_code, 403)


if __name__ == "__main__":
    unittest.main()
