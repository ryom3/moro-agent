# セキュリティ KB — ローカル RAG パイプライン

自分の教材(Notion メモ・各種コース資料PDF)を、Claude 等のエージェントから
`subprocess` で叩ける形で検索可能にするローカル RAG。

**検索方式(2026 ベストプラクティス)**:構造ベースチャンキング → BGE-M3 dense +
BM25 sparse → RRF 融合 → bge-reranker-v2-m3 リランキング。日英混在・技術トークン
(`SUID` `/etc/passwd` `CVE-2021-4034`)を確実に引く独自トークナイザ入り。

```
クエリ
  ├─ BGE-M3 (dense)   → 上位 dense_k ─┐
  ├─ BM25   (sparse)  → 上位 sparse_k ┴→ RRF 融合 → 上位 fuse_k
  └─ bge-reranker-v2-m3 で再順位付け → 上位 top_k
```

すべて単一コマンド `kb` に集約:`ingest` / `query` / `serve` / `eval` / `sync` / `status`。

## セットアップ

```bash
pip install -r requirements.txt
```

`FlagEmbedding`(BGE-M3 + reranker)は初回実行時に HuggingFace から重みを取得します。
`KB_BACKEND=mock` にするとダウンロード不要の決定的モックで動作確認できます。

| 環境変数 | 意味 |
|----------|------|
| `KB_BACKEND` | `bge`(既定・実モデル) / `mock`(オフライン検証用) |
| `KB_PORT` | デーモンのポート(既定 8917) |

## クイックスタート

```bash
# 1) 取り込み(初回は --reset。埋め込みモデルを変えたときも必ず --reset)
python3 kb.py ingest --reset --source ~/notion-export/ --tag notion
python3 kb.py ingest --source ~/oscp-pdfs/ --tag oscp --ocr    # PDF(レイアウト解析+OCR)

# 2) モデル常駐デーモン(別ターミナル)
python3 kb.py serve

# 3) 検索
python3 kb.py query "SUID privilege escalation"
python3 kb.py query "pwnkit pkexec" --json --top 5             # エージェント用
python3 kb.py query "SQLi bypass WAF" --tag oscp               # タグで絞る

# 4) 状態確認
python3 kb.py status
```

## サブコマンド詳細

### ingest — 取り込み(md / pdf / txt / csv)

`--source` は反復可。取り込みは**累積**(dense は upsert、BM25 は毎回コーパス全体で
再構築)。`--tag` で出所を刻み、`--reset` で初期化。

```bash
python3 kb.py ingest --reset --source ~/notion-export/ --tag notion
python3 kb.py ingest --source ~/pdfs/ --tag oscp
python3 kb.py ingest --source ~/a/ --source ~/b/ --tag cheatsheet
```

- `.md` → 構造ベースチャンカー(見出し + コードブロック保全 + パンくず)
- `.pdf` → **レイアウト解析**(下記 Phase 2)。`.txt` / `.csv` → プレーンテキスト

> **埋め込みモデルを変えたら必ず `--reset`**。旧 `all-MiniLM-L6-v2`(384次元)と
> BGE-M3(1024次元)はベクトル互換がないため、切り替え時は初期化して全再構築。

### query — 検索

```bash
python3 kb.py query "SUID privesc"              # 人間向け
python3 kb.py query "pwnkit" --json --top 5     # 機械可読(各結果に score/breadcrumb/source/tag/text)
python3 kb.py query "SQLi" --tag oscp           # タグ絞り込み(dense+sparse 両方に適用)
python3 kb.py query "..." --no-rerank           # リランクなし
python3 kb.py query "..." --no-hybrid           # dense のみ
```

デーモンが起動していれば自動でそちら経由(高速)。無ければその場でモデルをロード
(遅い)してフォールバック。

### serve — モデル常駐デーモン

BGE-M3 とリランカーのロードは数秒、BM25 も毎回再トークナイズすると重い。デーモンが
一度だけロードして常駐し、`query` は薄いクライアントになります。

```bash
python3 kb.py serve                # KB_PORT / 8917
```

### eval — 検索品質の計測

`eval_set.example.yaml` を雛形に、自分が答えを知っているクエリ 20〜30 個と
「引けてほしいチャンク」の対応表を作り、3 構成(dense_only / hybrid /
hybrid+rerank)を Recall@k・MRR・nDCG で比較。

```bash
python3 kb.py eval --eval-set my_eval.yaml --k 1 3 5 10
```

これがないと、ハイブリッド化やリランクが**あなたのコーパスで**効いたか数値で
確認できません。実装より先に作ることを推奨。

### sync — Notion API 同期

`notion_sync.py` に委譲。Integration を作り `.env` に `NOTION_API_KEY=ntn_xxx` を置く
(`kb/` の親ディレクトリ)。各ページに Integration を接続してから:

```bash
python3 kb.py sync --list       # 共有済みページ一覧
python3 kb.py sync --diff       # 前回同期以降の更新分
python3 kb.py sync --ingest     # 同期して取り込みまで(tag=notion)
```

### status — 状態確認

```bash
python3 kb.py status
# chunks   : 1423  (from 87 files, 512 contain code)
#    #notion: 940
#    #oscp: 483
# daemon   : up (http://127.0.0.1:8917, 1423 chunks)
```

## Phase 2 — PDF レイアウト解析

`loaders.py` が PyMuPDF のブロック幾何を使って、OffSec 等のコース資料を扱います。

- **2カラム対応**: 左ブロックと右ブロックが同じ高さ帯にあれば2カラムと判定し、
  「左カラム全部 → 右カラム全部」の順で読む。素朴な抽出(pdfplumber `extract_text`)が
  左右の行を混ぜて壊すのを防ぐ。
- **コードブロック保全**: 等幅フォントの行を ``` フェンスで囲む。`find / -perm -4000`
  や `LD_PRELOAD=...` が原形のまま BM25 に載る。
- **見出し検出**: 本文より大きいフォントを markdown 見出しに。構造チャンカーが
  パンくずを付けられる。
- **OCR(任意)**: `--ocr` でテキストがほぼ無いページ(スクショ)をラスタライズして
  Tesseract にかける。要 `apt install tesseract-ocr tesseract-ocr-jpn` + `pytesseract`。
  言語は `--ocr-lang eng+jpn`(既定)、パックが無ければ eng に自動フォールバック。

出力は markdown なので、PDF も Notion メモと**同じチャンカー**を通ります。別エンジンを
試したい場合は `--pdf-engine pymupdf4llm`。

## エージェント連携(Claude Code / Codex)

```python
import subprocess, json
out = subprocess.run(
    ["python3", "kb/kb.py", "query", "SUID privilege escalation", "--json", "--top", "3"],
    capture_output=True, text=True, check=True,
)
results = json.loads(out.stdout)   # [{score, breadcrumb, source, tag, text, ...}, ...]
```

エージェントに「まず `kb/kb.py query "<検索語>" --json` で自分のメモを検索してから
回答する」と指示すると効果的。互換のため `kb/query.py "<検索語>" --json` も同じ動作。

## オフライン動作確認

```bash
bash smoke_test.sh    # mock で ingest(md+pdf)→serve→query→status→eval を通しで検証
```

## 設定

既定値は `config.py`(`KBConfig`)。YAML で上書き可(各サブコマンドに `--config`)。
主なパラメータ: チャンク粒度(`target_chars` / `max_chars` / `min_chars`)、候補数
(`dense_k` / `sparse_k` / `fuse_k`)、`rrf_k`、`top_k`、`use_hybrid` / `use_rerank`。
生成物は既定で `./kb_data/`(`db/` = Chroma、`chunks.jsonl`、`bm25.pkl`)。

## ファイル構成

| ファイル | 役割 |
|----------|------|
| `kb.py` | 統合エントリ(`python3 kb.py <subcommand>`) |
| `cli.py` | サブコマンド実装 |
| `config.py` | 設定(`KBConfig`) |
| `tokenizer.py` | BM25 用ハイブリッドトークナイザ |
| `chunking.py` | 構造ベース markdown チャンカー |
| `loaders.py` | Phase 2: PDF レイアウト解析 + OCR / md・txt・csv |
| `backends.py` | 埋め込み・リランカー(実 BGE / モック) |
| `index.py` | Chroma(dense・upsert)+ BM25(sparse・全体再構築) |
| `search.py` | dense+sparse→RRF→rerank パイプライン |
| `server.py` | モデル常駐 FastAPI デーモン |
| `eval.py` | 評価ハーネス |
| `notion_sync.py` | Notion API 同期 |
| `ingest.py` / `query.py` | 旧コマンド互換シム(`kb.py` に委譲) |
| `eval_set.example.yaml` | 評価セット雛形 |
| `smoke_test.sh` | オフライン通しテスト |
