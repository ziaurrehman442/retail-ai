from waitress import serve

from app import app

if __name__ == "__main__":
    serve(
        app,
        host="127.0.0.1",
        port=5010,
        threads=16,
        channel_timeout=300,
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
        clear_untrusted_proxy_headers=True,
    )
