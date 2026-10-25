import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
FRONTEND_STATIC = BASE_DIR / "frontend" / "static"
DB_PATH = Path(os.environ.get("NIMBUS_DB_PATH", BASE_DIR / "nimbus.sqlite3"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

app = Flask(__name__)


@contextmanager
def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                website TEXT NOT NULL DEFAULT '',
                knowledge TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO sites (id, name, website, knowledge) VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, "My first website", "", ""),
            )


def get_site(site_id):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT id, name, website, knowledge, created_at FROM sites WHERE id = ?",
            (site_id,),
        ).fetchone()
    return dict(row) if row else None


def public_site(site):
    return {
        "id": site["id"],
        "name": site["name"],
        "website": site["website"],
    }


initialize_db()


@app.after_request
def add_public_widget_headers(response):
    if request.path.startswith("/api/public/") or request.path == "/api/chat":
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.get("/")
def index():
    if FRONTEND_DIST.exists():
        return send_from_directory(FRONTEND_DIST, "index.html")
    return (
        "React frontend is not built. Run `cd frontend && npm install && npm run build`, "
        "or start the development frontend with `npm run dev`.",
        503,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.get("/assets/<path:filename>")
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.get("/widget.js")
def website_widget():
    return send_from_directory(FRONTEND_STATIC, "widget.js")


@app.get("/api/sites")
def list_sites():
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT id, name, website, knowledge, created_at FROM sites ORDER BY created_at, rowid"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/sites")
def create_site():
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
        connection.execute(
            "INSERT INTO sites (id, name, website, knowledge) VALUES (?, ?, ?, ?)",
            (site["id"], site["name"], site["website"], site["knowledge"]),
        )
    return jsonify(site), 201


@app.get("/api/sites/<site_id>")
def read_site(site_id):
    site = get_site(site_id)
    if not site:
        return jsonify(error="This website workspace could not be found."), 404
    return jsonify(site)


@app.put("/api/sites/<site_id>")
def update_site(site_id):
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

    try:
        upstream = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
            stream=True,
            timeout=(5, 180),
        )
        upstream.raise_for_status()
    except requests.RequestException as error:
        app.logger.warning("Could not connect to the configured Ollama model: %s", error)
        return jsonify(
            error=(
                f"Could not reach Ollama at {OLLAMA_URL}. Start Ollama, pull "
                f"'{OLLAMA_MODEL}', then try again."
            )
        ), 503

    def generate():
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
                    yield f"data: {json.dumps({'text': text})}\n\n"
                if chunk.get("error"):
                    yield f"event: error\ndata: {json.dumps({'error': chunk['error']})}\n\n"
                    break
                if chunk.get("done"):
                    yield "event: done\ndata: {}\n\n"
                    break
        except requests.RequestException:
            app.logger.exception("The Ollama response stream failed.")
            yield f"event: error\ndata: {json.dumps({'error': 'The model stream ended unexpectedly. Please try again.'})}\n\n"
        finally:
            upstream.close()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
