"""python -m rescue_mine_data_sim  —— 启动模拟矿井 mine-data 服务（默认 127.0.0.1:8101/mcp）。"""

from rescue_contract.serving import serve

from .server import build_server


def main() -> None:
    serve(build_server(), "mine_data")


if __name__ == "__main__":
    main()
