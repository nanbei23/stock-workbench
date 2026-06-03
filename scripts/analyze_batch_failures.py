"""Analyze batch research failures from structured job logs.

The script is read-only. It reads batch_jobs, batch_job_items,
batch_job_item_steps, and batch_job_logs, then prints a compact failure report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH


FAILED_ITEM_STATUSES = {
    "failed",
    "timeout",
    "cancelled",
    "waiting_snapshot",
    "quota_paused",
    "guard_paused",
    "interrupted",
}
FAILED_STEP_STATUSES = {
    "failed",
    "timeout",
    "cancelled",
    "quota_paused",
    "guard_paused",
    "interrupted",
}
ERROR_TYPE_LABELS = {
    "quota_exhausted": "额度/限额",
    "network": "网络/连接",
    "rate_limit": "接口限流",
    "context_limit": "上下文过长",
    "snapshot_incomplete": "快照不完整",
    "json_parse": "模型 JSON 解析",
    "role_failure": "角色执行失败",
    "unknown": "未知",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row else {}


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _require_tables(conn: sqlite3.Connection) -> None:
    required = ["batch_jobs", "batch_job_items"]
    missing = [table for table in required if not _table_exists(conn, table)]
    if missing:
        raise RuntimeError(f"数据库缺少批量任务表: {', '.join(missing)}")


def _classify_error(status: str = "", error: str = "", error_type: str | None = None) -> str:
    if error_type:
        return str(error_type)
    text = f"{status} {error}".lower()
    if any(marker in text for marker in ("quota", "余额", "额度", "insufficient_quota")):
        return "quota_exhausted"
    if any(marker in text for marker in ("rate limit", "429", "限流", "too many requests")):
        return "rate_limit"
    if any(marker in text for marker in ("proxy", "connection", "timeout", "timed out", "network", "max retries", "断开")):
        return "network"
    if any(marker in text for marker in ("context", "token", "上下文", "maximum")):
        return "context_limit"
    if any(marker in text for marker in ("snapshot", "快照", "七层")):
        return "snapshot_incomplete"
    if any(marker in text for marker in ("json", "parse", "解析")):
        return "json_parse"
    if any(marker in text for marker in ("role", "角色")):
        return "role_failure"
    return "unknown"


def _latest_job(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM batch_jobs
        ORDER BY datetime(COALESCE(created_at, updated_at, '1970-01-01')) DESC, job_id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("没有找到 batch_jobs 记录")
    return _row_to_dict(row)


def _job_by_id(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM batch_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise RuntimeError(f"没有找到批量任务: {job_id}")
    return _row_to_dict(row)


def _status_counts(conn: sqlite3.Connection, job_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM batch_job_items
        WHERE job_id=?
        GROUP BY status
        ORDER BY count DESC, status ASC
        """,
        (job_id,),
    ).fetchall()
    return {str(row["status"] or "unknown"): int(row["count"] or 0) for row in rows}


def _load_failed_items(conn: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in FAILED_ITEM_STATUSES)
    rows = conn.execute(
        f"""
        SELECT *
        FROM batch_job_items
        WHERE job_id=?
          AND (status IN ({placeholders}) OR COALESCE(error, '') <> '')
        ORDER BY id ASC
        """,
        (job_id, *sorted(FAILED_ITEM_STATUSES)),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_failed_steps(conn: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "batch_job_item_steps"):
        return []
    placeholders = ",".join("?" for _ in FAILED_STEP_STATUSES)
    rows = conn.execute(
        f"""
        SELECT s.*, i.code, i.name
        FROM batch_job_item_steps s
        LEFT JOIN batch_job_items i ON i.id=s.item_id
        WHERE s.job_id=?
          AND (s.status IN ({placeholders}) OR COALESCE(s.error, '') <> '')
        ORDER BY s.item_id ASC, s.step_order ASC, s.id ASC
        """,
        (job_id, *sorted(FAILED_STEP_STATUSES)),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_logs(conn: sqlite3.Connection, job_id: str, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "batch_job_logs"):
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM batch_job_logs
        WHERE job_id=?
          AND (
            lower(level) IN ('error', 'warning', 'warn')
            OR lower(event) LIKE '%fail%'
            OR lower(event) LIKE '%error%'
            OR lower(event) LIKE '%quota%'
            OR lower(event) LIKE '%retry%'
          )
        ORDER BY datetime(COALESCE(created_at, '1970-01-01')) DESC, id DESC
        LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()
    logs = [_row_to_dict(row) for row in rows]
    logs.reverse()
    for log in logs:
        log["data_json"] = _loads(log.get("data_json"), {})
    return logs


def _group_items(items: list[dict[str, Any]], *, limit: int) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = _classify_error(item.get("status", ""), item.get("error", ""), item.get("error_type") or None)
        buckets[key].append(item)
    for key, values in sorted(buckets.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        grouped[key] = {
            "label": ERROR_TYPE_LABELS.get(key, key),
            "count": len(values),
            "examples": [
                {
                    "code": item.get("code") or "",
                    "name": item.get("name") or "",
                    "status": item.get("status") or "",
                    "error": item.get("error") or "",
                    "retry_count": int(item.get("retry_count") or 0),
                    "report_id": item.get("report_id"),
                    "started_at": item.get("started_at") or "",
                    "completed_at": item.get("completed_at") or "",
                }
                for item in values[:limit]
            ],
        }
    return grouped


def _group_steps(steps: list[dict[str, Any]], *, limit: int) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in steps:
        key = str(step.get("role_key") or "unknown")
        buckets[key].append(step)
    for key, values in sorted(buckets.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        role_name = values[0].get("role_name") or key
        grouped[key] = {
            "role_name": role_name,
            "count": len(values),
            "error_types": dict(
                Counter(
                    _classify_error(step.get("status", ""), step.get("error", ""), step.get("error_type") or None)
                    for step in values
                )
            ),
            "examples": [
                {
                    "code": step.get("code") or "",
                    "name": step.get("name") or "",
                    "role_name": step.get("role_name") or key,
                    "status": step.get("status") or "",
                    "error_type": _classify_error(step.get("status", ""), step.get("error", ""), step.get("error_type") or None),
                    "error": step.get("error") or "",
                    "duration_ms": step.get("duration_ms"),
                    "retry_count": int(step.get("retry_count") or 0),
                }
                for step in values[:limit]
            ],
        }
    return grouped


def analyze_batch_failures(
    db_path: Path = DB_PATH,
    *,
    job_id: str | None = None,
    example_limit: int = 10,
    log_limit: int = 80,
) -> dict[str, Any]:
    with _connect(Path(db_path)) as conn:
        _require_tables(conn)
        job = _job_by_id(conn, job_id) if job_id else _latest_job(conn)
        target_job_id = str(job["job_id"])
        status_counts = _status_counts(conn, target_job_id)
        failed_items = _load_failed_items(conn, target_job_id)
        failed_steps = _load_failed_steps(conn, target_job_id)
        logs = _load_logs(conn, target_job_id, limit=log_limit)

    total = sum(status_counts.values())
    failed_like = sum(
        count
        for status, count in status_counts.items()
        if status in FAILED_ITEM_STATUSES or status not in {"completed", "skipped"}
    )
    return {
        "db_path": str(Path(db_path).expanduser().resolve()),
        "job": {
            "job_id": target_job_id,
            "job_type": job.get("job_type") or "",
            "name": job.get("name") or "",
            "status": job.get("status") or "",
            "total_count": int(job.get("total_count") or 0),
            "completed_count": int(job.get("completed_count") or 0),
            "failed_count": int(job.get("failed_count") or 0),
            "skipped_count": int(job.get("skipped_count") or 0),
            "waiting_count": int(job.get("waiting_count") or 0),
            "created_at": job.get("created_at") or "",
            "started_at": job.get("started_at") or "",
            "completed_at": job.get("completed_at") or "",
            "error": job.get("error") or "",
        },
        "summary": {
            "total": total,
            "failed_like": failed_like,
            "failed_item_rows": len(failed_items),
            "failed_step_rows": len(failed_steps),
            "log_rows": len(logs),
        },
        "status_counts": status_counts,
        "failure_groups": _group_items(failed_items, limit=example_limit),
        "step_failure_groups": _group_steps(failed_steps, limit=example_limit),
        "logs": logs,
    }


def _fmt_table(rows: list[list[Any]], headers: list[str]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\n", " ").replace("|", "/")
    return text[:240]


def render_markdown(report: dict[str, Any]) -> str:
    job = report["job"]
    summary = report["summary"]
    lines = [
        "# 批量任务失败诊断",
        "",
        f"- 数据库: `{report['db_path']}`",
        f"- 任务: `{job['job_id']}` / {job['job_type']} / {job['status']}",
        f"- 名称: {job['name'] or '--'}",
        f"- 时间: {job['started_at'] or job['created_at'] or '--'} -> {job['completed_at'] or '--'}",
        f"- 统计: 总数 {summary['total']}，疑似失败/待处理 {summary['failed_like']}，失败 item {summary['failed_item_rows']}，失败 step {summary['failed_step_rows']}，相关日志 {summary['log_rows']}",
        "",
        "## 状态分布",
        "",
    ]
    status_rows = [[status, count] for status, count in report["status_counts"].items()]
    lines.append(_fmt_table(status_rows, ["状态", "数量"]) or "暂无状态数据")

    lines.extend(["", "## 按失败类型分组", ""])
    if report["failure_groups"]:
        for key, group in report["failure_groups"].items():
            lines.extend(
                [
                    f"### {key} / {group.get('label', key)} ({group['count']})",
                    "",
                    _fmt_table(
                        [
                            [
                                item["code"],
                                item["name"],
                                item["status"],
                                item["retry_count"],
                                item["error"],
                            ]
                            for item in group["examples"]
                        ],
                        ["代码", "名称", "状态", "重试", "错误"],
                    )
                    or "暂无样例",
                    "",
                ]
            )
    else:
        lines.append("未发现失败 item。")

    lines.extend(["", "## 按角色步骤分组", ""])
    if report["step_failure_groups"]:
        for role_key, group in report["step_failure_groups"].items():
            lines.extend(
                [
                    f"### {role_key} / {group.get('role_name', role_key)} ({group['count']})",
                    "",
                    f"- 错误类型: {json.dumps(group.get('error_types', {}), ensure_ascii=False)}",
                    "",
                    _fmt_table(
                        [
                            [
                                item["code"],
                                item["name"],
                                item["role_name"],
                                item["status"],
                                item["error_type"],
                                item["duration_ms"],
                                item["error"],
                            ]
                            for item in group["examples"]
                        ],
                        ["代码", "名称", "角色", "状态", "类型", "耗时ms", "错误"],
                    )
                    or "暂无样例",
                    "",
                ]
            )
    else:
        lines.append("未发现失败步骤。")

    lines.extend(["", "## 相关错误/告警日志", ""])
    if report["logs"]:
        lines.append(
            _fmt_table(
                [
                    [
                        log.get("created_at", ""),
                        log.get("level", ""),
                        log.get("event", ""),
                        log.get("item_id", ""),
                        log.get("message", ""),
                    ]
                    for log in report["logs"]
                ],
                ["时间", "级别", "事件", "item", "消息"],
            )
        )
    else:
        lines.append("未发现 error/warning/retry/quota 相关日志。")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze failures for the latest or selected batch research job.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--job-id", help="指定 batch job id；不传则读取最近一个批量任务")
    parser.add_argument("--examples", type=int, default=10, help="每组最多展示多少失败样例")
    parser.add_argument("--log-limit", type=int, default=80, help="最多读取多少条相关错误/告警日志")
    parser.add_argument("--json", action="store_true", help="输出 JSON；默认输出 Markdown")
    parser.add_argument("--output", type=Path, help="写入文件；不传则输出到 stdout")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = analyze_batch_failures(
            args.db,
            job_id=args.job_id,
            example_limit=max(1, args.examples),
            log_limit=max(1, args.log_limit),
        )
    except Exception as exc:
        print(f"批量任务失败诊断失败: {exc}", file=sys.stderr)
        return 1
    output = json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_markdown(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
