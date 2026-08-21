#!/usr/bin/env python3
"""
CyberGym × moro-agent — コンテナ実行ラッパー (ホスト側)
---------------------------------------------------
gen_task 済みタスクディレクトリを moro-agent コンテナに /workspace として
マウントし、ヘッドレスで実行する。公式 Example Agents (codex 等) と同じ
構造: エージェントはコンテナ内、提出は submit.sh → サーバへ。

ネットワーク:
  - cybergym-internal が存在すればそれに接続 (プロキシ env も設定)
  - なければ既定ブリッジ (サーバはブリッジゲートウェイにバインドしておく)

使い方:
  # イメージビルド (初回)
  python3 scripts/cybergym_container.py build
  # 実行
  python3 scripts/cybergym_container.py run --task-dir ./tasks/arvo_10400 \
      --model glm-5.3 --budget-min 40

前提: .env にモデル API キー (GLM_API_KEY 等) が設定済み。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
IMAGE = "moro-agent:cybergym"
INTERNAL_NETWORK = "cybergym-internal"
PROXY_CONTAINER = "cybergym-proxy"
PROXY_PORT = 3128


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def log(msg: str) -> None:
    print(f"[cg-container {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cmd_build(args) -> int:
    log(f"イメージビルド: {IMAGE}")
    rc = subprocess.call([
        "docker", "build", "-t", IMAGE,
        "-f", str(FRAMEWORK_DIR / "scripts" / "cybergym" / "Dockerfile"),
        str(FRAMEWORK_DIR),
    ])
    return rc


def network_info() -> tuple[str, dict[str, str]]:
    """接続ネットワーク名と、プロキシ環境変数を返す。"""
    r = sh(["docker", "network", "inspect", INTERNAL_NETWORK])
    if r.returncode == 0:
        try:
            gw = json.loads(r.stdout)[0]["IPAM"]["Config"][0]["Gateway"]
        except Exception:  # noqa: BLE001
            gw = ""
        envs: dict[str, str] = {}
        # プロキシコンテナが動いていればその URL を設定
        pc = sh(["docker", "inspect", "-f", "{{.State.Status}}", PROXY_CONTAINER])
        if pc.returncode == 0 and pc.stdout.strip() == "running":
            proxy_url = f"http://{PROXY_CONTAINER}:{PROXY_PORT}"
            no_proxy = [x for x in (gw, "localhost", "127.0.0.1") if x]
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                envs[k] = proxy_url
            for k in ("NO_PROXY", "no_proxy"):
                envs[k] = ",".join(no_proxy)
        log(f"ネットワーク: {INTERNAL_NETWORK} (gateway={gw}, proxy={'有' if envs else '無'})")
        return INTERNAL_NETWORK, envs
    log(f"ネットワーク: {INTERNAL_NETWORK} が無いため既定ブリッジを使用")
    return "bridge", {}


def resolve_model_env(model: str) -> dict[str, str]:
    """.env + models.json からコンテナに渡すモデル env を解決。"""
    envs: dict[str, str] = {}
    env_file = FRAMEWORK_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            envs[k.strip()] = v.strip().strip('"').strip("'")
    try:
        data = json.loads((FRAMEWORK_DIR / "models.json").read_text())
        cfg = data.get("models", data).get(model, {})
        for k, v in (cfg.get("env") or {}).items():
            if isinstance(v, str) and v.startswith("$"):
                envs[k] = envs.get(v[1:], "")
            else:
                envs[k] = str(v)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] models.json 解決失敗: {e}")
    return envs


def cmd_run(args) -> int:
    task_dir = Path(args.task_dir).resolve()
    if not (task_dir / "submit.sh").exists():
        log(f"エラー: {task_dir}/submit.sh なし (gen_task 済み?)")
        return 2

    network, proxy_envs = network_info()
    model_envs = resolve_model_env(args.model)

    state_dir = task_dir / "_agent_state"
    state_dir.mkdir(exist_ok=True)

    name = f"moro-{task_dir.name}-{int(time.time())}"
    log_file = task_dir / "container.log"

    docker_args = [
        "docker", "run", "--rm",
        "--name", name,
        "--network", network,
        "-v", f"{task_dir}:/workspace",
        "-v", f"{state_dir}:/opt/moro-agent/state",
        "-w", "/workspace",
    ]
    # モデル env (シークレット含む — argv に載る点注意: ホストローカル運用前提)
    for k, v in model_envs.items():
        if v:
            docker_args += ["-e", f"{k}={v}"]
    for k, v in proxy_envs.items():
        docker_args += ["-e", f"{k}={v}"]
    docker_args += ["-e", "AGENT_ID="]
    # コンテナ内の claude が /workspace を信頼するように
    docker_args += ["-e", "IS_SANDBOX=1"]
    docker_args += [
        IMAGE,
        "--task-dir", "/workspace",
        "--model", args.model,
        "--budget-min", str(args.budget_min),
    ]
    if args.skip_extract:
        docker_args.append("--skip-extract")

    log(f"コンテナ起動: {name} (task={task_dir.name}, log={log_file})")
    with log_file.open("w") as lf:
        rc = subprocess.call(docker_args, stdout=lf, stderr=subprocess.STDOUT)
    log(f"終了 (exit={rc})")

    fa = task_dir / "final_answer.json"
    if fa.exists():
        log(f"final_answer.json あり: {fa.read_text()[:200]}")
        return 0
    return rc if rc != 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.set_defaults(func=cmd_build)
    r = sub.add_parser("run")
    r.add_argument("--task-dir", required=True)
    r.add_argument("--model", default="glm-5.3")
    r.add_argument("--budget-min", type=int, default=40)
    r.add_argument("--skip-extract", action="store_true")
    r.set_defaults(func=cmd_run)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
