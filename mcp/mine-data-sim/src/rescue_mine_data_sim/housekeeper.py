"""演示轮次的归档服务（只用于演示环境，docs/deploy.md）。

控制台点“新一轮演示”→ 模拟矿井 POST /sim/demo/round（控制令牌）→ 转给本服务 POST /round：
1. 控制目录的 round.json 轮次加一、置 resetting；各容器里的守护进程（rescue_contract.rounds）停掉服务并报告 stopped；
2. 全部停下后，把上一轮的数据（记录、事件、值班会话、技能运行目录、台账、审计、工作区、DSH 的会话）
   **移到** archive/round-<轮次>-<时间>/（不删除：台账和审计是复盘依据）；DSH 的配置档和网页端令牌保留，链接不变；
3. 模拟矿井回到常态（/sim/demo/normal，导演也复位）；
4. 置 running，各服务重新启动。

只移动各数据目录**里面**的内容，不移动目录本身：这些目录是其他容器的挂载点，挪走目录本身会让容器继续写进归档里。

    python -m rescue_mine_data_sim.housekeeper            # 服务（端口 RESCUE_HOUSEKEEPER_PORT，缺省 8109）
    python -m rescue_mine_data_sim.housekeeper archive    # 只归档（服务都已停止时用，scripts/demo.sh reset）

环境变量：RESCUE_DEMO_CONTROL_DIR（控制目录）、RESCUE_DEMO_DATA（数据根目录，下有各数据目录和 archive/）、
RESCUE_DEMO_SERVICES（要等停下的服务，逗号分隔）、RESCUE_SIM_CONTROL_URL（模拟矿井的 /sim）、RESCUE_SIM_CONTROL_TOKEN。
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from rescue_contract.rounds import RESETTING, RUNNING, read_login, read_round, read_service, write_json

# 每轮清空（内容移到归档）的数据目录；dsh-home 只移走会话相关的几项，配置档、网页端令牌（.credentials.yaml）、pnpm 缓存保留
DATA_DIRS = ("records", "incidents", "duty", "runs", "ledger", "audit", "documents")
DSH_HOME = "dsh-home"
DSH_HOME_ITEMS = ("sessions", "storages", "logs")


def archive_round(data: Path, round_no: int, now: datetime | None = None) -> Path | None:
    """把上一轮的数据移到 archive/ 下；没有任何内容时不建归档目录，返回 None。"""
    now = now or datetime.now()
    dest = data / "archive" / f"round-{round_no:04d}-{now:%Y%m%d-%H%M%S}"
    moved = 0
    plan: list[tuple[Path, Path]] = []
    for name in DATA_DIRS:
        src = data / name
        if src.is_dir():
            plan += [(p, dest / name / p.name) for p in sorted(src.iterdir())]
    home = data / DSH_HOME
    plan += [(home / n, dest / DSH_HOME / n) for n in DSH_HOME_ITEMS if (home / n).exists()]
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        moved += 1
    if not moved:
        return None
    write_json(dest / "round.json", {"round": round_no, "archived_at": now.isoformat(timespec="seconds"), "items": moved})
    return dest


class Housekeeper:
    def __init__(
        self,
        control: Path,
        data: Path,
        services: list[str],
        sim_url: str | None,
        sim_token: str | None,
        stop_timeout_s: float = 90.0,
        poll_s: float = 1.0,
    ) -> None:
        self.control, self.data, self.services = control, data, services
        self.sim_url, self.sim_token = (sim_url or "").rstrip("/") or None, sim_token
        self.stop_timeout_s, self.poll_s = stop_timeout_s, poll_s
        self.lock = threading.Lock()
        self.job: dict[str, Any] = {"state": "idle"}
        control.mkdir(parents=True, exist_ok=True)
        st = read_round(control)
        if st["phase"] == RESETTING:  # 上次重置中途退出：先放行，否则服务永远等着
            write_json(control / "round.json", {**st, "phase": RUNNING, "note": "归档服务重启，恢复运行"})

    def status(self) -> dict[str, Any]:
        st = read_round(self.control)
        return {
            "available": True,
            "round": st["round"],
            "phase": st["phase"],
            "job": dict(self.job),
            "services": {s: read_service(self.control, s) for s in self.services},
            # 网页端当前的登录链接（DSH 每次启动都换；已经登录过的浏览器靠 cookie，不受影响）
            "login_url": next((x["url"] for s in self.services if (x := read_login(self.control, s))), None),
        }

    def start(self, by: str = "console") -> dict[str, Any]:
        with self.lock:
            if self.job.get("state") in ("stopping", "archiving", "resetting_mine"):
                raise RuntimeError("上一次“新一轮演示”还没完成")
            st = read_round(self.control)
            new = st["round"] + 1
            write_json(self.control / "round.json", {"round": new, "phase": RESETTING, "requested_at": datetime.now().isoformat(timespec="seconds"), "by": by})
            self.job = {"state": "stopping", "round": new, "started": time.time(), "message": "正在停止服务"}
        threading.Thread(target=self._run, args=(new,), daemon=True).start()
        return self.status()

    def _stopped(self, round_no: int) -> list[str]:
        """还没停下的服务。"""
        waiting = []
        for s in self.services:
            rep = read_service(self.control, s)
            if not rep or rep.get("round", -1) < round_no or rep.get("state") not in ("stopped", "exited"):
                waiting.append(s)
        return waiting

    def _set(self, **kw: Any) -> None:
        self.job = {**self.job, **kw}

    def _run(self, round_no: int) -> None:
        archive: Path | None = None
        try:
            deadline = time.time() + self.stop_timeout_s
            while waiting := self._stopped(round_no):
                if time.time() > deadline:
                    raise RuntimeError(f"服务没有停下：{'、'.join(waiting)}（没有归档，服务继续运行上一轮的数据）")
                self._set(message=f"正在停止服务（等待 {'、'.join(waiting)}）")
                time.sleep(self.poll_s)
            self._set(state="archiving", message="正在归档上一轮的数据")
            archive = archive_round(self.data, round_no - 1)
            self._set(state="resetting_mine", message="模拟矿井回到常态", archive=archive.name if archive else None)
            self._reset_mine()
            self._set(state="done", finished=time.time(), message=f"第 {round_no} 轮已就绪" + (f"，上一轮归档在 {archive.name}" if archive else ""))
        except Exception as exc:  # noqa: BLE001 - 失败原因要显示到控制台
            self._set(state="failed", finished=time.time(), message=str(exc))
        finally:
            st = read_round(self.control)
            write_json(self.control / "round.json", {**st, "phase": RUNNING})

    def _reset_mine(self) -> None:
        if not self.sim_url:
            return
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {self.sim_token}"} if self.sim_token else {})}
        last: Exception | None = None
        for _ in range(5):
            try:
                req = urllib.request.Request(f"{self.sim_url}/demo/normal", data=b"{}", headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(2)
        raise RuntimeError(f"模拟矿井回到常态失败：{last}")


def make_handler(hk: Housekeeper, token: str | None) -> type[BaseHTTPRequestHandler]:
    expected = f"Bearer {token}".encode() if token else None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if expected is None:
                return True
            got = (self.headers.get("Authorization") or "").encode()
            if hmac.compare_digest(got, expected):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"ok": True, "server": "housekeeper"})
            if self.path == "/round" and self._authorized():
                return self._send(200, hk.status())
            if self.path != "/round":
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/round":
                return self._send(404, {"error": "not found"})
            if not self._authorized():
                return
            try:
                self._send(202, hk.start())
            except RuntimeError as exc:
                self._send(409, {"error": str(exc)})

        def log_message(self, fmt: str, *args: Any) -> None:  # 只记 POST，轮询不刷屏
            if self.command == "POST":
                sys.stderr.write(f"housekeeper: {self.address_string()} {fmt % args}\n")

    return Handler


def from_env() -> Housekeeper:
    return Housekeeper(
        Path(os.environ["RESCUE_DEMO_CONTROL_DIR"]),
        Path(os.environ["RESCUE_DEMO_DATA"]),
        [s for s in os.environ.get("RESCUE_DEMO_SERVICES", "dsh,actions,sentinel").split(",") if s],
        os.environ.get("RESCUE_SIM_CONTROL_URL"),
        os.environ.get("RESCUE_SIM_CONTROL_TOKEN"),
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["archive"]:
        control, data = Path(os.environ["RESCUE_DEMO_CONTROL_DIR"]), Path(os.environ["RESCUE_DEMO_DATA"])
        control.mkdir(parents=True, exist_ok=True)
        st = read_round(control)
        dest = archive_round(data, st["round"])
        write_json(control / "round.json", {"round": st["round"] + 1, "phase": RUNNING, "by": "demo.sh reset"})
        print(f"已归档到 {dest.relative_to(data)}" if dest else "没有需要归档的数据")
        return 0
    hk = from_env()
    port = int(os.environ.get("RESCUE_HOUSEKEEPER_PORT", "8109"))
    host = os.environ.get("RESCUE_MCP_HOST", "127.0.0.1")
    token = os.environ.get("RESCUE_SIM_CONTROL_TOKEN")
    print(f"housekeeper: 监听 {host}:{port}，等待停下的服务：{'、'.join(hk.services)}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((host, port), make_handler(hk, token)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
