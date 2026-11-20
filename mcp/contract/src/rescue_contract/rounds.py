"""演示轮次（只用于演示环境，docs/deploy.md）：控制台点“新一轮演示”时，各服务停下、归档上一轮的数据、再重新启动。

不用 Docker 套接字（那等于把宿主机的 root 权限交给容器）：每个有状态的容器里由本模块的守护进程起真正的服务，
守护进程看控制目录里的 round.json：

    {"round": 3, "phase": "resetting" | "running", ...}

- phase 变成 resetting 且 round 比自己正在跑的大：停掉服务（整个进程组，先 SIGTERM 后 SIGKILL），
  在 service-<名>.json 里报告 stopped，等 phase 回到 running 再重新启动服务；
- 服务自己退出：守护进程以同样的退出码退出（容器按重启策略重启）；
- 收到 SIGTERM（docker stop）：停掉服务后退出。

服务的输出原样转发；输出里出现网页端的登录链接（DSH 每次启动生成新的 `?token=`）时记进 login-<名>.json，
控制台据此显示当前的登录链接（服务停下时删掉，旧链接已经失效）。

协调方（归档服务 rescue_mine_data_sim.housekeeper）把 round 加一、置 resetting，等所有服务报告 stopped，
归档数据，复位模拟矿井，再置 running。没有设置控制目录时直接 exec 服务（本机开发、评测不受影响）。

    python -m rescue_contract.rounds <服务名> -- <命令> [参数...]
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

CONTROL_DIR_ENV = "RESCUE_DEMO_CONTROL_DIR"
ROUND_FILE = "round.json"
RUNNING = "running"
RESETTING = "resetting"
LOGIN_URL = re.compile(rb"https?://\S+?/\?token=[A-Za-z0-9_-]+")


def read_round(control: Path) -> dict[str, Any]:
    """当前轮次；文件不存在或损坏时按第 0 轮、running 处理（不能因为控制文件坏了让服务永远不启动）。"""
    try:
        data = json.loads((control / ROUND_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("round"), int) and data.get("phase") in (RUNNING, RESETTING):
            return data
    except (OSError, ValueError):
        pass
    return {"round": 0, "phase": RUNNING}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写（先写临时文件再改名），读的一方不会读到半个文件。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def service_file(control: Path, name: str) -> Path:
    return control / f"service-{name}.json"


def login_file(control: Path, name: str) -> Path:
    return control / f"login-{name}.json"


def _read_obj(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_service(control: Path, name: str) -> dict[str, Any] | None:
    return _read_obj(service_file(control, name))


def read_login(control: Path, name: str) -> dict[str, Any] | None:
    """服务当前的网页端登录链接（只有 DSH 有）；服务停下后没有。"""
    return _read_obj(login_file(control, name))


class Supervisor:
    def __init__(self, name: str, cmd: list[str], control: Path, poll_s: float = 1.0, stop_timeout_s: float = 20.0) -> None:
        self.name, self.cmd, self.control = name, cmd, control
        self.poll_s, self.stop_timeout_s = poll_s, stop_timeout_s
        self.child: subprocess.Popen[bytes] | None = None
        self.terminating = False

    def report(self, round_no: int, state: str, **extra: Any) -> None:
        try:
            write_json(service_file(self.control, self.name), {"service": self.name, "round": round_no, "state": state, "wall": time.time(), **extra})
        except OSError as exc:  # 控制目录不可写：只影响“新一轮演示”，服务照常运行
            print(f"rounds[{self.name}]: 写不了状态文件：{exc}", file=sys.stderr, flush=True)

    def forward(self, stream: Any, round_no: int) -> None:
        """转发服务的输出，顺便记下登录链接。"""
        out = sys.stdout.buffer
        for line in iter(stream.readline, b""):
            out.write(line)
            out.flush()
            if m := LOGIN_URL.search(line):
                with contextlib.suppress(OSError):
                    write_json(login_file(self.control, self.name), {"url": m.group(0).decode(), "round": round_no, "wall": time.time()})

    def stop_child(self) -> None:
        with contextlib.suppress(OSError):
            login_file(self.control, self.name).unlink(missing_ok=True)
        child = self.child
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(self.stop_timeout_s)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()

    def on_signal(self, signum: int, _frame: object) -> None:
        self.terminating = True
        self.stop_child()
        sys.exit(128 + signum)

    def wait_running(self) -> int:
        """等到 phase 为 running，返回当前轮次；等待期间一直报告 stopped（协调方据此判断可以归档了）。"""
        while True:
            st = read_round(self.control)
            if st["phase"] == RUNNING:
                return st["round"]
            self.report(st["round"], "stopped")
            time.sleep(self.poll_s)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.on_signal)
        signal.signal(signal.SIGINT, self.on_signal)
        while True:
            current = self.wait_running()
            # 单独的进程组：停的时候连子进程一起停
            self.child = subprocess.Popen(self.cmd, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            threading.Thread(target=self.forward, args=(self.child.stdout, current), daemon=True).start()
            self.report(current, RUNNING, pid=self.child.pid)
            print(f"rounds[{self.name}]: 第 {current} 轮，已启动（pid {self.child.pid}）", file=sys.stderr, flush=True)
            while True:
                rc = self.child.poll()
                if rc is not None:
                    self.report(current, "exited", code=rc)
                    return rc
                st = read_round(self.control)
                if st["phase"] == RESETTING and st["round"] > current:
                    print(f"rounds[{self.name}]: 开始第 {st['round']} 轮，停止服务", file=sys.stderr, flush=True)
                    self.stop_child()
                    self.report(st["round"], "stopped")
                    break
                time.sleep(self.poll_s)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        print(__doc__, file=sys.stderr)
        return 2
    name, cmd = args[0], args[2:]
    control = os.environ.get(CONTROL_DIR_ENV)
    if not control:
        os.execvp(cmd[0], cmd)
    path = Path(control)
    path.mkdir(parents=True, exist_ok=True)
    return Supervisor(name, cmd, path).run()


if __name__ == "__main__":
    sys.exit(main())
