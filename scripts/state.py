#!/usr/bin/env python3
"""
Pentest Framework — 状態管理

使い方:
  python3 scripts/state.py host    --ip 172.16.50.55 --services "5985/winrm,445/smb"
  python3 scripts/state.py tried   --host 172.16.50.55 --method "winrm_svc_automation"
  python3 scripts/state.py cred    --user svc_portal --secret 'P0rt@l!Svc#2026' --source ".12 portal-secrets.env"
  python3 scripts/state.py finding --host 172.16.50.49 --step 3 --heading "CI pipeline poisoning" --narrative "..."
  python3 scripts/state.py log     --host 172.16.50.55 --action "winrm spray" --result fail --detail "Access denied"
  python3 scripts/state.py show    [--host IP]
  python3 scripts/state.py spray   --cred-id 3
  python3 scripts/state.py relay   --summary "..." --dead-ends "..." --next-steps "..."
  python3 scripts/state.py resume  [--agent AGENT_ID]
"""
import json, sys, os, argparse, fcntl, contextlib
from datetime import datetime, timezone

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")

def load_json(name):
    path = os.path.join(STATE_DIR, name)
    with open(path, 'r') as f:
        return json.load(f)


@contextlib.contextmanager
def locked_json(name, default):
    """load → (yield data) → save の read-modify-write 全体を flock で排他する。

    load_json と save_json を分けて呼ぶ旧来のパターンは、複数エージェントが
    同時に追記したとき lost update (書き込み欠落) を起こす。このコンテキストで
    読み・改変・書き込みを一括でロックし、cred/finding の id 重複も防ぐ。
    """
    path = os.path.join(STATE_DIR, name)
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(default, f)
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = default
        except Exception:
            data = default
        yield data
        f.seek(0)
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.truncate()
        fcntl.flock(f, fcntl.LOCK_UN)

def append_log(entry):
    path = os.path.join(STATE_DIR, "log.jsonl")
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entry.setdefault("agent", os.environ.get("AGENT_ID", "unknown"))
    with open(path, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    # 共通契約: 同一アクションを構造化イベントとしても記録する (加法的・非破壊)
    # ts/agent は emit_event が正規に付与するので、重複する timestamp/agent は除外。
    emit_event(entry.get("action", "log"), **{k: v for k, v in entry.items()
                                              if k not in ("action", "timestamp", "agent")})


def _append_jsonl(name, record):
    """JSONL 追記 (flock で排他)。共通のイベント/ログシンク。"""
    path = os.path.join(STATE_DIR, name)
    with open(path, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def emit_event(event_type, **fields):
    """構造化イベントを state/events.jsonl に追記。

    全エージェント・全ランタイムが共通で参照するイベントストリーム。
    {ts, agent, event, ...fields} の形 (None なフィールドは記録しない)。
    """
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": os.environ.get("AGENT_ID", "unknown"),
        "event": event_type,
    }
    rec.update({k: v for k, v in fields.items() if v is not None})
    _append_jsonl("events.jsonl", rec)
    return rec

def cmd_host(args):
    """ホストを登録/更新"""
    with locked_json("hosts.json", {"hosts": {}}) as hosts:
        h = hosts["hosts"].get(args.ip, {})
        h["state"] = args.state or h.get("state", "untouched")
        h["services"] = args.services.split(",") if args.services else h.get("services", [])
        h["tried"] = h.get("tried", [])
        h["notes"] = args.note or h.get("notes", "")
        hosts["hosts"][args.ip] = h
    append_log({"action": "host-register", "host": args.ip})
    print(f"HOST {args.ip} → {h['state']}")

def cmd_tried(args):
    """試行済み手法を記録"""
    with locked_json("hosts.json", {"hosts": {}}) as hosts:
        h = hosts["hosts"].get(args.host)
        if h is None:
            hosts["hosts"][args.host] = {"state": "in-progress", "services": [], "tried": [], "notes": ""}
            h = hosts["hosts"][args.host]
        if args.method not in h["tried"]:
            h["tried"].append(args.method)
    append_log({"action": "tried", "host": args.host, "method": args.method})
    print(f"TRIED: {args.method} on {args.host}")

def cmd_cred(args):
    """クレデンシャルを追加"""
    with locked_json("creds.json", {"credentials": []}) as creds:
        for existing in creds["credentials"]:
            if existing["user"] == args.user and existing["secret"] == args.secret:
                print(f"DUPLICATE — {args.user} already exists (id={existing['id']})")
                return
        # id はロック内で採番するため並行追加でも重複しない
        new_id = max([c.get("id", 0) for c in creds["credentials"]] + [0]) + 1
        entry = {
            "id": new_id,
            "user": args.user,
            "secret": args.secret,
            "secret_type": args.secret_type,
            "domain": args.domain,
            "source": args.source,
            "sprayed": [],
            "added_by": os.environ.get("AGENT_ID", "unknown"),
            "added_at": datetime.now(timezone.utc).isoformat()
        }
        creds["credentials"].append(entry)
    append_log({"action": "cred-add", "user": args.user, "source": args.source})
    print(f"CRED #{entry['id']}: {args.user}")

def cmd_finding(args):
    """レポート用の finding を追加"""
    with locked_json("findings.json", {"findings": []}) as findings:
        # リトライによる完全重複 (同 host/heading/narrative) を弾く
        # (Codex 等がツール呼び出しを retry して同一 finding を複数記録するのを防止)
        for existing in findings["findings"]:
            if (existing.get("host") == args.host
                    and existing.get("heading") == args.heading
                    and existing.get("narrative") == args.narrative):
                print(f"DUPLICATE — {args.heading} on {args.host} already recorded (id={existing['id']})")
                return
        new_id = max([f.get("id", 0) for f in findings["findings"]] + [0]) + 1
        entry = {
            "id": new_id,
            "host": args.host,
            "step": args.step,
            "heading": args.heading,
            "narrative": args.narrative,
            "commands": args.commands.split("|||") if args.commands else [],
            "output_summary": args.output or "",
            "screenshot": args.screenshot or "",
            "added_by": os.environ.get("AGENT_ID", "unknown"),
            "added_at": datetime.now(timezone.utc).isoformat()
        }
        findings["findings"].append(entry)
    append_log({"action": "finding-add", "host": args.host, "heading": args.heading})
    print(f"FINDING #{entry['id']}: {args.heading}")

def cmd_log(args):
    """アクションログ追記"""
    append_log({"host": args.host, "action": args.action, "result": args.result, "detail": args.detail or ""})
    print(f"LOG: {args.action} on {args.host} → {args.result}")

def cmd_show(args):
    """現在の状態を表示"""
    hosts = load_json("hosts.json")
    creds = load_json("creds.json")
    findings = load_json("findings.json")

    if args.host:
        h = hosts["hosts"].get(args.host, {})
        print(json.dumps(h, indent=2, ensure_ascii=False))
        return

    print(f"\n{'='*60}")
    print("HOSTS:")
    for ip, h in hosts["hosts"].items():
        state = h.get("state", "?")
        tried_count = len(h.get("tried", []))
        services = ", ".join(h.get("services", []))[:40]
        print(f"  {ip:20s} {state:12s} tried={tried_count:2d}  {services}")

    print(f"\nCREDS: {len(creds['credentials'])}")
    for c in creds["credentials"]:
        print(f"  #{c['id']:2d} {c['user']:20s} {c.get('secret_type','?'):10s} {c['source'][:30]}")

    print(f"\nFINDINGS: {len(findings['findings'])}")
    print(f"{'='*60}")

def cmd_spray(args):
    """指定 cred を全ホストに spray するコマンドを出力"""
    creds = load_json("creds.json")
    hosts = load_json("hosts.json")

    target_cred = None
    for c in creds["credentials"]:
        if c["id"] == args.cred_id:
            target_cred = c
            break
    if not target_cred:
        print(f"Cred #{args.cred_id} not found")
        return

    user = target_cred["user"]
    secret = target_cred["secret"]
    domain = target_cred.get("domain", "")
    domain_flag = f" -d {domain}" if domain else ""

    print(f"SPRAY: {user}")
    for ip in hosts["hosts"]:
        if ip in target_cred.get("sprayed", []):
            print(f"  {ip:20s} ✓ done")
        else:
            print(f"  {ip:20s} → TODO")
            if not args.dry_run:
                print(f"    nxc smb {ip} -u {user} -p '{secret}'{domain_flag}")
                print(f"    nxc winrm {ip} -u {user} -p '{secret}'{domain_flag}")
                print(f"    nxc ssh {ip} -u {user} -p '{secret}'")

def cmd_relay(args):
    """セッション状態を構造化して次のセッションに引き継ぐ"""
    agent_id = args.agent or os.environ.get("AGENT_ID", "unknown")
    hosts = load_json("hosts.json")
    creds = load_json("creds.json")
    findings = load_json("findings.json")

    relay = {
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_summary": args.summary,
        "dead_ends": args.dead_ends.split("|||") if args.dead_ends else [],
        "next_steps": args.next_steps.split("|||") if args.next_steps else [],
        "hosts": {ip: {"state": h.get("state"), "tried": h.get("tried", [])}
                  for ip, h in hosts.get("hosts", {}).items()},
        "cred_count": len(creds.get("credentials", [])),
        "finding_count": len(findings.get("findings", [])),
    }

    relay_path = os.path.join(STATE_DIR, f"relay_{agent_id}.json")
    with open(relay_path, 'w') as f:
        json.dump(relay, f, indent=2, ensure_ascii=False)

    append_log({"action": "relay", "summary": args.summary[:200]})
    print(f"RELAY → {relay_path}")
    print(f"  Dead ends: {len(relay['dead_ends'])}")
    print(f"  Next steps: {len(relay['next_steps'])}")

def cmd_resume(args):
    """前セッションの relay を読み込む"""
    agent_id = args.agent or os.environ.get("AGENT_ID", "unknown")
    relay_path = os.path.join(STATE_DIR, f"relay_{agent_id}.json")

    if not os.path.exists(relay_path):
        relay_files = [f for f in os.listdir(STATE_DIR) if f.startswith("relay_") and f.endswith(".json")]
        if relay_files:
            print(f"No relay for {agent_id}. Available:")
            for rf in relay_files:
                with open(os.path.join(STATE_DIR, rf)) as f:
                    r = json.load(f)
                print(f"  {rf}: {r.get('session_summary', '?')[:60]}")
        else:
            print("No relay files. Starting fresh.")
        return

    with open(relay_path) as f:
        relay = json.load(f)

    print(f"{'='*60}")
    print(f"RESUME: {agent_id} ({relay.get('created_at', '?')})")
    print(f"\n{relay.get('session_summary', '')}")

    if relay.get("dead_ends"):
        print("\nDEAD ENDS (do NOT repeat):")
        for d in relay["dead_ends"]:
            print(f"  ✗ {d}")

    if relay.get("next_steps"):
        print("\nNEXT STEPS:")
        for i, s in enumerate(relay["next_steps"], 1):
            print(f"  {i}. {s}")

    print(f"\nCreds: {relay.get('cred_count', 0)} / Findings: {relay.get('finding_count', 0)}")
    print(f"{'='*60}")
    append_log({"action": "resume", "from_relay": relay.get("created_at", "")})

def cmd_alert(args):
    """重要な発見を通知。監督 AI が即座に確認すべきイベント"""
    alert = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": os.environ.get("AGENT_ID", "unknown"),
        "host": args.host,
        "type": args.type,
        "detail": args.detail,
    }
    with locked_json("alerts.json", []) as alerts:
        alerts.append(alert)
    append_log({"action": "alert", "host": args.host, "type": args.type, "detail": args.detail})
    print(f"ALERT: [{args.type}] {args.host} — {args.detail}")


def cmd_event(args):
    """任意の構造化イベントを events.jsonl に記録する。

    例: エージェントのライフサイクルや、ツール呼び出しの要約。
      state.py event --type agent_start --model glm-5.3 --runtime claude-code
      state.py event --type agent_done  --summary "..."
    """
    rec = emit_event(args.type,
                     host=args.host or None,
                     detail=args.detail or None,
                     **dict(pair.split("=", 1) for pair in (args.field or [])))
    print(f"EVENT: [{args.type}] → events.jsonl")


def cmd_reset(args):
    """state と logs を初期化。前回のデータは archive/ に退避"""
    import shutil
    framework_dir = os.path.dirname(STATE_DIR)
    logs_dir = os.path.join(framework_dir, "logs")

    if not args.force:
        print("以下を初期化します:")
        print(f"  state/ (hosts, creds, findings, log)")
        print(f"  logs/")
        print(f"前回のデータは archive/ に退避されます")
        confirm = input("実行する? (y/N): ")
        if confirm.lower() != 'y':
            print("キャンセル")
            return

    # archive に退避
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = os.path.join(framework_dir, "archive", ts)
    os.makedirs(archive_dir, exist_ok=True)

    if os.path.exists(logs_dir) and os.listdir(logs_dir):
        shutil.copytree(logs_dir, os.path.join(archive_dir, "logs"))
    workspace_dir = os.path.join(framework_dir, "workspace")
    if os.path.exists(workspace_dir) and os.listdir(workspace_dir):
        shutil.copytree(workspace_dir, os.path.join(archive_dir, "workspace"))
    for f in ["hosts.json", "creds.json", "findings.json", "log.jsonl", "events.jsonl"]:
        src = os.path.join(STATE_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(archive_dir, f))
    # relay ファイルも退避
    for f in os.listdir(STATE_DIR):
        if f.startswith("relay_"):
            shutil.copy2(os.path.join(STATE_DIR, f), os.path.join(archive_dir, f))
            os.remove(os.path.join(STATE_DIR, f))

    print(f"Archived → archive/{ts}/")

    # 初期化
    with open(os.path.join(STATE_DIR, "hosts.json"), 'w') as f:
        json.dump({"hosts": {}}, f)
    with open(os.path.join(STATE_DIR, "creds.json"), 'w') as f:
        json.dump({"credentials": []}, f)
    with open(os.path.join(STATE_DIR, "findings.json"), 'w') as f:
        json.dump({"findings": []}, f)
    with open(os.path.join(STATE_DIR, "log.jsonl"), 'w') as f:
        pass
    with open(os.path.join(STATE_DIR, "events.jsonl"), 'w') as f:
        pass
    alerts_path = os.path.join(STATE_DIR, "alerts.json")
    with open(alerts_path, 'w') as f:
        json.dump([], f)

    # logs をクリア
    if os.path.exists(logs_dir):
        shutil.rmtree(logs_dir)
    os.makedirs(logs_dir, exist_ok=True)

    # workspace をクリア
    workspace_dir = os.path.join(framework_dir, "workspace")
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    os.makedirs(workspace_dir, exist_ok=True)

    print("Reset complete. scope.json は維持されています (手動で更新してください)")


def main():
    parser = argparse.ArgumentParser(description="Pentest State Manager")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("host")
    p.add_argument("--ip", required=True)
    p.add_argument("--services", default="")
    p.add_argument("--state", default="")
    p.add_argument("--note", default="")

    p = sub.add_parser("tried")
    p.add_argument("--host", required=True)
    p.add_argument("--method", required=True)

    p = sub.add_parser("cred")
    p.add_argument("--user", required=True)
    p.add_argument("--secret", required=True)
    p.add_argument("--secret-type", default="password", choices=["password", "hash", "key", "token", "cookie"])
    p.add_argument("--domain", default="")
    p.add_argument("--source", required=True)

    p = sub.add_parser("finding")
    p.add_argument("--host", required=True)
    p.add_argument("--step", type=int, required=True)
    p.add_argument("--heading", required=True)
    p.add_argument("--narrative", required=True)
    p.add_argument("--commands", default="")
    p.add_argument("--output", default="")
    p.add_argument("--screenshot", default="")

    p = sub.add_parser("log")
    p.add_argument("--host", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--result", required=True, choices=["success", "fail", "partial", "blocked"])
    p.add_argument("--detail", default="")

    p = sub.add_parser("show")
    p.add_argument("--host", default="")

    p = sub.add_parser("spray")
    p.add_argument("--cred-id", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("relay")
    p.add_argument("--agent", default=os.environ.get("AGENT_ID", "unknown"))
    p.add_argument("--summary", required=True)
    p.add_argument("--dead-ends", default="")
    p.add_argument("--next-steps", default="")

    p = sub.add_parser("resume")
    p.add_argument("--agent", default=os.environ.get("AGENT_ID", "unknown"))

    p = sub.add_parser("alert")
    p.add_argument("--host", required=True)
    p.add_argument(
        "--type",
        required=True,
        choices=["root", "cred", "pivot", "flag", "sensitive", "dead-end", "done"],
    )
    p.add_argument("--detail", required=True)

    p = sub.add_parser("reset")
    p.add_argument("--force", action="store_true", help="確認なしで実行")

    p = sub.add_parser("event", help="任意の構造化イベントを events.jsonl に記録")
    p.add_argument("--type", required=True, help="イベント種別 (agent_start, agent_done, tool_call ...)")
    p.add_argument("--host", default="")
    p.add_argument("--detail", default="")
    p.add_argument("--field", action="append", default=[],
                   help="追加フィールド key=value (反復可)")

    args = parser.parse_args()
    cmds = {
        "host": cmd_host, "tried": cmd_tried, "cred": cmd_cred,
        "finding": cmd_finding, "log": cmd_log, "show": cmd_show,
        "spray": cmd_spray, "relay": cmd_relay, "resume": cmd_resume,
        "alert": cmd_alert, "reset": cmd_reset, "event": cmd_event,
    }
    if args.cmd in cmds:
        cmds[args.cmd](args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
