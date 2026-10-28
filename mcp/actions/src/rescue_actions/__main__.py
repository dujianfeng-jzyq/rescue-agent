"""python -m rescue_actions  —— 启动 actions 服务（默认 127.0.0.1:8106/mcp，预检接口 /redlines/check）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "actions")


if __name__ == "__main__":
    main()
