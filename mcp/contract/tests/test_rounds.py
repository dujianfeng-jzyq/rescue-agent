"""演示轮次的守护进程（rescue_contract.rounds）：新一轮时停下服务、等放行再启动；服务自己退出时带着退出码退出。"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rescue_contract.rounds import read_round, read_service, write_json

CHILD = [sys.executable, "-c", "import time; time.sleep(60)"]


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if v := fn():
            return v
        time.sleep(0.1)
    raise AssertionError("超时")


def start(control: Path, cmd=CHILD) -> subprocess.Popen:
    env = {**os.environ, "RESCUE_DEMO_CONTROL_DIR": str(control)}
    return subprocess.Popen([sys.executable, "-m", "rescue_contract.rounds", "svc", "--", *cmd], env=env)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # 僵尸进程也算退出
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def test_read_round_defaults_when_missing_or_broken(tmp_path):
    assert read_round(tmp_path) == {"round": 0, "phase": "running"}
    (tmp_path / "round.json").write_text("{坏")
    assert read_round(tmp_path) == {"round": 0, "phase": "running"}


def test_reset_stops_and_restarts_service(tmp_path):
    p = start(tmp_path)
    try:
        first = wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "running" and r)
        assert first["round"] == 0
        write_json(tmp_path / "round.json", {"round": 1, "phase": "resetting"})
        wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "stopped" and r["round"] == 1)
        assert not alive(first["pid"])
        time.sleep(1.5)
        assert read_service(tmp_path, "svc")["state"] == "stopped"  # 放行之前不启动
        write_json(tmp_path / "round.json", {"round": 1, "phase": "running"})
        second = wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "running" and r)
        assert second["round"] == 1 and second["pid"] != first["pid"] and alive(second["pid"])
        p.send_signal(signal.SIGTERM)
        p.wait(10)
        wait_for(lambda: not alive(second["pid"]))
    finally:
        p.kill()


def test_waits_while_resetting_at_start(tmp_path):
    write_json(tmp_path / "round.json", {"round": 2, "phase": "resetting"})
    p = start(tmp_path)
    try:
        wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "stopped")
        write_json(tmp_path / "round.json", {"round": 2, "phase": "running"})
        wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "running" and r["round"] == 2)
    finally:
        p.kill()


def test_child_exit_code_propagates(tmp_path):
    p = start(tmp_path, [sys.executable, "-c", "raise SystemExit(3)"])
    assert p.wait(10) == 3
    assert read_service(tmp_path, "svc")["state"] == "exited"


def test_without_control_dir_execs_directly():
    env = {k: v for k, v in os.environ.items() if k != "RESCUE_DEMO_CONTROL_DIR"}
    cmd = [sys.executable, "-m", "rescue_contract.rounds", "svc", "--", sys.executable, "-c", "import os; print(os.getpid())"]
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate(timeout=10)
    assert int(out) == p.pid  # 没有控制目录：exec 成服务本身，没有守护进程


def test_login_url_captured_and_dropped_on_stop(tmp_path):
    from rescue_contract.rounds import read_login

    cmd = [sys.executable, "-u", "-c", "import time; print('open https://h:8443/?token=abc_-1 now'); time.sleep(60)"]
    p = start(tmp_path, cmd)
    try:
        assert wait_for(lambda: read_login(tmp_path, "svc"))["url"] == "https://h:8443/?token=abc_-1"
        write_json(tmp_path / "round.json", {"round": 1, "phase": "resetting"})
        wait_for(lambda: (r := read_service(tmp_path, "svc")) and r["state"] == "stopped")
        assert read_login(tmp_path, "svc") is None  # 旧链接已失效
    finally:
        p.kill()
