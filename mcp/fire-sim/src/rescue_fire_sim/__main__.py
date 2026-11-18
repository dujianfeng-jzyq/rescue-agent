"""python -m rescue_fire_sim  —— 启动 fire_sim 服务（默认 127.0.0.1:8108/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "fire_sim")


if __name__ == "__main__":
    main()
