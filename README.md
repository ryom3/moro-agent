# moro-agent — AI マルチエージェント・バグバウンティ/VDP 基盤

監督 AI（オーケストレータ）が、差し替え可能なランタイム（Claude Code / Codex / aider / DSH）
のサブエージェントを並列起動し、共有状態を通して協調させて脆弱性を発見・報告する。

**検証層 (LLM-as-a-Verifier) を内蔵**し、陰性結果の審査と重要判断の品質を構造的に保証する。

---

## アーキテクチャ

<img src="docs/arch-flow.png" alt="現在のアーキテクチャ全体フロー" width="100%">

> 図の生成: `python3 docs/gen_design.py --svg-dir docs`（SVG + PNG + `DESIGN.html` を再生成。

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
                     ▼
         ┌──────────────────────────────┐
         │  verify_negative / verify_judgment  │  ← LLM-as-a-Verifier
         │  (偽陰性の検出・判断品質の担保)       │    (qwen3.7-max @ Opencode Go)
         └──────────────────────────────┘
```

### 設計思想（3つの大原則）

<img src="docs/principles.png" alt="設計思想（3つの大原則）" width="100%">

### コンポーネント構成

<img src="docs/components.png" alt="コンポーネント構成（ランタイム層 + ツール層 + 検証層）" width="100%">

---

## ディレクトリ構造

```
moro-agent/
├── CLAUDE.md / CLAUDE-bb.md / CLAUDE-re.md   # 監督AI指示 (pentest/bb/RE)
├── handoff_templates.md                      # ハンドオフ/リレープロトコル (CHAP)
├── config                                    # MAX_AGENTS / EFFORT / RELAY_CONTEXT_TOKENS
├── models.json                               # モデル → ランタイムのマッピング
│
├── scripts/                                  # ★ コア (オーケストレーション)
│   ├── run.sh / start.sh                     #   監督・サブエージェント起動 (tmux)
│   ├── runner.py                             #   ランタイムレジストリ
│   ├── state.py                              #   共有状態管理 (flock排他)
│   ├── env_export.py                         #   env解決 (秘密は環境経由)
│   ├── wait_alert.sh                         #   イベント駆動待機 (~1秒検知)
│   ├── check_updates.sh                      #   起動時更新チェック
│   ├── sync_skills.py                        #   攻撃系スキル公開
│   ├── fetch_bbd.py                          #   Bug Bounty Disclosures 取り込み
│   └── gen_report.py                         #   findings → Markdown
│
├── kb/                                       #   ローカル RAG (BGE-M3 + BM25 + rerank)
│   ├── ad-playbook/                          #     AD攻撃プレイブック (自作)
│   └── bbd-disclosures/                      #     4,083件の開示レポート (fetch_bbd.py)
├── mcp/                                      #   MCP サーバ (20ツール)
├── tools/                                    #   補助ツール
│   ├── re/                                   #     RE (r2_recon.py + Ghidra設計)
│   ├── cors/                                 #     CORS 解析
│   └── ...                                   #     parse_scope / tokens 等
├── docs/                                     #   設計ドキュメント + 図生成器
├── data/                                     #   エンゲージメント入力
├── third_party/Anthropic-Cybersecurity-Skills/  # スキル submodule (817個)
├── .claude/skills/                           #   攻撃系 257 スキル (自動公開)
│
├── state/                                    #   共有状態 (全エージェントの接点)
│   ├── scope.json, hosts.json, creds.json, findings.json
│   ├── log.jsonl, events.jsonl               #   イベントストリーム (共通契約)
│   └── relay_*.json, alerts.json
├── logs/  workspace/  archive/               #   ランタイム (gitignore)
└── mcp/.venv/                                #   MCP SDK 隔離環境 (gitignore)
```

---

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

サブエージェント起動:
```bash
./scripts/run.sh glm-5.3 "API エンドポイントを調査して"           # モデル名で
./scripts/run.sh wave1 claude-haiku-4-5 "偵察して"                  # 明示ID + モデル
./scripts/run.sh dsh-deepseek-flash "別の視点で再検証して"          # DSH 経由 deepseek
./scripts/run.sh dsh-ox-alpha-free "第三の意見を聞く"               # DSH 経由 Ox Alpha
```

**DSH ランタイム (dsh-* モデル) を使う場合の前提** — DSH は moro-agent の `.env`
ではなく自分の設定 (`~/.dsh/settings.yaml` + `~/.dsh/.credentials.yaml`) を使う:

1. `npm i -g @deepseek-ai/dsh` でインストール
2. `dsh web` を一度起動 → Models ページでプロバイダ (opencode-go 等) と API キーを設定
3. `models.json` の `dsh.provider` と同じ名前が `~/.dsh/settings.yaml` の
   `llm-pi-ai.providers` に定義されていること

`runner.py` が起動前にこの前提を検証する。新規環境で dsh 未導入・プロバイダ未定義・
API キー不在のいずれかがあれば、**原因と対処法を出して即座に停止**する
(不可解な DSH 内部エラーを放置しない)。モデル指定は settings.yaml の
一時スワップで実現し、エージェント終了時に自動復元。

観察（実行と分離された「窓」）:
```bash
./scripts/run.sh --tail [AGENT_ID]      # エージェントのログを tail -f
./scripts/run.sh --events               # 構造化イベントストリームを follow
./scripts/run.sh --monitor              # state 概要を watch
```

---

## 検証層 (LLM-as-a-Verifier)

回顧 (HTB Hard 失敗) の敗因「偽陰性の受理」「狭い問い」を構造的に防ぐ。
判断案を 2〜3 個書き出し、logprobs 期待値で fine-grained スコアリングして最良を選ぶ。

| ツール | 用途 | 審査基準 |
|---|---|---|
| `verify_negative` | 陰性結果の審査 (偽陰性の検出) | 監視点到達 / 挑発能力 / 陰性の再解釈 |
| `verify_judgment` | 監督判断の審査 | 全攻撃原理の列挙 / 推測より窃取・リレー / 可逆改変の活用 |

**バックエンド**: `qwen3.7-max` (Opencode Go, logprobs対応, サブスク)
モデル変更: `VERIFIER_MODEL` env で差し替え可

```bash
# 実動例
# サブエージェントが「consumerは存在しない」と報告
# → verify_negative で審査:
#   Candidate #1 (陰性受理): 0.086  ← 却下
#   Candidate #2 (設計を疑う): 0.982 ★ ← 正しく選択
```

---

## 設計思想

| 原則 | 実装 |
|---|---|
| **CLAUDE.md が全て** | 指示系統をプロンプトで定義。モデル変更より指示改善を優先 (Sultan 2026) |
| **state/ が唯一の接点** | エージェント間通信は JSON 経由。tried 配列で重複排除 |
| **ランタイム差し替え** | `runner.py` のレジストリ。新ランタイムは 1 エントリ追加で拡張 |
| **共通契約** | `events.jsonl` + MCP で全ランタイムが同じツールを呼ぶ |
| **実行と観察の分離** | tmux は観察窓 (`--tail`/`--events`)。ログ・イベントが真実の源 |
| **イベント駆動待機** | `wait_alert.sh` が alert/events を約1秒で検知 (旧: 5分定期) |
| **Context Relay 2段しきい値** | ソフト 30k (チェックポイント relay) / ハード 60k (即 relay) |
| **検証層 (Verifier)** | 陰性結果・重要判断を logprobs 期待値で審査 (LLM-as-a-Verifier) |

## ランタイムレジストリ

| モデル例 | runtime | ループ | prompt 渡し |
|---|---|---|---|
| claude-*, glm-* | `claude-code` | Anthropic 製 | tui (tmuxペースト) |
| fugu-ultra | `codex` | OpenAI 製 | tui |
| dsh-default | `dsh` | DSH 製 | argv (コマンド埋込) |
| (任意) | `aider` | Aider 製 | tui |

## MCP ツール (共通契約, 20ツール)

`mcp/server.py` が全ツールを公開。Claude Code / Codex / DSH から自然言語で呼べる。

| 分類 | ツール |
|---|---|
| 検索 | `kb_query` |
| 状態 | `state_show` / `state_host` / `state_tried` / `state_cred` / `state_finding` / `state_log` / `state_alert` / `state_event` |
| 連携 | `state_spray` / `state_relay` / `state_resume` |
| RE | `re_checksec` / `re_funcs` / `re_strings` / `re_imports` / `re_disasm` / `re_xrefs` |
| **検証** | `verify_negative` / `verify_judgment` |

## ナレッジベース (KB)

ローカル RAG: BGE-M3 dense + BM25 sparse → RRF 融合 → bge-reranker-v2-m3。
日英混在・技術トークン (`SUID` / `CVE-2021-4034`) に強い独自トークナイザ。

| ソース | 件数 | タグ |
|---|---|---|
| OSCP/OSAI 教材 (Notion + PDF) | 7,299 chunks | `notion` / `offsec` |
| 技術書 (cs-books) | 8,777 chunks | `cs-books` |
| **AD攻撃プレイブック** (自作) | 6 chunks | `ad-attack` |
| **Bug Bounty Disclosures** (4,083件の開示レポート、賞金付き) | ~4,083 chunks | `bbd` |

**Bug Bounty Disclosures**: 11,304 件の公開脆弱性開示 (HackerOne 9,991 / Bugcrowd 804 /
Code4rena 410 / Immunefi 92) から、賞金あり or High/Critical の 4,083 件を取り込み。
攻撃手順本文 (vulnerability_information) も含む (fetch_bbd.py --deep)。

```bash
python3 scripts/fetch_bbd.py              # カタログ取得 → md 変換
python3 scripts/fetch_bbd.py --deep       # H1 本文取得 (攻撃手順込み)
python3.13 kb/kb.py ingest --source kb/bbd-disclosures --tag bbd
```

## セキュリティスキルライブラリ

817 スキル (Apache-2.0) から**攻撃系 257 個**を `.claude/skills/` に自動公開。
description 総量 7,093 token (200k の 3.5%)。

```bash
python3 scripts/sync_skills.py            # 攻撃系 257 個を symlink 公開
python3 scripts/sync_skills.py --all      # 全 817 個
```

- 引用元: [Anthropic-Cybersecurity-Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills)
- 絞り込み: red-teaming / pen-test / web-app / api / malware-analysis (RE) / network / identity

## セキュリティ

- API キーは argv でなく**環境変数経由** (`ps` への露出を防止)
- 共有状態は `locked_json` で read-modify-write を flock
- RoE/VDP ルールは CLAUDE.md と scope.json で規定
- 検証層が陰性結果の審査を構造化 (偽陰性の検出)

---

## 設計の根拠（研究・論文）

<img src="docs/rationale.png" alt="設計の根拠 — 研究・論文から設計判断への反映" width="100%">

### マルチエージェント設計

| 研究 | 知見 | 反映 |
|---|---|---|
| **Sultan 2026** | scaffold > model (0 → 49 タスク) | CLAUDE.md の指示品質を最優先 |
| **CHAP (NDSS 2026)** | Context Relay (30k 超過でハンドオフ) | `relay` + 2段しきい値 (30k/60k) |
| **NeurIPS 2026** | 同一モデル → バイト同一出力 | モデル多様性ルール (異なるモデルを混ぜる) |
| **XBOW 2026** | "route the right model to the right task" | 偵察は安く / 攻撃は強く |
| **LLM-as-a-Verifier** (arXiv:2607.05391) | Self-Verification: Pass@1 78.7% → 88.0% (Best-of-5) | `verify_negative` / `verify_judgment` |

### RAG（ナレッジベース）

| 技術 | 内容 | 反映 |
|---|---|---|
| **BGE-M3** (arXiv:2402.03216) | 多言語 dense embedding | `kb/` の embedding モデル |
| **BM25** | スパース検索 ("SUID" 等に強い) | ハイブリッド検索 |
| **RRF** (SIGIR 2009) | dense/sparse 統合 | 検索パイプライン |
| **bge-reranker-v2-m3** | リランキング (nDCG +10-20pt) | リランキング層 |

## テスト

```bash
python3 -m pytest          # tools/ (CORS 分析 16 テスト)
```

---

## モード切替

| モード | 指示ファイル | 用途 |
|---|---|---|
| **pentest** (default) | `CLAUDE.md` | ペネトレーションテスト |
| **bug bounty** | `CLAUDE-bb.md` (`start.sh --bb`) | VDP / バグバウンティ |
| **RE** | `CLAUDE-re.md` | リバースエンジニアリング |
| **CyberGym** | `CLAUDE-cybergym.md` (feature/cybergym branch) | ベンチマーク評価 |
