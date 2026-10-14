"""The feed's HTTP server: the page, and the replies its script polls for, on 127.0.0.1 only.

`GET /` answers the page. `GET /events?since=<seq>&match=<name>` brings the
followed record up to date and answers what the page has not seen yet: the
events numbered above `since`, the header and, for Red Alert, the order
throughput. `match` is the match the page shows; when the feed follows
another one by now (`--latest` switched to a newer match), the reply says
`reset` and holds that match's events from its first.

Every answer leaves through `secrets.redact`, like every other channel the
agent's code writes to: a record is redacted when it is written, and the
feed redacts again whatever it serves. The server only reads the record; it
writes nothing and connects to nothing.
"""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ucla_agent.core.secrets import redact
from ucla_agent.feed.header import header_fields
from ucla_agent.feed.page import page_html
from ucla_agent.feed.reader import RED_ALERT, MatchReader
from ucla_agent.feed.sources import Source
from ucla_agent.feed.throughput import Throughput

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE_ROUTE = "/"
EVENTS_ROUTE = "/events"
OK = 200
BAD_REQUEST = 400
NOT_FOUND = 404
HTML_TYPE = "text/html; charset=utf-8"
JSON_TYPE = "application/json"
_ENCODING = "utf-8"


class FeedView:
    """The followed match as the page sees it. Requests are answered on threads of their own, one at a time here."""

    def __init__(self, source: Source) -> None:
        self._source = source
        self._lock = threading.Lock()
        self._reader: MatchReader | None = None
        self._throughput = Throughput()

    def reply(self, since: int, match: str | None) -> dict[str, Any]:
        """Bring the record up to date and answer a page that has seen the events up to `since` of `match`."""
        with self._lock:
            new_events = self._source.refresh()
            reader = self._source.reader
            if reader is not self._reader:
                # A new match: its refresh read it from its first event, and its throughput starts from nothing.
                self._reader, self._throughput = reader, Throughput()
            for event in new_events:
                self._throughput.take_in(event)
            return self._reply_of(reader, since, match)

    def _reply_of(self, reader: MatchReader | None, since: int, match: str | None) -> dict[str, Any]:
        if reader is None:
            return {
                "match": None,
                "game_type": None,
                "reset": False,
                "events": [],
                "last_seq": 0,
                "header": {},
                "throughput": None,
            }
        reset = match is not None and match != reader.name
        throughput = self._throughput
        return {
            "match": reader.name,
            "game_type": reader.game_type,
            "reset": reset,
            "events": [event.as_dict() for event in reader.events_after(0 if reset else since)],
            "last_seq": reader.last_seq,
            "header": header_fields(reader.game_type, reader.latest_state, reader.closing, reader.meta.get("agent_id")),
            "throughput": (
                {"figures": throughput.figures(), "series": throughput.series()}
                if reader.game_type == RED_ALERT
                else None
            ),
        }


class _Handler(BaseHTTPRequestHandler):
    """The HTTP around the view; `view` is set on the subclass a server is built with."""

    view: FeedView

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        address = urlsplit(self.path)
        if address.path == PAGE_ROUTE:
            self._send(OK, HTML_TYPE, page_html().decode(_ENCODING))
        elif address.path == EVENTS_ROUTE:
            self._send_events(parse_qs(address.query))
        else:
            self._send(NOT_FOUND, JSON_TYPE, json.dumps({"error": "not_found"}))

    def log_message(self, _format: str, *_arguments: Any) -> None:
        """Requests are not logged: the page polls once a second, and the base class would print a line for each."""

    def _send_events(self, query: dict[str, list[str]]) -> None:
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            self._send(BAD_REQUEST, JSON_TYPE, json.dumps({"error": "since must be a whole number"}))
            return
        match = query.get("match", [None])[0]
        self._send(OK, JSON_TYPE, json.dumps(self.view.reply(since, match), default=repr))

    def _send(self, status: int, content_type: str, text: str) -> None:
        payload = redact(text).encode(_ENCODING)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


class FeedServer(ThreadingHTTPServer):
    """The feed's server, bound to 127.0.0.1 on the given port (0 for any free one), one thread per request."""

    daemon_threads = True
    # On Windows the address-reuse option lets a second server bind a port that is already served. Elsewhere it
    # only allows binding again right after a restart, which is wanted.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, port: int, view: FeedView) -> None:
        """Bind 127.0.0.1:`port` at once. Raises `OSError` when the port is taken."""
        handler = type("FeedHandler", (_Handler,), {"view": view})
        super().__init__((LOOPBACK, port), handler)

    @property
    def port(self) -> int:
        """The port the server is bound to."""
        return self.server_address[1]

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Not the HTTP server's own bind: that one also looks the host's name up, which can take seconds and is of
        # no use to a loopback server.
        socketserver.TCPServer.server_bind(self)
