"""python -m rescue_ventnet  —— 启动 ventnet 服务（默认 127.0.0.1:8102/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "ventnet")


if __name__ == "__main__":
    main()
