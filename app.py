import hashlib
import html as html_module
import http.client
import hmac
import ipaddress
import json
import os
import re
import secrets
import smtplib
import socket
import sqlite3
import ssl
import time
import uuid
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import requests
from flask import Flask, Response, g, jsonify, request, session, send_from_directory, stream_with_context
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
FRONTEND_STATIC = BASE_DIR / "frontend" / "static"
PUBLIC_URL = os.environ.get("NIMBUS_PUBLIC_URL", "").strip().rstrip("/")
DB_PATH = Path(os.environ.get("NIMBUS_DB_PATH", BASE_DIR / "nimbus.sqlite3"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
MAX_CRAWL_PAGES = 5
MAX_PAGE_BYTES = 512_000
MAX_CRAWL_CHARACTERS = 12_000
try:
    FREE_REQUESTS_PER_MONTH = int(os.environ.get("NIMBUS_FREE_REQUESTS_PER_MONTH", "30"))
except ValueError as error:
    raise RuntimeError("NIMBUS_FREE_REQUESTS_PER_MONTH must be a non-negative integer.") from error
if FREE_REQUESTS_PER_MONTH < 0:
    raise RuntimeError("NIMBUS_FREE_REQUESTS_PER_MONTH must be a non-negative integer.")
PLAN_DETAILS = {
    "free": {
        "name": "Free",
        "price_usd": 0,
        "monthly_requests": FREE_REQUESTS_PER_MONTH or None,
        "websites": 1,
    },
    "pro": {"name": "Pro", "price_usd": 5, "monthly_requests": None, "websites": 1},
    "custom": {"name": "Custom", "price_usd": 4, "monthly_requests": None, "websites": 1},
}
DEFAULT_BILLING_SETTINGS = {
    "plans": {
        "pro": {"name": "Pro", "price_per_site_month": 5.0},
        "custom": {"name": "Custom", "price_per_site_month": 4.0},
    },
    "term_discounts": {"1": 0, "2": 0, "6": 10, "12": 20},
    "wallets": {"TRC20": "", "ERC20": "", "BEP20": ""},
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
production_mode = os.environ.get("NIMBUS_ENV", "").lower() == "production"
configured_secret = os.environ.get("NIMBUS_SECRET_KEY", "").strip()
if production_mode and len(configured_secret) < 32:
    raise RuntimeError("Production requires a stable NIMBUS_SECRET_KEY of at least 32 characters.")
cookie_secure = os.environ.get("NIMBUS_COOKIE_SECURE", "false").lower() == "true"
if production_mode and not cookie_secure:
    raise RuntimeError("Production requires NIMBUS_COOKIE_SECURE=true and HTTPS.")
app.secret_key = configured_secret or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cookie_secure,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
)


@contextmanager
def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db():
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
                plan TEXT NOT NULL DEFAULT 'free' CHECK(plan IN ('free', 'pro', 'custom')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "status" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        if "website_limit" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN website_limit INTEGER NOT NULL DEFAULT 1")
        if "subscription_expires_at" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN subscription_expires_at TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                website TEXT NOT NULL DEFAULT '',
                knowledge TEXT NOT NULL DEFAULT '',
                owner_id TEXT REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        site_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(sites)").fetchall()
        }
        if "owner_id" not in site_columns:
            connection.execute("ALTER TABLE sites ADD COLUMN owner_id TEXT REFERENCES users(id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_requests (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES users(id),
                site_id TEXT NOT NULL REFERENCES sites(id),
                visitor_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                response_ms REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        chat_request_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(chat_requests)").fetchall()
        }
        if "first_response_ms" not in chat_request_columns:
            connection.execute("ALTER TABLE chat_requests ADD COLUMN first_response_ms REAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_requests (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                requested_plan TEXT NOT NULL CHECK(requested_plan IN ('pro', 'custom')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS login_codes (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE,
                code_hash TEXT NOT NULL,
                request_ip_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                requested_plan TEXT NOT NULL CHECK(requested_plan IN ('pro', 'custom')),
                term_months INTEGER NOT NULL CHECK(term_months IN (1, 2, 6, 12)),
                requested_websites INTEGER NOT NULL CHECK(requested_websites BETWEEN 1 AND 100),
                amount_usd REAL NOT NULL,
                network TEXT NOT NULL CHECK(network IN ('TRC20', 'ERC20', 'BEP20')),
                wallet_address TEXT NOT NULL,
                transaction_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
                admin_note TEXT NOT NULL DEFAULT '',
                activated_websites INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_requests_created ON chat_requests(created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_requests_owner_created ON chat_requests(owner_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_requests_site_created ON chat_requests(site_id, created_at)"
        )

        admin_email = os.environ.get("NIMBUS_ADMIN_EMAIL", "").strip().lower()
        if admin_email:
            connection.execute(
                """
                UPDATE users
                SET role = 'user', plan = 'free', website_limit = 1, subscription_expires_at = NULL
                WHERE role = 'admin' AND email != ? COLLATE NOCASE
                """,
                (admin_email,),
            )
        else:
            connection.execute(
                """
                UPDATE users
                SET role = 'user', plan = 'free', website_limit = 1, subscription_expires_at = NULL
                WHERE role = 'admin'
                """
            )
        admin_id = None
        if admin_email:
            admin = connection.execute(
                "SELECT id FROM users WHERE email = ? COLLATE NOCASE", (admin_email,)
            ).fetchone()
            admin_id = admin["id"] if admin else uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO users (id, email, role, plan)
                VALUES (?, ?, 'admin', 'custom')
                ON CONFLICT(email) DO UPDATE SET role = 'admin', plan = 'custom',
                    status = 'active', website_limit = 100, subscription_expires_at = NULL
                """,
                (admin_id, admin_email),
            )
        settings_defaults = {
            "billing": DEFAULT_BILLING_SETTINGS,
        }
        for key, value in settings_defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

def get_site(site_id):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT id, name, website, knowledge, owner_id, created_at FROM sites WHERE id = ?",
            (site_id,),
        ).fetchone()
    return dict(row) if row else None


def public_site(site):
    return {
        "id": site["id"],
        "name": site["name"],
    }


class WebsiteContentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.title_parts = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._ignored_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)
            if self._in_title:
                self.title_parts.append(text)


def normalize_crawl_url(value):
    value = value.strip()
    if not value:
        raise ValueError("Add the public website URL before fetching its content.")
    if "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only public HTTP or HTTPS websites can be fetched.")
    if not parts.hostname or parts.username or parts.password:
        raise ValueError("Enter a valid public website URL.")
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("The website URL contains an invalid port.") from error
    if port is not None and port != (443 if parts.scheme.lower() == "https" else 80):
        raise ValueError("For safety, website fetching only supports standard HTTP and HTTPS ports.")

    host = parts.hostname.lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local and private network addresses cannot be fetched.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Enter a public website hostname, not an IP address.")

    clean = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
    return urldefrag(clean)[0]


def ensure_public_host(url):
    hostname = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("The website hostname could not be resolved. Check the URL and try again.") from error
    if not addresses:
        raise ValueError("The website hostname does not resolve to a public address.")
    public_addresses = set()
    for address in {entry[4][0] for entry in addresses}:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as error:
            raise ValueError("The website resolved to an invalid network address.") from error
        if not ip.is_global:
            raise ValueError("The website resolved to a private or reserved network address.")
        public_addresses.add(str(ip))
    return sorted(public_addresses)


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname, address, port, timeout):
        super().__init__(hostname, port=port, timeout=timeout)
        self.pinned_address = address

    def connect(self):
        self.sock = socket.create_connection(
            (self.pinned_address, self.port), self.timeout, self.source_address
        )


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, port, timeout):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_address = address

    def connect(self):
        sock = socket.create_connection(
            (self.pinned_address, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def fetch_public_page(session, url):
    current_url = normalize_crawl_url(url)
    for _ in range(4):
        addresses = ensure_public_host(current_url)
        parsed = urlsplit(current_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection_class = PinnedHTTPSConnection if parsed.scheme == "https" else PinnedHTTPConnection
        connection = connection_class(parsed.hostname, addresses[0], port, timeout=8)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": parsed.hostname,
                    "User-Agent": "NexaWebsiteAssistant/1.0 (+website knowledge import)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                response.read(1024)
                if not location:
                    raise ValueError("The website returned an invalid redirect.")
                redirected = normalize_crawl_url(urljoin(current_url, location))
                if urlsplit(redirected).hostname != parsed.hostname:
                    raise ValueError("The website redirected to another domain. Enter the final website URL instead.")
                current_url = redirected
                continue
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"The website returned HTTP {response.status}. Check the URL and try again.")
            content_type = (response.getheader("Content-Type") or "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return None
            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_PAGE_BYTES:
                        raise ValueError("A website page is larger than the safe import limit.")
                except ValueError as error:
                    if str(error) == "A website page is larger than the safe import limit.":
                        raise
                    raise ValueError("The website returned an invalid Content-Length header.") from error
            content = response.read(MAX_PAGE_BYTES + 1)
            if len(content) > MAX_PAGE_BYTES:
                raise ValueError("A website page is larger than the safe import limit.")
            parser = WebsiteContentParser()
            parser.feed(content.decode("utf-8", errors="replace"))
            text = re.sub(r"[ \t]+\n", "\n", "\n".join(parser.parts))
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return {
                "url": current_url,
                "title": " ".join(parser.title_parts).strip(),
                "text": text,
                "links": parser.links,
            }
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            app.logger.warning("Could not fetch validated public website %s: %s", current_url, error)
            raise ValueError("Could not load the website. Check that it is public and try again.") from error
        finally:
            connection.close()
    raise ValueError("The website redirected too many times.")


def crawl_website(start_url, max_characters=MAX_CRAWL_CHARACTERS):
    start_url = normalize_crawl_url(start_url)
    base_host = urlsplit(start_url).hostname
    session = requests.Session()
    session.trust_env = False
    visited = set()
    pending = [start_url]
    pages = []
    priority_links = []

    try:
        while pending and len(pages) < MAX_CRAWL_PAGES:
            url = pending.pop(0)
            url = urldefrag(url)[0]
            if url in visited:
                continue
            visited.add(url)
            page = fetch_public_page(session, url)
            if not page:
                continue
            if page["text"]:
                pages.append(page)
            for href in page["links"]:
                try:
                    link = normalize_crawl_url(urljoin(page["url"], href))
                except ValueError:
                    continue
                parts = urlsplit(link)
                if parts.hostname != base_host or link in visited or link in pending:
                    continue
                if parts.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip")):
                    continue
                if re.search(r"about|faq|help|contact|shipping|delivery|return|policy|service|product", parts.path, re.I):
                    priority_links.append(link)
                else:
                    pending.append(link)
            pending = priority_links + pending
            priority_links = []
    except requests.RequestException as error:
        app.logger.warning("Could not fetch public website content: %s", error)
        raise ValueError("Could not load the website. Check that it is public and try again.") from error
    finally:
        session.close()

    if not pages:
        raise ValueError("No readable text was found. The website may block crawlers or require a login.")

    sections = []
    remaining = min(max_characters, MAX_CRAWL_CHARACTERS)
    page_character_limit = max(500, remaining // MAX_CRAWL_PAGES)
    for page in pages:
        heading = page["title"] or page["url"]
        section = f"## {heading}\nSource: {page['url']}\n{page['text']}"
        section = section[:min(remaining, page_character_limit)].strip()
        if section:
            sections.append(section)
            remaining -= len(section) + 2
        if remaining <= 0:
            break

    return {"knowledge": "\n\n".join(sections), "pages": len(pages), "url": start_url}


initialize_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with connect_db() as connection:
        row = connection.execute(
            "SELECT id, email, role, plan, status, website_limit, subscription_expires_at, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        session.clear()
        return None
    if row["status"] != "active":
        session.clear()
        return None
    return dict(row)


def get_billing_settings(connection):
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'billing'"
    ).fetchone()
    if not row:
        return json.loads(json.dumps(DEFAULT_BILLING_SETTINGS))
    return json.loads(row["value"])


def is_valid_wallet_address(network, address):
    if network in {"ERC20", "BEP20"}:
        return re.fullmatch(r"0x[a-fA-F0-9]{40}", address) is not None
    if network != "TRC20" or len(address) != 34:
        return False

    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in address:
        try:
            digit = alphabet.index(character)
        except ValueError:
            return False
        number = number * 58 + digit
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    decoded = b"\0" * (len(address) - len(address.lstrip("1"))) + decoded
    if len(decoded) != 25:
        return False
    payload, checksum = decoded[:-4], decoded[-4:]
    expected_checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return payload[0] == 0x41 and hmac.compare_digest(checksum, expected_checksum)


def effective_plan(user):
    expires_at = user.get("subscription_expires_at")
    if (
        user.get("plan") != "free"
        and expires_at
        and expires_at <= datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    ):
        return "free"
    return user.get("plan", "free")


def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def validate_smtp_transport(smtp_username, smtp_password, smtp_ssl, starttls):
    if smtp_ssl and starttls:
        raise RuntimeError("Enable either implicit SMTP SSL or STARTTLS, not both.")
    if bool(smtp_username) != bool(smtp_password):
        raise RuntimeError("Set both NIMBUS_SMTP_USERNAME and NIMBUS_SMTP_PASSWORD, or leave both blank.")
    if smtp_username and not (smtp_ssl or starttls):
        raise RuntimeError("SMTP authentication requires a secure connection.")


def send_admin_notification(subject, body):
    recipient = os.environ.get("NIMBUS_ADMIN_EMAIL", "").strip()
    if not recipient:
        raise RuntimeError("NIMBUS_ADMIN_EMAIL is not configured.")
    smtp_host = os.environ.get("NIMBUS_SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("NIMBUS_SMTP_PORT", "587"))
    smtp_username = os.environ.get("NIMBUS_SMTP_USERNAME", "")
    smtp_password = os.environ.get("NIMBUS_SMTP_PASSWORD", "")
    sender = os.environ.get("NIMBUS_SMTP_FROM", smtp_username).strip()
    smtp_ssl = os.environ.get("NIMBUS_SMTP_SSL", "true" if smtp_port == 465 else "false").lower() == "true"
    starttls = os.environ.get(
        "NIMBUS_SMTP_STARTTLS", "true" if smtp_port == 587 else "false"
    ).lower() == "true"
    if not smtp_host or not sender:
        raise RuntimeError("Email delivery is not configured.")
    validate_smtp_transport(smtp_username, smtp_password, smtp_ssl, starttls)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    if smtp_ssl:
        with smtplib.SMTP_SSL(
            smtp_host, smtp_port, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            if smtp_username:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        if starttls:
            smtp.starttls(context=ssl.create_default_context())
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def send_customer_payment_email(email, subject, body):
    smtp_host = os.environ.get("NIMBUS_SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("NIMBUS_SMTP_PORT", "587"))
    smtp_username = os.environ.get("NIMBUS_SMTP_USERNAME", "")
    smtp_password = os.environ.get("NIMBUS_SMTP_PASSWORD", "")
    sender = os.environ.get("NIMBUS_SMTP_FROM", smtp_username).strip()
    smtp_ssl = os.environ.get("NIMBUS_SMTP_SSL", "true" if smtp_port == 465 else "false").lower() == "true"
    starttls = os.environ.get(
        "NIMBUS_SMTP_STARTTLS", "true" if smtp_port == 587 else "false"
    ).lower() == "true"
    if not smtp_host or not sender:
        raise RuntimeError("Email delivery is not configured.")
    validate_smtp_transport(smtp_username, smtp_password, smtp_ssl, starttls)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = email
    message.set_content(body)
    if smtp_ssl:
        with smtplib.SMTP_SSL(
            smtp_host, smtp_port, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            if smtp_username:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        if starttls:
            smtp.starttls(context=ssl.create_default_context())
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


@app.before_request
def load_user_and_validate_csrf():
    g.user = current_user()
    if (
        request.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.path not in {
            "/api/auth/request-otp", "/api/auth/verify-otp", "/api/chat"
        }
    ):
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            return jsonify(error="Your security token expired. Please reload and sign in again."), 403


def require_login():
    if not g.user:
        return jsonify(error="Please sign in to continue."), 401
    return None


def require_admin():
    failure = require_login()
    if failure:
        return failure
    if g.user["role"] != "admin":
        return jsonify(error="Administrator access is required."), 403
    return None


def owned_site(site_id):
    site = get_site(site_id)
    if not site:
        return None
    if site["owner_id"] != g.user["id"]:
        return None
    return site


def start_chat_request(site, visitor_id):
    with connect_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            """
            SELECT id, role, plan, status, website_limit, subscription_expires_at
            FROM users WHERE id = ?
            """,
            (site["owner_id"],),
        ).fetchone()
        if not user:
            raise RuntimeError("This website is not linked to an account.")
        if user["status"] != "active":
            return None, 0, None, "suspended"
        plan_id = effective_plan(dict(user))
        if user["role"] != "admin":
            owned_sites = connection.execute(
                "SELECT id FROM sites WHERE owner_id = ? ORDER BY created_at, rowid",
                (user["id"],),
            ).fetchall()
            allowed_sites = (
                1 if plan_id == "free"
                else max(2 if plan_id == "custom" else 1, user["website_limit"])
            )
            if site["id"] not in {row["id"] for row in owned_sites[:allowed_sites]}:
                return None, 0, None, "website_limit"
        plan = PLAN_DETAILS.get(plan_id, PLAN_DETAILS["free"])
        month_count = connection.execute(
            """
            SELECT COUNT(*) FROM chat_requests
            WHERE owner_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
            """,
            (site["owner_id"],),
        ).fetchone()[0]
        limit = plan["monthly_requests"]
        if limit is not None and month_count >= limit:
            return None, month_count, limit, "monthly_limit"
        request_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO chat_requests (id, owner_id, site_id, visitor_id, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (request_id, site["owner_id"], site["id"], visitor_id[:128]),
        )
    return request_id, month_count + 1, limit, None


def finish_request(request_id, status, started_at):
    completion_ms = round((time.perf_counter() - started_at) * 1000, 2)
    with connect_db() as connection:
        connection.execute(
            "UPDATE chat_requests SET status = ?, response_ms = ? WHERE id = ?",
            (status, completion_ms, request_id),
        )


def record_first_response(request_id, response_ms):
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE chat_requests SET first_response_ms = ?
            WHERE id = ? AND first_response_ms IS NULL
            """,
            (response_ms, request_id),
        )


@app.get("/api/auth/me")
def auth_me():
    if not g.user:
        return jsonify(authenticated=False)
    csrf_token = session.get("csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["csrf_token"] = csrf_token
    return jsonify(authenticated=True, user=g.user, csrf_token=csrf_token)


def send_login_code(email, code):
    smtp_host = os.environ.get("NIMBUS_SMTP_HOST", "").strip()
    try:
        smtp_port = int(os.environ.get("NIMBUS_SMTP_PORT", "587"))
    except ValueError as error:
        raise RuntimeError("NIMBUS_SMTP_PORT must be a valid port number.") from error
    if not 1 <= smtp_port <= 65535:
        raise RuntimeError("NIMBUS_SMTP_PORT must be between 1 and 65535.")
    smtp_username = os.environ.get("NIMBUS_SMTP_USERNAME", "")
    smtp_password = os.environ.get("NIMBUS_SMTP_PASSWORD", "")
    sender = os.environ.get("NIMBUS_SMTP_FROM", smtp_username).strip()
    smtp_ssl = os.environ.get("NIMBUS_SMTP_SSL", "true" if smtp_port == 465 else "false").lower() == "true"
    starttls = os.environ.get(
        "NIMBUS_SMTP_STARTTLS", "true" if smtp_port == 587 else "false"
    ).lower() == "true"
    if not smtp_host or not sender:
        if os.environ.get("NIMBUS_ALLOW_CONSOLE_OTP", "false").lower() == "true":
            app.logger.info("Development login code for %s: %s", email, code)
            return
        raise RuntimeError("Email delivery is not configured. Set the NIMBUS_SMTP_* variables.")
    if smtp_ssl and starttls:
        raise RuntimeError("Enable either implicit SMTP SSL or STARTTLS, not both.")
    if bool(smtp_username) != bool(smtp_password):
        raise RuntimeError("Set both NIMBUS_SMTP_USERNAME and NIMBUS_SMTP_PASSWORD, or leave both blank.")
    if smtp_username and not (smtp_ssl or starttls):
        raise RuntimeError(
            "SMTP authentication requires a secure connection. Enable STARTTLS on port 587 "
            "or implicit SSL on port 465."
        )

    message = EmailMessage()
    message["Subject"] = "Your Nexa sign-in code"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Your Nexa sign-in code is {code}.\n\n"
        "This code expires in 10 minutes. If you did not request it, you can ignore this email."
    )
    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if smtp_ssl else smtplib.SMTP
    smtp_kwargs = {"context": context} if smtp_ssl else {}
    with smtp_class(smtp_host, smtp_port, timeout=10, **smtp_kwargs) as smtp:
        if starttls:
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                raise RuntimeError(
                    "The SMTP server does not offer STARTTLS on this port. "
                    "Check the provider's secure SMTP port settings."
                )
            smtp.starttls()
            smtp.ehlo()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def hash_login_code(code):
    return hmac.new(
        app.secret_key.encode("utf-8"), code.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@app.post("/api/auth/request-otp")
def request_otp():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
        return jsonify(error="Enter a valid email address."), 400

    now = int(time.time())
    request_ip_hash = hashlib.sha256(
        request.remote_addr.encode("utf-8") if request.remote_addr else b"unknown"
    ).hexdigest()
    with connect_db() as connection:
        connection.execute("DELETE FROM login_codes WHERE created_at < datetime('now', '-1 day')")
        email_count = connection.execute(
            """
            SELECT COUNT(*) FROM login_codes
            WHERE email = ? AND created_at >= datetime('now', '-1 hour')
            """,
            (email,),
        ).fetchone()[0]
        ip_count = connection.execute(
            """
            SELECT COUNT(*) FROM login_codes
            WHERE request_ip_hash = ? AND created_at >= datetime('now', '-1 hour')
            """,
            (request_ip_hash,),
        ).fetchone()[0]
        recently_sent = connection.execute(
            """
            SELECT 1 FROM login_codes
            WHERE email = ? AND created_at >= datetime('now', '-60 seconds')
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if email_count >= 5 or ip_count >= 20 or recently_sent:
            return jsonify(error="Please wait before requesting another sign-in code."), 429

    code = f"{secrets.randbelow(1_000_000):06d}"
    code_id = uuid.uuid4().hex
    try:
        send_login_code(email, code)
    except (OSError, smtplib.SMTPException, RuntimeError) as error:
        app.logger.warning("Could not send login email: %s", error)
        return jsonify(error="We could not send your sign-in email. Check email configuration and try again."), 503

    with connect_db() as connection:
        connection.execute(
            "UPDATE login_codes SET used = 1 WHERE email = ? AND used = 0", (email,)
        )
        connection.execute(
            """
            INSERT INTO login_codes (id, email, code_hash, request_ip_hash, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code_id, email, hash_login_code(code), request_ip_hash, now + 600),
        )
    return jsonify(ok=True, message="If this email can sign in, a one-time code has been sent.")


@app.post("/api/auth/verify-otp")
def verify_otp():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    code = str(payload.get("code", "")).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not re.fullmatch(r"\d{6}", code):
        return jsonify(error="Enter your email and the six-digit sign-in code."), 400

    with connect_db() as connection:
        row = connection.execute(
            """
            SELECT id, code_hash, expires_at, attempts FROM login_codes
            WHERE email = ? AND used = 0 ORDER BY created_at DESC LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row or row["expires_at"] < int(time.time()) or row["attempts"] >= 5:
            if row:
                connection.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))
            return jsonify(error="That sign-in code is invalid or expired. Request a new code."), 400
        connection.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        if not hmac.compare_digest(row["code_hash"], hash_login_code(code)):
            return jsonify(error="That sign-in code is invalid or expired. Request a new code."), 400
        connection.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))
        user = connection.execute(
            "SELECT id, status FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if user:
            if user["status"] != "active":
                return jsonify(error="This account is suspended. Contact the administrator."), 403
            user_id = user["id"]
        else:
            user_id = uuid.uuid4().hex
            role = "admin" if email == os.environ.get("NIMBUS_ADMIN_EMAIL", "").strip().lower() else "user"
            plan = "custom" if role == "admin" else "free"
            connection.execute(
                "INSERT INTO users (id, email, role, plan) VALUES (?, ?, ?, ?)",
                (user_id, email, role, plan),
            )
            connection.execute(
                "INSERT INTO sites (id, name, owner_id) VALUES (?, ?, ?)",
                (uuid.uuid4().hex, "My website", user_id),
            )

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(user=current_user(), csrf_token=session["csrf_token"]), 201


@app.post("/api/auth/logout")
def logout():
    failure = require_login()
    if failure:
        return failure
    session.clear()
    return jsonify(ok=True)


@app.after_request
def add_public_widget_headers(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.path.startswith("/api/public/") or request.path == "/api/chat":
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.get("/")
def index():
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        base_url = PUBLIC_URL or request.host_url.rstrip("/")
        structured_data = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "Nexa",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "url": f"{base_url}/",
            "description": (
                "An AI chatbot for retail and other business websites. Add a "
                "customizable chat widget to help visitors find answers about "
                "products, services, and business information."
            ),
            "featureList": [
                "AI chatbot for retail websites",
                "Embeddable website chat widget",
                "Customer support automation",
                "Website-based business answers",
            ],
        }
        escaped_base_url = html_module.escape(base_url, quote=True)
        html = html.replace("__NEXA_PUBLIC_URL__", escaped_base_url)
        html = html.replace(
            "__NEXA_STRUCTURED_DATA__",
            json.dumps(structured_data, separators=(",", ":")).replace("<", "\\u003c"),
        )
        public_plans_json = json.dumps(
            public_plan_data(), separators=(",", ":")
        )
        public_plans_json = (
            public_plans_json.replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        html = html.replace("__NEXA_PUBLIC_PLANS__", public_plans_json)
        response = Response(html, mimetype="text/html")
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    return (
        "React frontend is not built. Run `cd frontend && npm install && npm run build`, "
        "or start the development frontend with `npm run dev`.",
        503,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.get("/robots.txt")
def robots_txt():
    base_url = PUBLIC_URL or request.host_url.rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /widget.js\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    base_url = PUBLIC_URL or request.host_url.rstrip("/")
    last_modified = datetime.now(timezone.utc).date().isoformat()
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url>"
        f"<loc>{html_module.escape(base_url, quote=True)}/</loc>"
        f"<lastmod>{last_modified}</lastmod>"
        "<changefreq>weekly</changefreq>"
        "<priority>1.0</priority>"
        "</url>"
        "</urlset>"
    )
    return Response(body, mimetype="application/xml")


@app.get("/assets/<path:filename>")
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.get("/widget.js")
def website_widget():
    return send_from_directory(FRONTEND_STATIC, "widget.js")


@app.get("/api/sites")
def list_sites():
    failure = require_login()
    if failure:
        return failure
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT id, name, website, knowledge, created_at FROM sites
            WHERE owner_id = ? ORDER BY created_at, rowid
            """,
            (g.user["id"],),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/sites")
def create_site():
    failure = require_login()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    if not name:
        return jsonify(error="Add a business name to create a workspace."), 400
    site = {
        "id": uuid.uuid4().hex,
        "name": name[:100],
        "website": str(payload.get("website", "")).strip()[:300],
        "knowledge": "",
    }
    with connect_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT id, role, plan, status, website_limit, subscription_expires_at FROM users WHERE id = ?",
            (g.user["id"],),
        ).fetchone()
        if not user or user["status"] != "active":
            return jsonify(error="This account is not active."), 403
        if user["role"] != "admin":
            site_count = connection.execute(
                "SELECT COUNT(*) FROM sites WHERE owner_id = ?", (g.user["id"],)
            ).fetchone()[0]
            plan_id = effective_plan(dict(user))
            website_limit = (
                1 if plan_id == "free"
                else max(2 if plan_id == "custom" else 1, user["website_limit"])
            )
            if site_count >= website_limit:
                return jsonify(error=f"Your current plan includes {website_limit} website workspace(s)."), 403
        connection.execute(
            "INSERT INTO sites (id, name, website, knowledge, owner_id) VALUES (?, ?, ?, ?, ?)",
            (site["id"], site["name"], site["website"], site["knowledge"], g.user["id"]),
        )
    return jsonify(site), 201


@app.get("/api/sites/<site_id>")
def read_site(site_id):
    failure = require_login()
    if failure:
        return failure
    site = owned_site(site_id)
    if not site:
        return jsonify(error="This website workspace could not be found."), 404
    return jsonify(site)


@app.put("/api/sites/<site_id>")
def update_site(site_id):
    failure = require_login()
    if failure:
        return failure
    site = owned_site(site_id)
    if not site:
        return jsonify(error="This website workspace could not be found."), 404
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    website = str(payload.get("website", "")).strip()
    knowledge = str(payload.get("knowledge", "")).strip()
    if not name:
        return jsonify(error="Business name is required."), 400
    if len(name) > 100 or len(website) > 300 or len(knowledge) > 20000:
        return jsonify(error="One of the fields is too long. Please shorten it and try again."), 400

    with connect_db() as connection:
        cursor = connection.execute(
            "UPDATE sites SET name = ?, website = ?, knowledge = ? WHERE id = ?",
            (name, website, knowledge, site_id),
        )
    if cursor.rowcount == 0:
        return jsonify(error="This website workspace could not be found."), 404
    return jsonify(get_site(site_id))


@app.post("/api/sites/<site_id>/crawl")
def crawl_site(site_id):
    failure = require_login()
    if failure:
        return failure
    site = owned_site(site_id)
    if not site:
        return jsonify(error="This website workspace could not be found."), 404
    payload = request.get_json(silent=True) or {}
    website = str(payload.get("website") or site["website"]).strip()
    if len(website) > 300:
        return jsonify(error="The website URL is too long."), 400
    max_characters = payload.get("max_characters", MAX_CRAWL_CHARACTERS)
    if not isinstance(max_characters, int) or not 500 <= max_characters <= MAX_CRAWL_CHARACTERS:
        return jsonify(error="There is not enough space to import website content. Shorten the existing knowledge and try again."), 400
    try:
        result = crawl_website(website, max_characters)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except requests.RequestException:
        app.logger.exception("Unexpected error while importing website content.")
        return jsonify(error="The website could not be imported. Please try again."), 502
    return jsonify(result)


@app.get("/api/billing")
def billing_status():
    failure = require_login()
    if failure:
        return failure
    with connect_db() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM chat_requests
            WHERE owner_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
            """,
            (g.user["id"],),
        ).fetchone()[0]
        sites = connection.execute(
            "SELECT COUNT(*) FROM sites WHERE owner_id = ?", (g.user["id"],)
        ).fetchone()[0]
        pending = connection.execute(
            """
            SELECT requested_plan FROM payment_orders
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (g.user["id"],),
        ).fetchone()
        settings = get_billing_settings(connection)
    plan_id = effective_plan(g.user)
    return jsonify(
        plan=plan_id,
        usage_this_month=count,
        websites=sites,
        pending_plan=pending["requested_plan"] if pending else None,
        website_limit=(
            1 if plan_id == "free"
            else max(2 if plan_id == "custom" else 1, g.user["website_limit"])
        ),
        subscription_expires_at=g.user["subscription_expires_at"],
        plans={
            **{"free": PLAN_DETAILS["free"]},
            **{
                plan_id: {
                    "name": details["name"],
                    "price_per_site_month": details["price_per_site_month"],
                    "monthly_requests": None,
                }
                for plan_id, details in settings["plans"].items()
            },
        },
        term_discounts=settings["term_discounts"],
        wallets=settings["wallets"],
        payments_enabled=any(settings["wallets"].values()),
    )


@app.get("/api/usage")
def account_usage():
    failure = require_login()
    if failure:
        return failure
    try:
        days = int(request.args.get("days", "7"))
    except ValueError:
        return jsonify(error="Days must be a number between 1 and 90."), 400
    if not 1 <= days <= 90:
        return jsonify(error="Days must be a number between 1 and 90."), 400
    interval = f"-{days - 1} days"
    with connect_db() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful,
                   SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS unsuccessful,
                   AVG(CASE WHEN status = 'success' THEN first_response_ms END) AS avg_ms
            FROM chat_requests
            WHERE owner_id = ? AND created_at >= datetime('now', ?)
            """,
            (g.user["id"], interval),
        ).fetchone()
        daily = connection.execute(
            """
            SELECT date(created_at) AS day, COUNT(*) AS requests,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful
            FROM chat_requests
            WHERE owner_id = ? AND created_at >= datetime('now', ?)
            GROUP BY date(created_at) ORDER BY day
            """,
            (g.user["id"], interval),
        ).fetchall()
        month_count = connection.execute(
            """
            SELECT COUNT(*) FROM chat_requests
            WHERE owner_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
            """,
            (g.user["id"],),
        ).fetchone()[0]
        site_count = connection.execute(
            "SELECT COUNT(*) FROM sites WHERE owner_id = ?", (g.user["id"],)
        ).fetchone()[0]
    plan_id = effective_plan(g.user)
    limit = PLAN_DETAILS[plan_id]["monthly_requests"]
    return jsonify(
        days=days,
        plan=plan_id,
        requests=totals["total"] or 0,
        successful=totals["successful"] or 0,
        unsuccessful=totals["unsuccessful"] or 0,
        avg_response_ms=round(totals["avg_ms"], 2) if totals["avg_ms"] is not None else None,
        daily=[dict(row) for row in daily],
        usage_this_month=month_count,
        monthly_limit=limit,
        websites=site_count,
    )


@app.get("/api/billing/orders")
def billing_orders():
    failure = require_login()
    if failure:
        return failure
    with connect_db() as connection:
        rows = connection.execute(
            """
            SELECT id, requested_plan, term_months, requested_websites, amount_usd,
                   network, wallet_address, transaction_hash, status, admin_note,
                   activated_websites, created_at, reviewed_at
            FROM payment_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 50
            """,
            (g.user["id"],),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/billing/orders")
def create_payment_order():
    failure = require_login()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    plan_id = payload.get("plan")
    months = payload.get("term_months")
    websites = payload.get("websites")
    network = payload.get("network")
    transaction_hash = str(payload.get("transaction_hash", "")).strip()
    if plan_id not in {"pro", "custom"}:
        return jsonify(error="Choose a valid paid plan."), 400
    if isinstance(months, bool) or months not in {1, 2, 6, 12}:
        return jsonify(error="Choose a subscription term of 1, 2, 6, or 12 months."), 400
    minimum_websites = 2 if plan_id == "custom" else 1
    if (
        isinstance(websites, bool)
        or not isinstance(websites, int)
        or not minimum_websites <= websites <= 100
    ):
        return jsonify(error=f"Choose between {minimum_websites} and 100 websites for this plan."), 400
    if network not in {"TRC20", "ERC20", "BEP20"}:
        return jsonify(error="Choose a supported USDT network."), 400
    if not re.fullmatch(r"[A-Za-z0-9:_-]{8,200}", transaction_hash):
        return jsonify(error="Enter a valid blockchain transaction hash after sending payment."), 400
    transaction_hash = transaction_hash.lower()

    with connect_db() as connection:
        settings = get_billing_settings(connection)
        wallet = settings["wallets"].get(network, "").strip()
        plan = settings["plans"][plan_id]
        if not wallet:
            return jsonify(error=f"USDT payments on {network} are not configured yet."), 503
        if connection.execute(
            "SELECT 1 FROM payment_orders WHERE network = ? AND transaction_hash = ?",
            (network, transaction_hash),
        ).fetchone():
            return jsonify(error="This transaction hash has already been submitted."), 409
        discount = settings["term_discounts"][str(months)]
        amount = (
            Decimal(str(plan["price_per_site_month"]))
            * websites
            * months
            * Decimal(100 - discount)
            / Decimal(100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        order_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO payment_orders
                (id, user_id, requested_plan, term_months, requested_websites,
                 amount_usd, network, wallet_address, transaction_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id, g.user["id"], plan_id, months, websites, float(amount),
                network, wallet, transaction_hash,
            ),
        )

    notification_warning = None
    try:
        send_admin_notification(
            f"Nexa USDT payment review: {g.user['email']}",
            (
                f"Account: {g.user['email']}\nPlan: {plan['name']}\nTerm: {months} months\n"
                f"Requested websites: {websites}\nAmount: {amount:.2f} USDT equivalent\n"
                f"Network: {network}\nWallet: {wallet}\nTransaction hash: {transaction_hash}\n"
                f"Order ID: {order_id}\n\nReview this payment in the Nexa admin dashboard."
            ),
        )
    except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as error:
        app.logger.error("Payment order %s was stored but admin email failed: %s", order_id, error)
        notification_warning = (
            "Your payment request was recorded and appears in the admin dashboard, "
            "but the admin notification email could not be sent. Do not submit it again."
        )
    return jsonify(ok=True, order_id=order_id, amount_usdt=float(amount), warning=notification_warning), 201


@app.post("/api/billing/request-plan")
def request_plan():
    return jsonify(error="Paid plans now require a USDT payment order for administrator review."), 410


@app.get("/api/public/plans")
def public_plans():
    return jsonify(public_plan_data())


def public_plan_data():
    with connect_db() as connection:
        settings = get_billing_settings(connection)
    return {
        "free": {
            "name": "Free",
            "price_per_month": 0,
            "monthly_requests": FREE_REQUESTS_PER_MONTH or None,
            "websites": 1,
        },
        "pro": {
            "name": settings["plans"]["pro"]["name"],
            "price_per_month": settings["plans"]["pro"]["price_per_site_month"],
            "monthly_requests": None,
            "websites": 1,
        },
        "custom": {
            "name": settings["plans"]["custom"]["name"],
            "price_per_site_month": settings["plans"]["custom"]["price_per_site_month"],
            "monthly_requests": None,
            "minimum_websites": 2,
        },
    }


@app.get("/api/admin/settings")
def admin_settings():
    failure = require_admin()
    if failure:
        return failure
    with connect_db() as connection:
        return jsonify(billing=get_billing_settings(connection))


@app.put("/api/admin/settings")
def update_admin_settings():
    failure = require_admin()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    plans = payload.get("plans")
    discounts = payload.get("term_discounts")
    wallets = payload.get("wallets")
    if not isinstance(plans, dict) or not isinstance(discounts, dict) or not isinstance(wallets, dict):
        return jsonify(error="Billing settings are incomplete."), 400
    normalized_plans = {}
    for plan_id in ("pro", "custom"):
        item = plans.get(plan_id)
        if not isinstance(item, dict):
            return jsonify(error=f"Pricing for {plan_id} is required."), 400
        name = str(item.get("name", "")).strip()
        price = item.get("price_per_site_month")
        if (
            not name or len(name) > 30 or isinstance(price, bool)
            or not isinstance(price, (int, float)) or not 0 <= price <= 100000
        ):
            return jsonify(error=f"Enter a valid name and monthly price for {plan_id}."), 400
        normalized_plans[plan_id] = {
            "name": name,
            "price_per_site_month": round(float(price), 2),
        }
    normalized_discounts = {}
    for months in ("1", "2", "6", "12"):
        discount = discounts.get(months)
        if isinstance(discount, bool) or not isinstance(discount, int) or not 0 <= discount <= 80:
            return jsonify(error=f"The {months}-month discount must be from 0% to 80%."), 400
        normalized_discounts[months] = discount
    normalized_wallets = {}
    for network in ("TRC20", "ERC20", "BEP20"):
        address = wallets.get(network, "")
        if not isinstance(address, str) or len(address.strip()) > 120:
            return jsonify(error=f"Enter a valid {network} wallet address."), 400
        address = address.strip()
        if address and not is_valid_wallet_address(network, address):
            return jsonify(error=f"Enter a valid {network} wallet address."), 400
        normalized_wallets[network] = address
    settings = {
        "plans": normalized_plans,
        "term_discounts": normalized_discounts,
        "wallets": normalized_wallets,
    }
    with connect_db() as connection:
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES ('billing', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(settings),),
        )
    return jsonify(ok=True, billing=settings)


@app.post("/api/admin/payment-orders/<order_id>/review")
def review_payment_order(order_id):
    failure = require_admin()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    approve = payload.get("approve")
    note = str(payload.get("admin_note", "")).strip()
    activated_websites = payload.get("activated_websites")
    if not isinstance(approve, bool):
        return jsonify(error="Choose whether to approve or reject the payment."), 400
    if len(note) > 1000:
        return jsonify(error="The review response must be 1,000 characters or fewer."), 400
    with connect_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute(
            """
            SELECT p.*, u.email, u.subscription_expires_at
            FROM payment_orders p JOIN users u ON u.id = p.user_id
            WHERE p.id = ? AND p.status = 'pending'
            """,
            (order_id,),
        ).fetchone()
        if not order:
            return jsonify(error="This pending payment could not be found."), 404
        if activated_websites is None:
            activated_websites = order["requested_websites"]
        minimum_activated_websites = 2 if order["requested_plan"] == "custom" else 1
        if (
            isinstance(activated_websites, bool)
            or not isinstance(activated_websites, int)
            or not minimum_activated_websites <= activated_websites <= 100
        ):
            return jsonify(error=f"The activated website count must be between {minimum_activated_websites} and 100."), 400
        status = "approved" if approve else "rejected"
        connection.execute(
            """
            UPDATE payment_orders
            SET status = ?, admin_note = ?, activated_websites = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, note, activated_websites if approve else 0, order_id),
        )
        if approve:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            previous = order["subscription_expires_at"]
            if previous:
                try:
                    previous_expiry = datetime.fromisoformat(previous)
                except ValueError as error:
                    raise RuntimeError("The saved subscription expiry date is invalid.") from error
                if previous_expiry > now:
                    now = previous_expiry
            expires_at = add_months(now, order["term_months"]).isoformat(sep=" ")
            connection.execute(
                """
                UPDATE users SET plan = ?, website_limit = ?, subscription_expires_at = ?
                WHERE id = ?
                """,
                (order["requested_plan"], activated_websites, expires_at, order["user_id"]),
            )
        customer_email = order["email"]
        plan_name = get_billing_settings(connection)["plans"][order["requested_plan"]]["name"]
    response_text = note or (
        "Your payment was verified and the subscription is active."
        if approve else "The payment could not be approved. Contact support if you need help."
    )
    notification_warning = None
    try:
        send_customer_payment_email(
            customer_email,
            f"Nexa payment {status}",
            (
                f"Your {plan_name} payment request for {order['amount_usd']:.2f} USDT "
                f"({order['term_months']} months) was {status}.\n"
                f"Activated websites: {activated_websites if approve else 0}\n"
                f"Admin response: {response_text}\n"
                f"Order ID: {order_id}"
            ),
        )
    except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as error:
        app.logger.error("Payment order %s was reviewed but customer email failed: %s", order_id, error)
        notification_warning = (
            "The payment status was updated, but the customer email could not be sent. "
            "The response is visible in the customer dashboard."
        )
    return jsonify(
        ok=True,
        status=status,
        activated_websites=activated_websites if approve else 0,
        warning=notification_warning,
    )


@app.patch("/api/admin/users/<user_id>")
def update_admin_user(user_id):
    failure = require_admin()
    if failure:
        return failure
    if user_id == g.user["id"]:
        return jsonify(error="Use the configured administrator email to manage this account."), 400
    payload = request.get_json(silent=True) or {}
    plan = payload.get("plan")
    status = payload.get("status")
    website_limit = payload.get("website_limit")
    expiry = payload.get("subscription_expires_at")
    if plan not in {"free", "pro", "custom"}:
        return jsonify(error="Choose a valid account plan."), 400
    if status not in {"active", "suspended"}:
        return jsonify(error="Choose active or suspended account status."), 400
    minimum_website_limit = 2 if plan == "custom" else 1
    if (
        isinstance(website_limit, bool)
        or not isinstance(website_limit, int)
        or not minimum_website_limit <= website_limit <= 100
    ):
        return jsonify(error=f"Website allowance must be between {minimum_website_limit} and 100 for this plan."), 400
    if expiry in ("", None):
        expiry = None
    elif isinstance(expiry, str):
        try:
            parsed_expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if parsed_expiry.tzinfo:
                parsed_expiry = parsed_expiry.astimezone(timezone.utc).replace(tzinfo=None)
            expiry = parsed_expiry.isoformat(sep=" ")
        except ValueError:
            return jsonify(error="Enter a valid subscription expiry date."), 400
    else:
        return jsonify(error="Enter a valid subscription expiry date."), 400
    if plan == "free":
        website_limit = 1
        expiry = None
    with connect_db() as connection:
        cursor = connection.execute(
            """
            UPDATE users SET plan = ?, status = ?, website_limit = ?, subscription_expires_at = ?
            WHERE id = ? AND role != 'admin'
            """,
            (plan, status, website_limit, expiry, user_id),
        )
        if cursor.rowcount == 0:
            exists = connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
            if not exists:
                return jsonify(error="This account could not be found."), 404
            return jsonify(error="Another administrator account cannot be modified here."), 403
        updated = connection.execute(
            "SELECT id, email, plan, status, website_limit, subscription_expires_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return jsonify(dict(updated))


@app.delete("/api/admin/users/<user_id>")
def delete_admin_user(user_id):
    failure = require_admin()
    if failure:
        return failure
    if user_id == g.user["id"]:
        return jsonify(error="You cannot delete the active administrator account."), 400
    with connect_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT email FROM users WHERE id = ? AND role != 'admin'", (user_id,)
        ).fetchone()
        if not user:
            exists = connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
            if not exists:
                return jsonify(error="This account could not be found."), 404
            return jsonify(error="Another administrator account cannot be deleted here."), 403
        connection.execute(
            """
            DELETE FROM chat_requests
            WHERE owner_id = ? OR site_id IN (SELECT id FROM sites WHERE owner_id = ?)
            """,
            (user_id, user_id),
        )
        connection.execute("DELETE FROM sites WHERE owner_id = ?", (user_id,))
        connection.execute("DELETE FROM plan_requests WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM payment_orders WHERE user_id = ?", (user_id,))
        connection.execute(
            "DELETE FROM login_codes WHERE email = ? COLLATE NOCASE", (user["email"],)
        )
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return jsonify(ok=True)


@app.get("/api/admin/metrics")
def admin_metrics():
    failure = require_admin()
    if failure:
        return failure
    try:
        days = int(request.args.get("days", "30"))
    except ValueError:
        return jsonify(error="Days must be a number between 1 and 90."), 400
    if not 1 <= days <= 90:
        return jsonify(error="Days must be a number between 1 and 90."), 400
    interval = f"-{days - 1} days"
    with connect_db() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT visitor_id) AS visitors,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful,
                   SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS unsuccessful,
                   AVG(CASE WHEN status = 'success' THEN first_response_ms END) AS avg_ms
            FROM chat_requests
            WHERE created_at >= datetime('now', ?)
            """,
            (interval,),
        ).fetchone()
        daily = connection.execute(
            """
            SELECT date(created_at) AS day, COUNT(*) AS requests,
                   COUNT(DISTINCT visitor_id) AS visitors,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful,
                   AVG(CASE WHEN status = 'success' THEN first_response_ms END) AS avg_ms
            FROM chat_requests
            WHERE created_at >= datetime('now', ?)
            GROUP BY date(created_at) ORDER BY day
            """,
            (interval,),
        ).fetchall()
        users = connection.execute(
            """
            SELECT u.id, u.email, u.role, u.plan, u.status, u.website_limit,
                   u.subscription_expires_at, u.created_at,
                   COUNT(r.id) AS requests_in_period,
                   SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END) AS successful,
                   AVG(CASE WHEN r.status = 'success' THEN r.first_response_ms END) AS avg_ms,
                   COUNT(DISTINCT r.visitor_id) AS visitors
            FROM users u LEFT JOIN chat_requests r
              ON r.owner_id = u.id AND r.created_at >= datetime('now', ?)
            GROUP BY u.id ORDER BY requests_in_period DESC, u.email LIMIT 100
            """,
            (interval,),
        ).fetchall()
        pending = connection.execute(
            """
            SELECT p.id, p.requested_plan, p.created_at, u.email
            FROM plan_requests p JOIN users u ON u.id = p.user_id
            WHERE p.status = 'pending' ORDER BY p.created_at
            """
        ).fetchall()
        payment_orders = connection.execute(
            """
            SELECT p.id, p.requested_plan, p.term_months, p.requested_websites,
                   p.activated_websites, p.amount_usd, p.network, p.wallet_address,
                   p.transaction_hash, p.status, p.admin_note, p.created_at,
                   p.reviewed_at, u.email
            FROM payment_orders p JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC LIMIT 100
            """
        ).fetchall()
        billing_settings = get_billing_settings(connection)
    return jsonify(
        days=days,
        totals={
            "requests": totals["total"] or 0,
            "visitors": totals["visitors"] or 0,
            "successful": totals["successful"] or 0,
            "unsuccessful": totals["unsuccessful"] or 0,
            "avg_response_ms": round(totals["avg_ms"], 2) if totals["avg_ms"] is not None else None,
        },
        daily=[dict(row) for row in daily],
        users=[dict(row) for row in users],
        pending_plan_requests=[dict(row) for row in pending],
        payment_orders=[dict(row) for row in payment_orders],
        plans={
            "free": PLAN_DETAILS["free"],
            **{
                plan_id: {
                    "name": details["name"],
                    "price_usd": details["price_per_site_month"],
                    "monthly_requests": None,
                    "websites": 2 if plan_id == "custom" else 1,
                }
                for plan_id, details in billing_settings["plans"].items()
            },
        },
    )


@app.post("/api/admin/plan-requests/<plan_request_id>/review")
def review_plan_request(plan_request_id):
    return jsonify(error="Legacy plan approvals are disabled. Review the payment order instead."), 410


@app.route("/api/public/sites/<site_id>", methods=["GET", "OPTIONS"])
def read_public_site(site_id):
    if request.method == "OPTIONS":
        return "", 204
    site = get_site(site_id)
    if not site:
        return jsonify(error="This chat assistant is not available."), 404
    return jsonify(public_site(site))


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return "", 204
    payload = request.get_json(silent=True) or {}
    site_id = str(payload.get("site_id", ""))
    site = get_site(site_id)
    if not site:
        return jsonify(error="This chat assistant is not available."), 404
    if not site["knowledge"]:
        return jsonify(error="This assistant needs business information before it can answer. Add details in the dashboard first."), 400

    incoming = payload.get("messages")
    if not isinstance(incoming, list) or not incoming or len(incoming) > 20:
        return jsonify(error="Send between 1 and 20 chat messages."), 400

    messages = [
        {
            "role": "system",
            "content": (
                f"You are the helpful website support assistant for {site['name']}."
                " Answer using only the business information below. If the answer is not"
                " present, say you do not know and suggest contacting the business. Never"
                " invent policies, prices, order statuses, or contact details. Be warm,"
                " concise, and clear.\n\n"
                f"Business information:\n{site['knowledge']}"
            ),
        }
    ]
    for message in incoming:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            return jsonify(error="The chat history contains an invalid message."), 400
        content = message.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            return jsonify(error="Each message must contain between 1 and 4,000 characters."), 400
        messages.append({"role": message["role"], "content": content.strip()})

    visitor_id = str(payload.get("visitor_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", visitor_id):
        return jsonify(error="This chat session is invalid. Refresh the page and try again."), 400
    if not site["owner_id"]:
        return jsonify(error="This assistant is not assigned to an account yet."), 409
    started_at = time.perf_counter()
    try:
        tracked_request, usage, limit, block_reason = start_chat_request(site, visitor_id)
    except RuntimeError as error:
        app.logger.error("Could not apply account limits to website %s: %s", site_id, error)
        return jsonify(error="This assistant is temporarily unavailable."), 503
    if block_reason == "suspended":
        return jsonify(error="This website assistant is temporarily unavailable."), 403
    if block_reason == "website_limit":
        return jsonify(error="This website is outside the active website allowance."), 403
    if block_reason == "monthly_limit":
        return jsonify(
            error=f"This website has used its {limit} free chat requests for this month.",
            code="monthly_limit_reached",
            requests_used=usage,
            requests_limit=limit,
            plans=PLAN_DETAILS,
        ), 429

    try:
        upstream = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": True,
                "keep_alive": "24h",
            },
            stream=True,
            timeout=(5, 300),
        )
        upstream.raise_for_status()
    except requests.RequestException as error:
        finish_request(tracked_request, "error", started_at)
        app.logger.warning("Could not connect to the configured Ollama model: %s", error)
        return jsonify(
            error="The assistant service is temporarily unavailable. Please try again later."
        ), 503

    def generate():
        status = "error"
        first_response_recorded = False
        try:
            for line in upstream.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    app.logger.warning("Ollama returned an invalid streaming response.")
                    continue
                text = chunk.get("message", {}).get("content", "")
                if text:
                    if not first_response_recorded:
                        first_response_ms = round((time.perf_counter() - started_at) * 1000, 2)
                        record_first_response(tracked_request, first_response_ms)
                        first_response_recorded = True
                    yield f"data: {json.dumps({'text': text})}\n\n"
                if chunk.get("error"):
                    app.logger.error("The assistant service returned an error: %s", chunk["error"])
                    yield (
                        "event: error\ndata: "
                        f"{json.dumps({'error': 'The assistant is temporarily unavailable. Please try again later.'})}\n\n"
                    )
                    break
                if chunk.get("done"):
                    status = "success"
                    yield "event: done\ndata: {}\n\n"
                    break
        except requests.RequestException:
            app.logger.exception("The Ollama response stream failed.")
            yield f"event: error\ndata: {json.dumps({'error': 'The model stream ended unexpectedly. Please try again.'})}\n\n"
        finally:
            upstream.close()
            finish_request(tracked_request, status, started_at)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Nexa-Requests-Used": str(usage),
            "X-Nexa-Requests-Limit": str(limit) if limit is not None else "unlimited",
        },
    )


if __name__ == "__main__":
    if production_mode:
        raise RuntimeError(
            "Do not use Flask's development server in production. Run "
            "'waitress-serve --host=0.0.0.0 --port=5000 app:app' behind HTTPS."
        )
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
