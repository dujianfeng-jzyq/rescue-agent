"""事件复盘时间线：把事件库的条目整理成按时间排序的时间线，计算次数和响应时长。

输入：{"evidence": {"incident_id", "category", "panel", "as_of", "entries": [<事件库条目>...], "receipts": {<矿井回执编号>: <get_action_status 的结果，或 {"error": ...}>}}}
（证据插件从事件库读出后存成证据记录；receipts 是流程在复盘时向矿井查到的回执后续状态，可以没有）。
输出 result：rows（时间线）、counts（各类次数）、durations（响应时长，分钟；没有对应事件时为 null）、状态、未处理事项。
次数、时长都在这里算好，照抄，不要自己数或推算。时间都是矿井时间。
"""

from datetime import datetime

from _skill import main

VERSION = "timeline/1.0.0"
STATUS_NAMES = {"monitoring": "监测中", "handling": "处置中", "stable": "已稳定", "closed": "已关闭"}
CAUSE_NAMES = {"anomaly": "监测异常", "mine_alarm": "矿井报警", "followup_due": "跟踪计划到点", "degraded": "监听降级", "approval_timeout": "批准超时"}
NOTE_NAMES = {"assessment": "研判", "effect_review": "效果复评", "sealing_plan": "封闭方案", "unsealing": "启封评估"}
MINE_STATUS = {"accepted": "已收到", "executing": "执行中", "completed": "已完成", "failed": "失败", "rejected": "矿井拒绝"}
KIND_NAMES = {"notice": "通知", "dispatch": "调度", "treatment": "处置"}
ACTION_STATUS = {"issued": "已下发", "rejected": "被拒绝", "failed": "执行失败"}
ENTRY_KEYS = ("event_id", "at", "kind", "by", "data")


def minutes(a: str | None, b: str | None) -> int | None:
    if a is None or b is None:
        return None
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60)


def describe(e: dict) -> tuple[str, str, list[str]]:
    """（标签, 文字, 关联记录编号）"""
    k, d = e["kind"], e.get("data") or {}
    if k == "opened":
        return "开事件", d.get("summary", ""), []
    if k == "alert":
        return f"告警（{CAUSE_NAMES.get(d.get('cause'), d.get('cause', ''))}）", d.get("summary", ""), []
    if k == "recovered":
        return "恢复", d.get("summary", ""), []
    if k == "degraded":
        return "监听降级", d.get("summary", ""), []
    if k == "delivered":
        return "已投递值班会话", "", []
    if k == "delivery_failed":
        return "投递失败", f"第 {d.get('attempts', '?')} 次：{d.get('error', '')}", []
    if k == "acked":
        return "已确认告警", "", []
    if k == "status":
        return f"状态变为{STATUS_NAMES.get(d.get('status'), d.get('status', ''))}", d.get("reason", ""), []
    if k == "followup_added":
        return "登记跟踪计划", f"{d.get('due_at', '')} 复评：{d.get('expect', '')}", []
    if k == "followup_due":
        return "跟踪计划到点", "", []
    if k == "followup_done":
        return "跟踪计划完成", d.get("result", ""), []
    if k == "note":
        t = d.get("type")
        if t == "approval_pending":
            return f"等待人工确认 {d.get('tool', '')}", f"确认人 {d.get('confirmer') or '未指定'}；{d.get('timeout_min') or '—'} 分钟内无人处理将升级给{d.get('escalate_to') or '上一级'}", list(d.get("record_ids") or [])
        if t == "approval_decided":
            return f"人工确认结果 {d.get('tool', '')}", {"allowed-once": "已放行", "rejected": "已拒绝", "cancelled": "已取消", "unavailable": "无人应答"}.get(d.get("outcome"), str(d.get("outcome"))), []
        if t == "approval_escalated":
            return f"批准超时升级 {d.get('tool', '')}", f"已等 {d.get('waited_min', '?')} 分钟，已提醒{d.get('escalate_to') or '上一级'}", []
        if t == "action":
            st = ACTION_STATUS.get(d.get("status"), d.get("status", ""))
            problems = "；".join(d.get("problems") or [])
            return f"对外动作 {d.get('tool', '')}（{st}）", f"{d.get('action_id') or ''} {problems}".strip(), list(d.get("record_ids") or [])
        return NOTE_NAMES.get(t, "记录"), d.get("text", ""), list(d.get("record_ids") or [])
    return k, "", []


def run(data, _args):
    ev = data.get("evidence")
    if not isinstance(ev, dict) or not isinstance(ev.get("entries"), list) or not ev["entries"]:
        raise ValueError("输入缺少证据 evidence.entries（事件库条目）")
    entries = ev["entries"]
    for e in entries:
        if not isinstance(e, dict) or any(key not in e for key in ENTRY_KEYS):
            raise ValueError(f"事件库条目缺少字段（需要 {ENTRY_KEYS}）：{str(e)[:80]}")
    if entries[0]["kind"] != "opened":
        raise ValueError("事件的第一条应为 opened")

    rows, counts = [], dict.fromkeys(
        ("alerts", "assessments", "effect_reviews", "sealing_plans", "actions_issued", "actions_rejected", "actions_failed", "followups_added", "followups_done", "delivery_failures", "approvals_asked", "approvals_escalated"), 0
    )
    first: dict[str, str | None] = {"alert": None, "assessment": None, "action_issued": None, "action_any": None, "closed": None}
    status, handled, alerts = "monitoring", set(), []
    due_open: dict[str, bool] = {}
    for e in entries:
        k, d, at = e["kind"], e.get("data") or {}, e["at"]
        label, text, rids = describe(e)
        if k != "acked":
            rows.append({"at": at, "kind": k, "by": e["by"], "label": label, "text": text, "record_ids": rids})
        if k == "alert":
            counts["alerts"] += 1
            alerts.append(e["event_id"])
            first["alert"] = first["alert"] or at
        elif k in ("delivered", "acked"):
            handled.add(d.get("alert_id"))
        elif k == "delivery_failed":
            counts["delivery_failures"] += 1
        elif k == "status" and d.get("status") in STATUS_NAMES:
            status = d["status"]
            if status == "closed":
                first["closed"] = at
        elif k == "followup_added":
            counts["followups_added"] += 1
            due_open[d.get("followup_id")] = True
        elif k == "followup_done":
            counts["followups_done"] += 1
            due_open[d.get("followup_id")] = False
        elif k == "note":
            t = d.get("type")
            if t == "assessment":
                counts["assessments"] += 1
                first["assessment"] = first["assessment"] or at
            elif t == "effect_review":
                counts["effect_reviews"] += 1
            elif t == "sealing_plan":
                counts["sealing_plans"] += 1
            elif t == "approval_pending":
                counts["approvals_asked"] += 1
            elif t == "approval_escalated":
                counts["approvals_escalated"] += 1
            elif t == "action":
                first["action_any"] = first["action_any"] or at
                key = {"issued": "actions_issued", "rejected": "actions_rejected", "failed": "actions_failed"}.get(d.get("status"))
                if key:
                    counts[key] += 1
                if d.get("status") == "issued":
                    first["action_issued"] = first["action_issued"] or at
    # 对外动作在矿井的回执和后续状态（流程在复盘时向矿井查到的）
    receipts = ev.get("receipts") or {}
    receipt_rows, tally = [], {"completed": 0, "pending": 0, "bad": 0, "unknown": 0}
    for e in entries:
        d = e.get("data") or {}
        if e["kind"] != "note" or d.get("type") != "action":
            continue
        for mid in d.get("mine_receipt_ids") or []:
            r = receipts.get(mid)
            if not isinstance(r, dict) or "status" not in r:
                tally["unknown"] += 1
                receipt_rows.append({"action_id": d.get("action_id"), "tool": d.get("tool"), "mine_id": mid, "kind_name": "", "status": "unknown", "status_name": "查不到", "detail": (r or {}).get("error", "") if isinstance(r, dict) else "", "has_detail": bool((r or {}).get("error")) if isinstance(r, dict) else False, "updated_at": None})
                continue
            tally["completed" if r["status"] == "completed" else "pending" if r["status"] in ("accepted", "executing") else "bad"] += 1
            receipt_rows.append({"action_id": d.get("action_id"), "tool": d.get("tool"), "mine_id": mid, "kind_name": KIND_NAMES.get(r.get("kind"), r.get("kind", "")), "status": r["status"], "status_name": MINE_STATUS.get(r["status"], r["status"]), "detail": r.get("detail") or "", "has_detail": bool(r.get("detail")), "updated_at": r.get("updated_at")})
    # 状态转换只认 status 条目；稳定后再次告警回到监测中（与事件库的折叠规则一致）
    unhandled = [a for a in alerts if a not in handled]
    open_followups = sum(1 for v in due_open.values() if v)
    last_at = entries[-1]["at"]
    durations = {
        "alert_to_assessment_min": minutes(first["alert"], first["assessment"]),
        "alert_to_action_min": minutes(first["alert"], first["action_any"]),
        "alert_to_issued_min": minutes(first["alert"], first["action_issued"]),
        "opened_to_last_min": minutes(entries[0]["at"], last_at),
    }
    text = {k: (f"{v} 分钟" if v is not None else "无") for k, v in durations.items()}
    result = {
        "incident_id": ev.get("incident_id"),
        "panel": ev.get("panel"),
        "category": ev.get("category"),
        "opened_at": entries[0]["at"],
        "last_at": last_at,
        "status": status,
        "status_name": STATUS_NAMES[status],
        "closed_at": first["closed"],
        "rows": rows,
        "counts": counts,
        "counts_text": f"告警 {counts['alerts']} 条；研判 {counts['assessments']} 次；效果复评 {counts['effect_reviews']} 次；封闭方案 {counts['sealing_plans']} 次；"
        f"对外动作 已下发 {counts['actions_issued']}、被拒绝 {counts['actions_rejected']}、执行失败 {counts['actions_failed']}；"
        f"跟踪计划 登记 {counts['followups_added']}、完成 {counts['followups_done']}；"
        f"人工确认 等待 {counts['approvals_asked']} 次、其中超时升级 {counts['approvals_escalated']} 次",
        "durations": durations,
        "durations_text": text,
        "unhandled_alert_count": len(unhandled),
        "open_followup_count": open_followups,
        "has_unfinished": bool(unhandled or open_followups),
        "receipt_rows": receipt_rows,
        "has_receipts": bool(receipt_rows),
        "receipts_text": f"矿井回执 {len(receipt_rows)} 条：已完成 {tally['completed']}、执行中 {tally['pending']}、失败或被矿井拒绝 {tally['bad']}、查不到 {tally['unknown']}",
        "has_actions": bool(counts["actions_issued"] or counts["actions_rejected"] or counts["actions_failed"]),
    }
    return result, {"entry_kinds": sorted({e["kind"] for e in entries})}, []


if __name__ == "__main__":
    main(VERSION, run)
