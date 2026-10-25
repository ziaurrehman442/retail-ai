# Nexa website chat

Nexa is a React dashboard and embeddable customer chat widget backed by a
Python/Flask API, SQLite, and a small local Ollama model. Flask serves the
prebuilt React files, so the deployed app does **not** need a separate React
server.

## Run the app

1. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in
   PowerShell). Set your admin email, a random
   `NIMBUS_SECRET_KEY`, and your SMTP provider's host, sender, username, and
   app-password. The admin email becomes an administrator after its owner
   verifies a one-time email code. No dashboard password is used.
2. Install Python packages and prepare the model:

   ```powershell
   python -m pip install -r requirements.txt
   ollama pull qwen2.5:3b
   ```

3. Start the complete website from the project root:

   ```powershell
   python app.py
   ```

4. Visit `http://127.0.0.1:5000`.

The public product homepage, passwordless sign-in, dashboard, and embedded
widget are served by Flask from the same origin. The React development server
is optional. For a separate frontend host, configure a same-origin reverse
proxy to the Flask API; do not ship a localhost backend URL to customers.

## Search engine setup

The public homepage includes a descriptive title and metadata for search and
social sharing, SoftwareApplication structured data, `robots.txt`, and an XML
sitemap at `/sitemap.xml`. Its three plans and public prices are embedded in
the page, so visitors can view them without signing in or a separate pricing
API request. Set `NIMBUS_PUBLIC_URL` to the canonical HTTPS
homepage origin (for example, `https://your-real-domain.example`) in deployment
configuration. Without it, canonical metadata and the sitemap use the current
request host; configure a fixed public URL before production to avoid indexing
staging or alternate hostnames.

After deploying on your real domain, verify the homepage and sitemap in Google
Search Console and Bing Webmaster Tools, then request indexing. Rankings for
terms such as “retail AI” or “AI chatbot for retail” depend on the real domain,
competition, useful content, and external signals; metadata cannot guarantee a
ranking. Nexa supports general business websites, and the homepage describes
the retail chatbot use case without claiming unsupported retail-specific
integrations.

The email sign-in codes expire after 10 minutes and have request and attempt
limits. To test on a private development computer without configuring SMTP,
set `NIMBUS_ALLOW_CONSOLE_OTP=true` in `.env`; codes are then printed in the
Python console. Do not enable console OTP on a shared or public server.

For **frontend development only**, run `npm install` and `npm run dev` inside
`frontend`. To update the checked-in, Flask-served frontend after changing
React source, run `npm run build` from `frontend`. Normal app use still requires
only `python app.py`.

## Accounts and roles

- Customers enter an email and verify a one-time code to sign in or create an
  account. No password login is provided.
- Set `NIMBUS_ADMIN_EMAIL` in `.env` to identify the administrator account.
  The address receives admin privileges after its owner verifies an email OTP.
- Admins can inspect platform-wide request totals, approximate unique browsers,
  per-account usage counts, successful response rate, and average time to first
  response text (from the visitor's request until the first answer text arrives).
  Only requests recorded after this timing update contribute to the metric;
  earlier records tracked full completion time instead.
  Admin APIs do not list customer websites, URLs, knowledge content, or
  individual visitor requests. Website workspace APIs only return workspaces
  owned by the signed-in account.
- Admins can suspend or delete customer accounts, assign plans and website
  allowances, and set subscription expiry dates.

## Plans and usage

- The public homepage shows Free (30 requests/month, one website), Pro
  (unlimited requests, one website, $5/month by default), and Custom (unlimited
  requests, at least two websites, $4 per website/month by default). Paid prices
  shown publicly follow the current admin-configured per-site monthly rates.
- **Free:** 30 chat requests per account per calendar month by default and one website.
- **Pro** and **Custom:** prices are editable by administrators, per website per
  month. Custom subscriptions require at least two websites. Admins configure
  discounts for 1, 2, 6, and 12-month terms.
- Customers can request 1–100 website slots. Only the allowance approved by an
  administrator is activated. The administrator may adjust the site count
  before approving a payment.
- Set `NIMBUS_FREE_REQUESTS_PER_MONTH` in `.env` to change the free allowance;
  setting it to `0` removes the monthly request cap.

### USDT payment review

The admin dashboard lets administrators configure receiving wallet addresses
for TRC20, ERC20, and BEP20 and choose pricing and term discounts. Customers
select a configured network, plan, term, and website count, send the displayed
USDT amount, and submit the transaction hash. A locally generated QR code
encodes the configured wallet address. Always independently verify the
transaction, token, amount, recipient, and chain in a trusted wallet or
explorer before approving. The app does **not** automatically verify blockchain
payments or custody funds.

Pending requests appear in the admin dashboard and trigger an email to
`NIMBUS_ADMIN_EMAIL`. On review, administrators may set the actual active
website allowance and include a response. That response is shown in the
customer billing dashboard and sent by email. SMTP must be configured for
notifications. A failed notification is reported in the API response; the
recorded payment remains visible in the dashboard.

USDT values are calculated as a 1:1 equivalent of USD pricing. No exchange-rate
feed, blockchain transaction verification, refund workflow, or payment
processor is connected. Do not treat this manual-review flow as automated
payment processing.

Each chat request is logged with the owning account, website, anonymous
browser-generated visitor ID, UTC timestamp, outcome, and model-response
latency. Browser IDs approximate unique visitors; they are not verified human
identities and can be cleared or changed by the visitor.

## Website knowledge and widget

1. Open **Knowledge base**, enter a business name and public website URL, and
   select **Fetch website content**. The importer reads the homepage and at most
   four relevant, same-host public HTML pages. Review the imported text and
   save it before using the assistant.
2. Open **Install widget** and place its unique script snippet before the
   website's closing `</body>` tag.
3. The floating widget streams Ollama responses and safely renders Markdown
   formatting such as **bold**, headings, lists, links, tables, and code. It
   can answer in English or Urdu, follows the visitor's language, and supports
   both Urdu script and Roman Urdu.

To test the widget from a local `.html` file, make the script URL point to the
Flask server, for example `http://127.0.0.1:5000/widget.js`. Do not use the Vite
development-server address (`:5173`) in a standalone file: its development
proxy is intended for a page opened from Vite, and a `file://` page has the
special browser origin `null`. Flask already provides the CORS preflight
headers required by the standalone widget.

Business information stays server-side and is sent as context to the small
model at answer time; this is retrieval, not model fine-tuning.

## Environment reference

See `.env.example` for SMTP settings, the admin email, `NIMBUS_SECRET_KEY`,
`NIMBUS_DB_PATH`, `NIMBUS_PUBLIC_URL`, `NIMBUS_COOKIE_SECURE`,
`NIMBUS_FREE_REQUESTS_PER_MONTH`, `OLLAMA_URL`, and `OLLAMA_MODEL`.
Set `NIMBUS_COOKIE_SECURE=true` when serving the dashboard over HTTPS.

For production, set `NIMBUS_ENV=production`, use a stable random
`NIMBUS_SECRET_KEY` of at least 32 characters, and set
`NIMBUS_COOKIE_SECURE=true`. The app refuses to start in production without
those settings and refuses to run Flask's development server in production.
Install dependencies, build the frontend, then start the WSGI server behind an
HTTPS reverse proxy:

```powershell
waitress-serve --host=127.0.0.1 --port=5000 app:app
```

Before a real launch, configure HTTPS, SMTP, database backups, operational
monitoring, request limits appropriate for your traffic, and a database sized
for your deployment. The website importer pins connections to validated public
IP addresses, rejects non-public DNS results, rechecks redirects, and preserves
TLS certificate verification. Public widget endpoints remain unauthenticated
so customer websites can call them; customer dashboard APIs and account data
require OTP authentication and CSRF validation.

No application can be guaranteed free of security issues. This implementation
adds specific protections, but needs deployment-specific review, monitoring,
and ongoing updates before being described as fully production-ready.
