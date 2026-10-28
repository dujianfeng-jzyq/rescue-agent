"""python -m rescue_kb  —— 启动 kb 封装层（默认 127.0.0.1:8105/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "kb")


if __name__ == "__main__":
    main()
