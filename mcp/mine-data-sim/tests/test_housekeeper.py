"""演示轮次的归档服务（housekeeper.py）：等服务停下、把上一轮数据移到归档、放行；网页端令牌和配置档保留。"""

import time
from datetime import datetime

from rescue_contract.rounds import read_round, write_json
from rescue_mine_data_sim.housekeeper import Housekeeper, archive_round


def seed(data):
    for d in ("records", "incidents/sentinel", "ledger", "dsh-home/sessions/x", "dsh-home/profiles/rescue", "dsh-home/storages", "control", "caddy"):
        (data / d).mkdir(parents=True, exist_ok=True)
    (data / "records" / "R-1.json").write_text("{}")
    (data / "incidents" / "INC-1.jsonl").write_text("{}\n")
    (data / "incidents" / "sentinel" / "state.json").write_text("{}")
    (data / "ledger" / "actions-ledger.jsonl").write_text("{}\n")
    (data / "dsh-home" / ".credentials.yaml").write_text("secret")
    (data / "dsh-home" / "storages" / "workspace.json").write_text("{}")
    (data / "tokens.env").write_text("X=1")


def test_archive_moves_contents_and_keeps_mount_points(tmp_path):
    seed(tmp_path)
    dest = archive_round(tmp_path, 4, datetime(2026, 11, 19, 15, 30, 0))
    assert dest is not None and dest.name == "round-0004-20261119-153000"
    assert (dest / "records" / "R-1.json").exists()
    assert (dest / "incidents" / "sentinel" / "state.json").exists()
    assert (dest / "ledger" / "actions-ledger.jsonl").exists()
    assert (dest / "dsh-home" / "sessions" / "x").is_dir() and (dest / "dsh-home" / "storages" / "workspace.json").exists()
    # 挂载点（目录本身）还在、已清空
    for d in ("records", "incidents", "ledger"):
        assert (tmp_path / d).is_dir() and not any((tmp_path / d).iterdir())
    # 网页端令牌、配置档、令牌文件、证书不动
    assert (tmp_path / "dsh-home" / ".credentials.yaml").read_text() == "secret"
    assert (tmp_path / "dsh-home" / "profiles" / "rescue").is_dir()
    assert (tmp_path / "tokens.env").exists() and (tmp_path / "caddy").is_dir()
    assert archive_round(tmp_path, 5) is None  # 没有内容时不建空归档


def wait_job(hk, timeout=10):
    end = time.time() + timeout
    while hk.job["state"] in ("stopping", "archiving", "resetting_mine"):
        assert time.time() < end
        time.sleep(0.05)
    return hk.job


def test_round_waits_for_services_then_archives(tmp_path):
    seed(tmp_path)
    control = tmp_path / "control"
    hk = Housekeeper(control, tmp_path, ["dsh", "sentinel"], None, None, stop_timeout_s=5, poll_s=0.05)
    st = hk.start()
    assert st["round"] == 1 and st["phase"] == "resetting"
    time.sleep(0.3)
    assert hk.job["state"] == "stopping" and (tmp_path / "records" / "R-1.json").exists()  # 服务没停下之前不动数据
    for s in ("dsh", "sentinel"):
        write_json(control / f"service-{s}.json", {"service": s, "round": 1, "state": "stopped"})
    job = wait_job(hk)
    assert job["state"] == "done" and job["archive"].startswith("round-0000-")
    assert not (tmp_path / "records" / "R-1.json").exists()
    assert read_round(control) == {**read_round(control), "round": 1, "phase": "running"}


def test_round_gives_up_without_archiving_when_service_does_not_stop(tmp_path):
    seed(tmp_path)
    control = tmp_path / "control"
    hk = Housekeeper(control, tmp_path, ["dsh"], None, None, stop_timeout_s=0.3, poll_s=0.05)
    hk.start()
    job = wait_job(hk)
    assert job["state"] == "failed" and "dsh" in job["message"]
    assert (tmp_path / "records" / "R-1.json").exists()
    assert read_round(control)["phase"] == "running"  # 放行，服务继续


def test_restart_releases_stale_resetting(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    write_json(control / "round.json", {"round": 3, "phase": "resetting"})
    Housekeeper(control, tmp_path, ["dsh"], None, None)
    assert read_round(control)["phase"] == "running"


def test_console_round_proxy(tmp_path, monkeypatch):
    """模拟矿井的 /sim/demo/round 转给归档服务（带控制令牌）；没配置时报告不可用，控制台不显示按钮。"""
    import threading
    from http.server import ThreadingHTTPServer

    from rescue_contract.serving import build_app
    from rescue_mine_data_sim.housekeeper import make_handler
    from rescue_mine_data_sim.server import SimHolder, build_server
    from starlette.testclient import TestClient

    monkeypatch.setenv("RESCUE_SIM_CONTROL_TOKEN", "ctl")
    monkeypatch.delenv("RESCUE_DEMO_ROUNDS_URL", raising=False)
    auth = {"Authorization": "Bearer ctl"}
    c = TestClient(build_app(build_server(SimHolder("s2-slow-oxidation")), key="mine_data"))
    assert c.get("/sim/demo/round", headers=auth).json() == {"available": False}

    seed(tmp_path)
    hk = Housekeeper(tmp_path / "control", tmp_path, ["dsh"], None, None, stop_timeout_s=0.3, poll_s=0.05)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hk, "ctl"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("RESCUE_DEMO_ROUNDS_URL", f"http://127.0.0.1:{httpd.server_port}")
        c = TestClient(build_app(build_server(SimHolder("s2-slow-oxidation")), key="mine_data"))
        assert c.get("/sim/demo/round").status_code == 401  # 控制令牌
        st = c.get("/sim/demo/round", headers=auth).json()
        assert st["available"] and st["round"] == 0
        r = c.post("/sim/demo/round", headers=auth)
        assert r.status_code == 202 and r.json()["round"] == 1
        assert c.post("/sim/demo/round", headers=auth).status_code == 409  # 上一次没完成
        wait_job(hk)
    finally:
        httpd.shutdown()
