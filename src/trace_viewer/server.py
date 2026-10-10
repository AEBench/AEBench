from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve_trace_site(site_dir: Path, *, host: str = "127.0.0.1", port: int = 8000) -> None:
	site_dir = site_dir.expanduser().resolve()
	if not (site_dir / "index.html").is_file():
		raise ValueError(f"trace directory does not contain index.html: {site_dir}")
	handler = partial(SimpleHTTPRequestHandler, directory=str(site_dir))
	server = ThreadingHTTPServer((host, port), handler)
	print(f"Trace viewer: http://{host}:{port}")
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		pass
	finally:
		server.server_close()
