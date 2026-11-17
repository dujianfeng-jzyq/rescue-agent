"""python -m rescue_route_planner  —— 启动 route_planner 服务（默认 127.0.0.1:8107/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "route_planner")


if __name__ == "__main__":
    main()
