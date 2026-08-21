# CyberGym 評価 — moro-agent 全自動ランナー

CyberGym (https://github.com/sunblaze-ucb/cybergym) タスクを moro-agent で
ヘッドレス全自動解決するための追加コンポーネント。

## 構成

| ファイル | 役割 |
|---|---|
| `CLAUDE-cybergym.md` | 監督AIへの指示 (CyberGym 特化版)。run.sh/state.py はそのまま使い、脆弱性解析の役割規定と提出プロトコル (final-submission 採点)・報酬ハッキング禁止を定義 |
| `scripts/cybergym_run.py` | 単一タスクランナー。gen_task 済みタスクディレクトリで repo 展開 (.git 除去) → 監督AI (claude -p headless) 起動 → バジェット監視 → final_answer.json 待ち |
| `scripts/cybergym_eval.py` | 複数タスクの逐次実行 / agent_id 列挙 (verify用) / 結果集計 |
| `scripts/cybergym/Dockerfile` | エージェントコンテナイメージ (Claude Code CLI + tmux + clang/gdb 等の解析チェーン)。公式 Example Agents と同じ方式 |
| `scripts/cybergym/entrypoint.sh` | コンテナ内エントリポイント: tmux サーバ起動 → cybergym_run.py ヘッドレス実行 |
| `scripts/cybergym_container.py` | ホスト側コンテナ実行ラッパー: イメージビルド / `cybergym-internal` ネットワークとプロキシ env 自動解決 / タスクを `/workspace` にマウント |
| `.dockerignore` | イメージにシークレット (.env) を含めないための除外設定 |

## コンテナ化 (公式方式)

CyberGym はエージェントを **Docker コンテナで隔離して実行** する前提
(`cybergym-internal` ネットワーク + ドメイン許可リストプロキシ)。
moro-agent は tmux を使うため、entrypoint.sh がコンテナ内で tmux サーバを
事前に起動してからヘッドレス監督AIを走らせる。

```bash
# 1. イメージビルド (初回のみ)
python3 scripts/cybergym_container.py build

# 2. プロキシ許可リストに GLM API を追加 (cybergym リポジトリの
#    src/cybergym/firewall/default_allowlist.txt に api.z.ai を追記)
python3 -m cybergym.firewall start   # cybergym 側

# 3. コンテナ実行
python3 scripts/cybergym_container.py run --task-dir <tasks>/arvo_10400 --budget-min 40
```

ネットワークは自動判定: `cybergym-internal` があれば接続して
`cybergym-proxy:3128` のプロキシ env を設定、なければ既定ブリッジ
(サーバをブリッジゲートウェイにバインドしておくこと)。
コンテナ内の共有状態 (events.jsonl) はタスクの `_agent_state/` に書かれる。


## 事前準備 (CyberGym 側)

```bash
# 1. サーバ起動 (cybergym リポジトリにて, ローカルのみ)
python3 -m cybergym.server --host <docker network gateway> --port 8666 \
    --mask_map_path mask_map.json --log_dir ./server_poc --db_path ./server_poc/poc.db

# 2. タスク生成 (エージェントに渡す形式に変換)
python3 -m cybergym.task.gen_task --task-id arvo:10400 --out-dir ./tasks/arvo_10400 \
    --data-dir ./cybergym_data/data --server "http://$HOST:8666" \
    --mask-map mask_map.json --difficulty level1
```

## 実行 (moro-agent 側)

```bash
# 単一タスク
python3 scripts/cybergym_run.py --task-dir <tasks>/arvo_10400 --model glm-5.3 --budget-min 40

# サブセット一括
python3 scripts/cybergym_eval.py run --tasks-root <tasks> --budget-min 40

# verify (agent_id は各タスク logs/args.json)
python3 scripts/cybergym_eval.py verify --tasks-root <tasks> --server http://$HOST:8666 --pocdb ./server_poc/poc.db
python3 scripts/cybergym_eval.py report --tasks-root <tasks>
```

## 設計メモ

- **採点方式**: FAQ Q3 に従い final-submission (最終回答1つ指名)。
  `final_answer.json` に `{poc_path, reason}` を書かせる
- **報酬ハッキング対策**: repo 展開時に `.git` を全層除去、上流検索禁止を指示に明記
- **監視契約**: 完了は `state/events.jsonl` の `task_done` イベント or
  `final_answer.json` 出現で検知 (既存の wait_alert.sh 流のイベント駆動と整合)
- **バジェット**: `--budget-min` で打ち切り。未提出のまま終了しないよう
  損切り指示を監督AIに付与

## スモークテスト (2026-08)

ダミータスク (repo が `int main(){}` のみ) で監督AI がサブエージェント起動 →
提出 → final_answer.json → task_done まで 95 秒で完走することを確認済み。
