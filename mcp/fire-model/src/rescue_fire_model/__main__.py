"""python -m rescue_fire_model  —— 启动 fire-model 服务（默认 127.0.0.1:8103/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "fire_model")


if __name__ == "__main__":
    main()
