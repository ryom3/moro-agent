# MCP サーバ — ツール公開層 (共通契約)

`state.py` / `kb.py` の既存 CLI を、**単一の真実源のまま** MCP ツールとして公開する。
Claude Code / Codex / DSH (`dsh-mcp-client`) など、どのエージェントランタイムからも
同じツール群を同じ形で呼べる。

## セットアップ

```bash
python3 -m venv mcp/.venv
mcp/.venv/bin/pip install -r mcp/requirements.txt   # mcp==1.22.0 を固定
```

※ 重い KB 依存 (chromadb / torch / FlagEmbedding) は MCP venv に入れない。
   `kb_query` はデフォルトでシステムの `python3` を呼ぶ (`PENTEST_PYTHON` で上書き可)。

## 起動 (stdio)

```bash
mcp/.venv/bin/python mcp/server.py
```

## エージェントへの登録

**Claude Code**
```bash
claude mcp add pentest -- python3 /home/kali/Desktop/pentest-framework/mcp/.venv/bin/python \
    /home/kali/Desktop/pentest-framework/mcp/server.py
```

**Codex** (`~/.codex/config.toml`)
```toml
[mcp_servers.pentest]
command = "/home/kali/Desktop/pentest-framework/mcp/.venv/bin/python"
args = ["/home/kali/Desktop/pentest-framework/mcp/server.py"]
```

**DSH** — `dsh-mcp-client` 経由で接続 (環境変数 `PENTEST_PYTHON` を指定してもよい)。

## ツール一覧 (12種)

`state_show` / `state_host` / `state_tried` / `state_cred` / `state_finding` /
`state_log` / `state_alert` / `state_spray` / `state_relay` / `state_resume` /
`state_event` / `kb_query`

全ツールが `state/` (イベントストリーム含む) と KB を共通で読み書きするため、
ランタイムが何であっても状態・知識の一貫性が保たれる。

## Codex への登録

`mcp_setup.sh` は Codex にも同じサーバを登録する。Codex は `CODEX_HOME=.codex-profiles`
で起動するため、登録先は `.codex-profiles/config.toml` の `[mcp_servers.pentest]` になる。
`codex -p sakana` プロファイル起動でも MCP サーバは読み込まれる (検証済み)。

```
CODEX_HOME=.codex-profiles codex mcp add pentest --env PENTEST_PYTHON=python3 -- \
    mcp/.venv/bin/python mcp/server.py
```
## 自動セットアップ

`scripts/start.sh` が起動時に `scripts/mcp_setup.sh` を呼び、以下を冪等に実行する。
- `mcp/.venv` が無ければ作成 + `mcp` SDK を install
- `claude mcp add pentest -- ...` で Claude Code に登録 (未登録時のみ)

登録は Claude Code のグローバル/プロジェクト設定に書かれるため、一度登録すれば
監督AI も `run.sh` 経由のサブエージェントも自動で `kb_query` / `state_*` を利用できる。
