# Pentest Framework — AI マルチエージェント ペネトレーションテスト

## 構造

```
pentest-framework/
├── CLAUDE.md              ← 監督 AI への指示 (人間が編集)
├── handoff_templates.md   ← ハンドオフフォーマット (CHAP 準拠)
├── models.json            ← モデル → プロバイダのマッピング
├── config                 ← 表示モード、サブエージェント設定
├── scripts/
│   ├── run.sh             ← モデル名だけで起動 (API key 自動解決)
│   ├── state.py           ← 状態管理 CLI (host/tried/cred/finding/log/show/spray/relay/resume)
│   └── gen_report.py      ← findings → Markdown レポート
├── state/                 ← 共有状態 (全エージェントの接点)
│   ├── scope.json, hosts.json, creds.json, findings.json, log.jsonl
└── .env                   ← API キー
```

## 使い方

```bash
cp .env.example .env && vim .env     # API キー (サブスクなら不要)
vim state/scope.json                  # ターゲット
./start.sh                            # 起動 (tmux 自動)
```

最初のプロンプト:
```
CLAUDE.md, handoff_templates.md, models.json, config, state/scope.json を読んで、
監督者として攻撃計画を立て、run.sh でサブエージェントを起動してください。
```

操作:
```
Ctrl+b o    — ペイン切替 (サブエージェントを見る)
Ctrl+b z    — ペイン最大化/戻す
Ctrl+b n/p  — タブ切替 (LAYOUT=tab の場合)
```

## 設計思想

- **CLAUDE.md が全て**: 人間 → 監督 AI → サブエージェントの指示系統
- **state/ が唯一の接点**: エージェント間の通信は JSON 経由。tried 配列で重複排除
- **モデル非依存**: models.json に 1 行追加 + .env にキーで新プロバイダ対応
- **config**: LAYOUT(split/tab)、TMUX_SESSION、MAX_AGENTS、MONITOR_INTERVAL
