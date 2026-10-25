# Nimbus website chat

Nimbus is a small Python + React starter for building customer-specific AI chat
widgets. The Flask backend saves each website's business information in SQLite
and supplies that information to a small Ollama model for each streamed answer.
Business details stay on the server and are not included in the public embed
snippet.

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer
- Ollama, running locally, with `qwen2.5:3b` downloaded

## Run locally

In one terminal, install the Python requirements and start Ollama's model:

```powershell
python -m pip install -r requirements.txt
ollama pull qwen2.5:3b
ollama serve
```

In another terminal, install and start the Python API:

```powershell
python app.py
```

In a third terminal, start the React dashboard:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://127.0.0.1:5173`).
The React dev server proxies API calls to Flask at `http://127.0.0.1:5000`.

To choose another Ollama model, set `OLLAMA_MODEL` before starting Flask, for
example:

```powershell
$env:OLLAMA_MODEL = "qwen2.5:3b"
python app.py
```

For a single-origin local deployment, build React and serve the result through
Flask:

```powershell
cd frontend
npm install
npm run build
cd ..
python app.py
```

## Add your assistant to a website

1. Open **Knowledge base**, enter the customer's business name and website, and
   add their products, services, FAQs, and policies.
2. Save the details. The assistant uses this content as its live context; this
   starter version does not crawl a website or train/fine-tune a model.
3. Open **Install widget**, copy the unique embed code, and place it just before
   the closing `</body>` tag:

   ```html
   <script src="https://YOUR-APP-HOST/widget.js"
           data-site-id="THE-WEBSITE-ID"
           async></script>
   ```

The script loads the assistant name from Flask and displays an animated
bottom-corner chat button. Replies stream from Ollama as the model generates
them. The widget expects its Flask API to be reachable at the same origin as
the `widget.js` URL.

## Data and production readiness

Set `NIMBUS_DB_PATH` to change where the SQLite database is stored. The MVP has
no user authentication, tenant authorization, website-domain allow-list,
conversation persistence, rate limiting, or automatic site crawler. Add those
controls, HTTPS, backups, and a reviewed data-retention policy before
production use. The public chat endpoints are intended for embedding, not as an
access-control boundary.

Small-model grounding is retrieval via the saved business information, not
model training. Fine-tuning is a separate, optional future step: first collect
consented conversations, remove personal data, review answer quality, and
prepare a representative dataset.
