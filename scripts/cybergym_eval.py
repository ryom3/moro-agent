#!/usr/bin/env python3
"""
CyberGym 評価ドライバ (複数タスク自動実行 + 集計)
------------------------------------------------
tasks-root 以下のタスクディレクトリ (gen_task 済み) を順に cybergym_run.py で
実行し、final_answer.json の有無と verify 結果を集計する。

使い方:
  # 実行
  python3 scripts/cybergym_eval.py run --tasks-root ./cybergym_tasks --budget-min 40
  # verify (CyberGymサーバ側で実施。agent_id は各タスク dir の logs/args.json)
  python3 scripts/cybergym_eval.py verify --tasks-root ./cybergym_tasks \
      --server http://127.0.0.1:8666 --pocdb ./server_poc/poc.db
  # 集計表示
  python3 scripts/cybergym_eval.py report --tasks-root ./cybergym_tasks
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent


def discover_tasks(tasks_root: Path) -> list[Path]:
    dirs = []
    for d in sorted(tasks_root.iterdir()):
        if d.is_dir() and (d / "submit.sh").exists():
            dirs.append(d)
    return dirs


def cmd_run(args) -> int:
    tasks = discover_tasks(Path(args.tasks_root))
    if not tasks:
        print(f"タスクなし: {args.tasks_root} (gen_task 済みのディレクトリが必要)")
        return 2
    selected = tasks
    if args.only:
        keys = set(args.only.split(","))
        selected = [t for t in tasks if any(k in t.name for k in keys)]
    print(f"実行タスク: {[t.name for t in selected]}")

    results = {}
    summary_path = Path(args.tasks_root) / "eval_summary.json"
    for i, task_dir in enumerate(selected, 1):
        print(f"\n=== [{i}/{len(selected)}] {task_dir.name} ===", flush=True)
        cmd = [
            sys.executable, str(FRAMEWORK_DIR / "scripts" / "cybergym_run.py"),
            "--task-dir", str(task_dir),
            "--framework-dir", str(FRAMEWORK_DIR),
            "--model", args.model,
            "--budget-min", str(args.budget_min),
        ]
        t0 = time.time()
        try:
            rc = subprocess.call(cmd)
        except KeyboardInterrupt:
            print("中断 — ここまでの結果を保存します")
            break
        results[task_dir.name] = {
            "exit_code": rc,
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "final_answer": (task_dir / "final_answer.json").exists(),
        }
        # 中間保存 (クラッシュ耐性)
        summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nサマリ: {summary_path}")
    return 0


def cmd_verify(args) -> int:
    import urllib.request

    tasks = discover_tasks(Path(args.tasks_root))
    server = args.server.rstrip("/")
    out = {}
    for task_dir in tasks:
        args_json = None
        for cand in (task_dir / "logs" / "args.json", task_dir / "args.json"):
            if cand.exists():
                args_json = cand
                break
        if not args_json:
            out[task_dir.name] = {"error": "args.json なし (gen_task 未実行?)"}
            continue
        agent_id = json.loads(args_json.read_text()).get("agent_id")
        if not agent_id:
            out[task_dir.name] = {"error": "agent_id 不明"}
            continue
        # サーバに poc.db への問い合わせはローカルファイルで行う (公式手順どおり別途
        # verify_agent_result.py を使うのが確実。ここでは agent_id の列挙のみ)
        out[task_dir.name] = {"agent_id": agent_id}
        print(f"{task_dir.name}: agent_id={agent_id}")
        print(f"  → python3 scripts/verify_agent_result.py --server {server} "
              f"--pocdb_path {args.pocdb} --agent_id {agent_id}")
    (Path(args.tasks_root) / "agent_ids.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args) -> int:
    tasks = discover_tasks(Path(args.tasks_root))
    rows = []
    for task_dir in tasks:
        fa = task_dir / "final_answer.json"
        sj = task_dir / "submit_final.json"
        row = {"task": task_dir.name, "final_answer": fa.exists()}
        if fa.exists():
            try:
                row["answer"] = json.loads(fa.read_text())
            except Exception as e:  # noqa: BLE001
                row["answer_error"] = str(e)
        if sj.exists():
            try:
                sub = json.loads(sj.read_text())
                row["submit_exit_code"] = sub.get("exit_code")
                row["poc_id"] = sub.get("poc_id")
            except Exception as e:  # noqa: BLE001
                row["submit_error"] = str(e)
        rows.append(row)
    solved = sum(1 for r in rows if r.get("final_answer"))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\nfinal_answer あり: {solved}/{len(rows)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--tasks-root", required=True)
    r.add_argument("--model", default="glm-5.3")
    r.add_argument("--budget-min", type=int, default=40)
    r.add_argument("--only", help="カンマ区切りのタスク名部分一致フィルタ")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify")
    v.add_argument("--tasks-root", required=True)
    v.add_argument("--server", required=True)
    v.add_argument("--pocdb", required=True)
    v.set_defaults(func=cmd_verify)

    p = sub.add_parser("report")
    p.add_argument("--tasks-root", required=True)
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
