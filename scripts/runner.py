#!/usr/bin/env python3
"""
ランタイムレジストリ — モデル → 起動ランタイム → 起動コマンド の単一の解決点
--------------------------------------------------------------------------
run.sh に埋め込まれていたモデル分岐 (claude-code / codex / aider) を、
差し替え可能な「ランタイムアダプタ」のレジストリとして切り出す。

各ランタイムは:
  - build(...)    : 起動コマンド (トークン列) を組み立てる。シークレットは
                    環境経由で渡すため argv に載せない。
  - prompt_mode   : プロンプトの渡し方。"tui" = tmux ペースト / "stdin" = 標準入力
を宣言する。新しいランタイム (例: dsh) はここに 1 エントリ足すだけ。

使い方 (CLI):
  python3 scripts/runner.py resolve glm-5.3            # ランタイム解決
  python3 scripts/runner.py command glm-5.3 agent-123  # 起動コマンド出力
"""
from __future__ import annotations

import json
import os
import shlex
import sys

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_PATH = os.path.join(FRAMEWORK_DIR, "models.json")
CODEX_PROFILES = os.path.join(FRAMEWORK_DIR, ".codex-profiles")


# ---------------------------------------------------------------------------
# ランタイムアダプタ
# ---------------------------------------------------------------------------
def build_claude_code(model: str, cfg: dict) -> list[str]:
    model_name = cfg.get("model_override", model)
    effort = os.environ.get("EFFORT", "high")
    return [
        "NODE_OPTIONS=--max-old-space-size=2560",
        f"claude --dangerously-skip-permissions --model {model_name} --effort {effort}",
    ]


def build_codex(model: str, cfg: dict) -> list[str]:
    c = cfg.get("codex", {})
    provider = c.get("provider_name", "custom")
    base_url = c.get("base_url", "")
    env_key = c.get("api_key_env", "")
    wire_api = c.get("wire_api", "responses")
    model_slug = c.get("model_slug", model)

    os.makedirs(CODEX_PROFILES, exist_ok=True)
    config_path = os.path.join(CODEX_PROFILES, f"{provider}.config.toml")
    catalog_path = os.path.join(CODEX_PROFILES, f"{provider}.json")

    lines = [
        f'model = "{model_slug}"',
        f'model_provider = "{provider}"',
        f'model_catalog_json = "{catalog_path}"',
        'model_reasoning_effort = "high"',
        'sandbox_permissions = ["network"]',
        f"[model_providers.{provider}]",
        f'name = "{provider}"',
        f'base_url = "{base_url}"',
        f'env_key = "{env_key}"',
        f'wire_api = "{wire_api}"',
        "stream_idle_timeout_ms = 7200000",
        "stream_max_retries = 5",
        "request_max_retries = 4",
    ]
    with open(config_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    # シークレット (SAKANA_API_KEY 等) は environment から継承 (env_key 参照)。
    # 対話モード (`codex -p`) をデフォルトにする (元 run.sh と同一挙動)。
    # 長文プロンプトの一括投入が要る場合は `codex exec ... -` (stdin) も選択可能 —
    # 将来 codex-exec ランタイムとして追加する余地を残す。
    return [
        f"CODEX_HOME={CODEX_PROFILES} codex -p {provider} -a never -s danger-full-access"
    ]


def build_aider(model: str, cfg: dict) -> list[str]:
    return [f"aider --yes-always --model {model}"]


def preflight_dsh(dsh_cfg: dict) -> None:
    """DSH ランタイムの前提を検証し、問題があれば即座に分かるメッセージで落とす。

    新規環境でよくある失敗 (不可解な DSH 内部エラー) を事前に防ぐ:
      1. dsh コマンドが無い
      2. ~/.dsh/settings.yaml が無い (DSH 初回起動未了)
      3. models.json の dsh.provider が settings.yaml に未定義
         → 書き換えると DSH 側の設定を壊すので、スワップ前に拒否
      4. provider の API キーが環境にも credentials にも無い
    """
    def fail(msg: str):
        print(f"[dsh-preflight] {msg}", file=sys.stderr)
        print("[dsh-preflight] DSH ランタイムの前提:\n"
              "  - dsh コマンドがインストール済み (npm i -g @deepseek-ai/dsh 等)\n"
              "  - dsh web を一度起動してプロバイダ (opencode-go 等) と API キーを設定\n"
              "    (鍵は ~/.dsh/.credentials.yaml に保存される)\n"
              "  - models.json の dsh.provider が ~/.dsh/settings.yaml の\n"
              "    llm-pi-ai.providers に定義済み", file=sys.stderr)
        sys.exit(1)

    import shutil as _shutil
    if not _shutil.which("dsh"):
        fail("dsh コマンドが見つかりません。")

    settings_path = os.path.expanduser("~/.dsh/settings.yaml")
    if not os.path.exists(settings_path):
        fail(f"{settings_path} が存在しません。DSH を一度起動して初期設定してください。")

    import yaml
    with open(settings_path) as f:
        settings = yaml.safe_load(f) or {}

    provider = dsh_cfg.get("provider")
    providers = (settings.get("llm-pi-ai") or {}).get("providers") or {}
    if provider not in providers:
        defined = ", ".join(sorted(providers)) or "(なし)"
        fail(f"provider '{provider}' が ~/.dsh/settings.yaml に定義されていません "
             f"(定義済み: {defined})。settings.yaml を書き換えると DSH 側を壊すため中断。")

    # API キー: 環境変数 or ~/.dsh/.credentials.yaml
    api_key_env = providers[provider].get("apiKeyEnv", "")
    cred_path = os.path.expanduser("~/.dsh/.credentials.yaml")
    has_key = bool(os.environ.get(api_key_env, "")) if api_key_env else False
    if not has_key and os.path.exists(cred_path):
        try:
            with open(cred_path) as f:
                creds = yaml.safe_load(f) or {}
            has_key = bool(creds.get(api_key_env))
        except Exception:
            pass
    if not has_key:
        fail(f"provider '{provider}' の API キーが見つかりません "
             f"(env ${api_key_env} にも ~/.dsh/.credentials.yaml にも無し)。")


def build_dsh(model: str, cfg: dict) -> list[str]:
    """DSH (DeepSeek Harness) をヘッドレス・ワンショットのサブエージェントとして起動。

    `dsh --profile headless "<task>"` はタスクを 1 つ解いて結果を出力して終了する。
    プロンプトは argv で渡す (prompt_mode="argv")。

    models.json の dsh ブロックに provider/model がある場合、**プロセス毎に独立した
    DSH_HOME (一時ディレクトリ) を作成**し、その中の settings.yaml だけを書き換える
    (DSH は $DSH_HOME をサポート)。旧方式 (~/.dsh/settings.yaml のスワップ&復元) は
    同時起動時に競合し、本番で TRANSPORT: Stream ended without finish_reason の
    集団死を引き起こした:
      - 監督がDSHを4秒差で連続起動 → 後続のスワップ/復元が先行エージェントの
        settings 読み込みと交錯 → プロバイダ解決が矛盾 → ストリーム異常
    DSH_HOME 分離なら共有状態を一切触らないため並行起動が完全に安全。
    一時ディレクトリのパスは .dsh-model-swap マーカーに記録し、run.sh の
    終了後処理で削除する (復元は不要 — 本体の ~/.dsh は最初から無傷)。

    実行前に preflight_dsh で前提 (dsh 導入・provider 定義・API キー) を検証する。
    """
    dsh_cfg = cfg.get("dsh", {})
    if dsh_cfg.get("provider") and dsh_cfg.get("model"):
        preflight_dsh(dsh_cfg)
        import shutil
        import tempfile
        import yaml

        real_home = os.path.expanduser("~/.dsh")
        tmp_home = tempfile.mkdtemp(prefix="dsh-moro-")
        # settings + credentials をコピー (profiles は起動時に解決される)
        for name in ("settings.yaml", ".credentials.yaml"):
            src = os.path.join(real_home, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp_home, name))

        # 一時 home 内でモデルを切り替え
        tmp_settings = os.path.join(tmp_home, "settings.yaml")
        with open(tmp_settings) as f:
            settings = yaml.safe_load(f) or {}
        settings["agent-default-model"] = {
            "provider": dsh_cfg["provider"],
            "model": dsh_cfg["model"],
        }
        with open(tmp_settings, "w") as f:
            yaml.dump(settings, f, default_flow_style=False)

        # 終了後の一時 home 削除を run.sh に依頼するマーカー
        marker = os.path.join(FRAMEWORK_DIR, ".dsh-model-swap")
        with open(marker, "w") as f:
            f.write(tmp_home)

        return [f'DSH_HOME="{tmp_home}"', "dsh", "--profile", "headless"]

    return ["dsh", "--profile", "headless"]


RUNTIMES = {
    "claude-code": {"build": build_claude_code, "prompt_mode": "tui"},
    "codex":       {"build": build_codex,       "prompt_mode": "tui"},
    "aider":       {"build": build_aider,       "prompt_mode": "tui"},
    "dsh":         {"build": build_dsh,         "prompt_mode": "argv"},
}


# ---------------------------------------------------------------------------
# 解決 API
# ---------------------------------------------------------------------------
def load_models() -> dict:
    with open(MODELS_PATH) as f:
        return json.load(f)


def resolve(model: str):
    """モデル名 → (runtime名, cfg) を解決する。"""
    models = load_models()
    cfg = models.get(model)
    if not cfg:
        raise KeyError(f"unknown model: {model}")
    runtime = cfg.get("runtime")
    if runtime not in RUNTIMES:
        raise KeyError(f"unknown runtime: {runtime} (model={model})")
    return runtime, cfg


def launch_command(model: str, agent_id: str, prompt: str = "") -> str:
    """起動コマンド (cd プレフィクス + AGENT_ID 付き) を返す。シークレットは含まない。

    prompt_mode が "argv" のランタイム (dsh) では prompt を argv に埋め込む。
    "tui" / "stdin" では prompt は外側 (run.sh) がペースト/標準入力で渡す。
    """
    runtime, cfg = resolve(model)
    parts = RUNTIMES[runtime]["build"](model, cfg)
    head = [f"cd {FRAMEWORK_DIR} &&", f"AGENT_ID={agent_id}"]
    tokens = head + parts
    if RUNTIMES[runtime]["prompt_mode"] == "argv" and prompt:
        tokens = tokens + [shlex.quote(prompt)]
    return " ".join(tokens)


def prompt_mode(model: str) -> str:
    runtime, _ = resolve(model)
    return RUNTIMES[runtime]["prompt_mode"]


def known_runtimes() -> list:
    return list(RUNTIMES)


def known_models() -> list:
    return list(load_models().keys())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(0)
    cmd = argv[0]
    if cmd == "resolve":
        runtime, _ = resolve(argv[1])
        print(runtime)
    elif cmd == "command":
        model, agent_id = argv[1], argv[2]
        prompt = argv[3] if len(argv) > 3 else ""
        print(launch_command(model, agent_id, prompt))
    elif cmd == "prompt-mode":
        print(prompt_mode(argv[1]))
    elif cmd == "models":
        print(" ".join(known_models()))
    elif cmd == "runtimes":
        print(" ".join(known_runtimes()))
    else:
        print(f"unknown subcommand: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
