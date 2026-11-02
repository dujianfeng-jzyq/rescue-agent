"""python -m rescue_workflow  —— 启动 workflow 服务（默认 127.0.0.1:8107/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "workflow")


if __name__ == "__main__":
    main()
