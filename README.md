# Pentest Framework — AI マルチエージェント・バグバウンティ/VDP 基盤

監督 AI（オーケストレータ）が、差し替え可能なランタイム（Claude Code / Codex / aider / DSH）
のサブエージェントを並列起動し、共有状態を通して協調させて脆弱性を発見・報告する。

## アーキテクチャ

```
人間 ──start.sh──▶ 監督AI (Claude Code / 任意のランタイム)
                     │  run.sh でサブエージェント並列起動
        ┌────────────┼────────────┐
        ▼            ▼            ▼
    Claude Code    Codex(Fugu)   DSH       ← ランタイムレジストリ (差し替え可)
        └────────────┼────────────┘
                     ▼ 共通契約 (MCP + events.jsonl)
        ┌──────────────────────────────┐
        │  state/ (共有状態) + kb/ (RAG) │  ← 単一の真実源
        └──────────────────────────────┘
```

## ディレクトリ構造

```
pentest-framework/
├── README.md, CLAUDE.md / CLAUDE-bb.md   # 監督AIへの指示 (bb=バグバウンティモード)
├── handoff_templates.md                  # ハンドオフ/リレープロトコル (CHAP 準拠)
├── config                                # LAYOUT / TMUX_SESSION / MAX_AGENTS / EFFORT
├── models.json                           # モデル → ランタイムのマッピング
├── .env.example / .gitignore
│
├── scripts/                              # ★ コア (オーケストレーション)
│   ├── run.sh / start.sh                 #   監督・サブエージェント起動 (tmux)
│   ├── runner.py                         #   ランタイムレジストリ (モデル→起動コマンド解決)
│   ├── state.py                          #   共有状態管理 (host/tried/cred/finding/log/
│   │                                     #     spray/relay/resume/alert/event/reset)
│   ├── env_export.py                     #   models.json の env ブロック解決 (秘密は環境経由)
│   └── gen_report.py                     #   findings → Markdown レポート
│
├── kb/                                   #   ローカル RAG (BGE-M3 + BM25 + rerank)
├── mcp/                                  #   MCP サーバ (state.py/kb.py をツール公開)
├── tools/                                #   ペンテスト補助 (タスク単位のワンオフ)
│   ├── cors/                             #     CORS 解析 (analyzer/probe/mock/test)
│   ├── parse_scope.py                    #     program.html/csv → scope.json
│   ├── tokens.py                         #     トークン使用量の集計
│   └── verify_maps_key_browser.py        #     Google Maps キーの検証
├── docs/                                 #   設計ドキュメント (AS-IS/TO-BE 可視化 + 生成器)
├── data/                                 #   エンゲージメント入力 (program.csv/html 等)
│
├── state/                                #   共有状態 (全エージェントの接点) — ランタイム
│   ├── scope.json, hosts.json, creds.json, findings.json
│   ├── log.jsonl, events.jsonl           #   イベントストリーム (共通契約)
│   └── relay_*.json, alerts.json
├── logs/  workspace/  archive/           #   ランタイム (gitignore 済み)
└── mcp/.venv/                            #   MCP SDK 用の隔離環境 (gitignore 済み)
```

## 使い方

```bash
cp .env.example .env && vim .env        # API キー (サブスクなら不要)
vim state/scope.json                     # ターゲット・RoE
./scripts/start.sh                       # 監督AI 起動 (tmux 自動)
```

監督AIへの最初のプロンプト:
```
CLAUDE.md, handoff_templates.md, models.json, config, state/scope.json を読んで、
監督者として攻撃計画を立て、run.sh でサブエージェントを起動してください。
```

サブエージェント起動（監督AI が実行）:
```bash
./scripts/run.sh glm-5.3 "ドメインの API エンドポイントを調査して"        # モデル名で
./scripts/run.sh wave1 claude-haiku-4-5 "偵察して"                          # 明示ID + モデル
```

観察（実行とは分離された「窓」）:
```bash
./scripts/run.sh --tail [AGENT_ID]      # エージェントのログを tail -f
./scripts/run.sh --events               # 構造化イベントストリームを follow
./scripts/run.sh --monitor              # state 概要を watch
```

## 設計思想

- **CLAUDE.md が全て** — 人間 → 監督 → サブの指示系統をプロンプトで定義
- **state/ が唯一の接点** — エージェント間通信は JSON 経由。tried 配列で重複排除
- **ランタイム差し替え** — `scripts/runner.py` のレジストリでモデル→ランタイムを解決。
  Claude Code / Codex / aider を差し替え、新ランタイム (dsh 等) は 1 エントリ追加で拡張
- **dsh ランタイム** — `dsh --profile headless "タスク"` で DeepSeek Harness 自身を
  ワンショットのサブエージェントとして起動 (プロンプトは argv で渡す)。モデル・認証は
  DSH 設定 ($DSH_HOME) に従う。models.json の `dsh-default` が対応。
- **共通契約** — `events.jsonl`(構造化イベント) + MCP(`mcp/server.py`) で
  どのランタイムからも同じツール・状態を同じ形で呼べる
- **実行と観察の分離** — tmux は観察窓 (`--tail`/`--events`)。ログ・イベントが真実の源

## セキュリティ

- API キーは argv でなく**環境変数経由で継承**（`ps` への平文露出を防止）
- 共有状態は `state.py` の `locked_json` で read-modify-write を flock（並行時の欠落・ID重複を防止）
- RoE/VDP ルール（1 req/sec, DoS禁止, PII即停止等）は CLAUDE.md と scope.json で規定

## ランタイムレジストリ

`models.json` にモデル名を追加し、`scripts/runner.py` の `RUNTIMES` にエントリを足すだけで
新ランタイムを追加できる。

| モデル例 | runtime | ループ | モデル/認証 | prompt 渡し |
|---|---|---|---|---|
| claude-*, glm-* | `claude-code` | Anthropic 製 | models.json + .env | tui (tmuxペースト) |
| fugu-ultra | `codex` | OpenAI 製 | Sakana API (api.sakana.ai) | tui |
| dsh-default | `dsh` | DSH 製 (`dsh-agent-loop`) | DSH 設定 ($DSH_HOME) | argv (コマンド埋込) |
| (任意) | `aider` | Aider 製 | models.json | tui |

## MCP ツール (共通契約)

`mcp/server.py` が `state.py` / `kb.py` を MCP ツールとして公開。Claude Code と Codex の
両方から同じツール面を自然言語で利用できる（`start.sh` が `mcp_setup.sh` で自動登録）。

| 分類 | ツール |
|---|---|
| 検索 | `kb_query` |
| 状態 | `state_show` / `state_host` / `state_tried` / `state_cred` / `state_finding` |
| 記録 | `state_log` / `state_alert` / `state_event` |
| 連携 | `state_spray` / `state_relay` / `state_resume` |

**実地確認済み**: Claude Code / Codex の両方で `kb_query`（KB検索）、`state_finding`、
`state_alert`、`state_show` が「自然言語 → ツール発火 → 副作用」を確認。残りも MCP
プロトコルレベルで全ツール動作確認済み。

## テスト

```bash
python3 -m pytest          # tools/ (CORS 分析 16 テスト)
```
