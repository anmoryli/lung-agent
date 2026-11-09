"""Agent Service HTTP 服务入口。"""

from __future__ import annotations

import logging
import os
from http.server import ThreadingHTTPServer

from backend.api.v1.handler import AgentRequestHandler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.getenv("AGENT_SERVICE_PORT", "8010"))
    host = os.getenv("AGENT_BIND_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), AgentRequestHandler)
    logging.info("Agent Service 已启动，监听 %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
