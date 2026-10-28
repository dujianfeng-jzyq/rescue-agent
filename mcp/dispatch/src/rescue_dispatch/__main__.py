"""python -m rescue_dispatch  —— 启动 dispatch 服务（默认 127.0.0.1:8104/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "dispatch")


if __name__ == "__main__":
    main()
