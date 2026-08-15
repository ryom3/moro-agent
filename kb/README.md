# セキュリティ KB — ローカル RAG パイプライン

自分の教材(Notion メモ、OffSec などのコース資料 PDF、技術書 PDF)を、CLI /
エージェントから検索できるようにするローカル RAG。

**検索方式**:構造ベースチャンキング → BGE-M3 dense + BM25 sparse → RRF 融合 →
bge-reranker-v2-m3 リランキング。日英混在・技術トークン(`SUID` `/etc/passwd`
`CVE-2021-4034`)を確実に引く独自トークナイザ入り。PDF はレイアウト解析
(2カラム対応・コードブロック保全・任意OCR)で取り込む。

```
クエリ
  ├─ BGE-M3 (dense)   → 上位 dense_k ─┐
  ├─ BM25   (sparse)  → 上位 sparse_k ┴→ RRF 融合 → 上位 fuse_k
  └─ bge-reranker-v2-m3 で再順位付け → 上位 top_k
```

単一コマンド `kb` に集約:`ingest` / `query` / `serve` / `eval` / `sync` / `status`。

---

## セットアップ

```
pip install -r requirements.txt
```

初回の取り込み・検索時に、HuggingFace から BGE-M3(約4.6GB)と
bge-reranker-v2-m3(約2.3GB)を取得してキャッシュします(以降は再ダウンロード無し)。

環境変数:
| 変数 | 意味 |
|------|------|
| `KB_BACKEND` | `bge`(既定・実モデル)/ `mock`(オフライン検証用) |
| `KB_PORT` | デーモンのポート(既定 8917) |

---

## GPU について(重要)

**このパイプラインで重いのは埋め込み計算(取り込み時)とリランク(検索時)だけ**で、
そこが GPU で劇的に速くなります。実測:notion 3284チャンクの取り込みが、
**CPU で約105分 → GPU(RTX 5070)で34秒**(約180倍)。

### GPU を使うための torch の選び方(ここでハマる)

GPU が使えるかは「NVIDIA ドライバが対応する CUDA バージョン」と「PyTorch が要求する
CUDA バージョン」の一致で決まります。ズレると `cuda available: False` や、最悪
import 時のクラッシュ(`double free` 等)になります。手順:

1. ドライバの対応 CUDA 上限を確認:
   ```
   nvidia-smi        # 右上の "CUDA Version: 12.x" がドライバの上限
   ```
2. その上限に **収まる** torch を入れる。例えば上限が 12.9 なら cu128:
   ```
   pip install torch --index-url https://download.pytorch.org/whl/cu128
   python -c "import torch; print('cuda', torch.cuda.is_available())"
   ```
   `cuda True` が出れば成功。**これが出るまで先に進まない。**

注意点:
- **torch を無闇に上げない**。新しすぎる CUDA ビルド(例 cu130)はドライバが古いと
  弾かれる。ドライバ上限に合わせるのが基本。
- **torch と torchvision は必ず同じ CUDA ビルドで揃える**(片方だけ入れ替えると
  `torchvision::nms does not exist` 等で壊れる)。揃えるなら同時に:
  ```
  pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
  ```
- RTX 5070 のような新しめ(Blackwell)の GPU では、対応が入った cu128 以降が必要。
  古い cu12x や中途半端なビルドは import 時にクラッシュすることがある。
- 環境を触るときは **必ず venv の中で**。system Python に混在させると依存が壊れる。

### GPU が用意できない/面倒なとき(CPU で動かす)

CPU 専用 torch を入れれば、GPU 無しでも確実に動く(取り込みは遅いが結果は同じ):
```
pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu
```
取り込みは一度きりなので、CPU でも「夜間に放置で完走」なら実用的。検索の速度は
下記「デーモンとリランク」を参照。

### VM(Hyper-V の Kali 等)で使う場合

Hyper-V の Linux VM から物理 GPU を直接使う(DDA/GPU-PV)のは**難易度が高く不安定**
（コンシューマ GeForce では DDA 非対応が多く、Linux ゲストの GPU-PV は実験的）。
現実的な選択肢は3つ:

- **A. VM の CPU で動かす**：追加設定不要。取り込みは遅い。検索は `--no-rerank` なら
  実用速度(下記)。索引 `kb_data/` は他マシンで作って VM にコピーしてもよい
  (ベクトルはマシン非依存)。
- **B. ホスト(GPU 有り)でデーモンを立て、VM から HTTP で問い合わせる**：
  ホストで `kb serve --host 0.0.0.0`、VM からはそのホスト IP:8917 に投げる。
  操作は VM 完結のまま、計算はホスト GPU で一瞬。GPU パススルーの危険作業は不要。
  ※ VM から使うにはファイアウォールで 8917 を許可し、VM 側の接続先をホスト IP に
    向ける設定が要る。
- **C. VM に GPU を直接パススルー**：理想だが茨の道。スナップショット必須の別作業。

大量に再取り込みするなら、GPU のあるマシン(またはホスト)で索引を作るのが圧倒的に楽。

---

## 使い方

### ingest — 取り込み(md / pdf / txt / csv)

`--source` は反復可。取り込みは**累積**(dense は upsert、BM25 は毎回コーパス全体で
再構築)。`--tag` で出所を刻み、`--reset` で初期化。

```
kb ingest --reset --source ./notion-export --tag notion    # 初回は --reset
kb ingest --source ./pdf/offsec --tag offsec               # 追記(reset 無し)
kb ingest --source ./pdf/cs-books --tag cs-books
```

**鉄則:`--reset` は「全部作り直す」ときだけ。** 追記時に付けると既存索引が消える。
埋め込みモデルやチャンク粒度を変えたときは `--reset` で全再構築(旧ベクトルと次元/
分布が変わり整合しないため)。

- `.md` → 構造ベースチャンカー(見出し + コードブロック保全 + パンくず)
- `.pdf` → レイアウト解析(下記)。`.txt` / `.csv` → プレーンテキスト

PDF オプション:`--pdf-engine native`(既定・2カラム+コード保全)/ `pymupdf4llm`
(代替エンジン。テキストの無いページを自動 OCR するため遅くなりやすい)。
`--ocr` でスクショ主体ページを Tesseract 処理(要 `tesseract` バイナリ、
`--ocr-lang eng+jpn`)。

### query — 検索

```
kb query "kerberoasting"                     # 全ソース横断
kb query "AMSI bypass" --tag offsec          # タグで絞る
kb query "python socket" --tag cs-books
kb query "..." --json --top 5                # エージェント用(機械可読)
kb query "..." --no-rerank                   # リランク省略(速い / 下記)
kb query "..." --no-hybrid                   # dense のみ
```

### serve — モデル常駐デーモン

```
kb serve                                     # 127.0.0.1:8917
kb serve --host 0.0.0.0 --port 8917          # 他マシン(VM 等)から使う場合
```

デーモンが動いていれば `query` は自動でそちら経由(高速)。無ければその場でモデルを
ロードしてフォールバック(遅い)。

### status — 状態確認

```
kb status
# chunks : 16083  (from 506 files, 8695 contain code)
#    #cs-books: 8777
#    #offsec: 4022
#    #notion: 3284
# daemon : up (http://127.0.0.1:8917, 16083 chunks)
```

### sync — Notion API 同期

`.env`(kb の親ディレクトリ)に `NOTION_API_KEY=ntn_xxx` を置き、対象ページに
Integration を接続してから:
```
kb sync --list        # 共有済みページ一覧
kb sync --diff        # 前回同期以降の更新分
kb sync --ingest      # 同期して取り込み(tag=notion)
```

### eval — 検索品質の計測(任意)

`eval_set.example.yaml` を自分の実クエリ 20〜30 問+正解で埋め、dense_only /
hybrid / hybrid+rerank を Recall@k・MRR・nDCG で比較:
```
kb eval --eval-set my_eval.yaml --k 1 3 5 10
```
「rerank を足すと良くなるか」「PDF エンジンの良し悪し」「チャンク粒度」を数値で
判断したいときに作る。使うだけなら不要。

---

## デーモンとリランク(速度の勘所)

検索 1 回は「モデルロード」+「クエリ計算」からなる。**デーモンが省くのはモデル
ロード(起動時 1 回だけにする)で、クエリ計算は毎回行う**。

クエリ計算の重い部分は **reranker**(RRF で絞った `fuse_k`=50 件を 1 件ずつ照合)。
- GPU:全部込みで一瞬。
- CPU:reranker が数秒〜十数秒。ここが「CPU だと遅い」の主因。

CPU で速くしたいとき:
- `--no-rerank` で reranker を省く → **約1秒**。失うのは最後の並べ替え精度のみ
  (dense+sparse+RRF は残るので実用十分)。
- あるいは `config.py` の `fuse_k` を下げる(50→20 等)。
- 常に rerank 無しでよいなら `config.py` の `use_rerank=False`。

**推奨構成:デーモン常駐 + GPU**(ロード 1 回、計算も一瞬)。GPU が無いなら
**デーモン + CPU + `--no-rerank`** が実用的な落とし所。

---

## エージェント連携

```python
import subprocess, json
out = subprocess.run(
    ["python3", "kb.py", "query", "SUID privilege escalation", "--json", "--top", "3"],
    capture_output=True, text=True, check=True)
results = json.loads(out.stdout)   # [{score, breadcrumb, source, tag, text, ...}, ...]
```
「回答前に `kb.py query "<検索語>" --json` で自分のメモを検索する」と指示すると効果的。

---

## 設定

既定は `config.py`(`KBConfig`)。YAML で上書き可(各サブコマンドに `--config`)。
主なパラメータ:チャンク粒度(`target_chars`/`max_chars`/`min_chars`)、候補数
(`dense_k`/`sparse_k`/`fuse_k`)、`rrf_k`、`top_k`、`use_hybrid`/`use_rerank`。
生成物は既定で `./kb_data/`(`db/`=Chroma、`chunks.jsonl`、`bm25.pkl`)。

---

## 取り扱い注意(著作権・個人情報)

OffSec 教材の PDF には**受講者ウォーターマーク(氏名・受講者ID)**が埋め込まれ、
抽出テキストや検索結果にも現れる。市販技術書も含め、これらは著作物。**索引・抽出
テキスト・検索結果を共有/公開しないこと。** あくまでローカル個人利用に限る。

---

## オフライン動作確認

```
bash smoke_test.sh    # mock バックエンドで ingest→serve→query→status→eval を通し検証
```

---

## ファイル構成

| ファイル | 役割 |
|----------|------|
| `kb.py` | 統合エントリ(`python3 kb.py <subcommand>`) |
| `cli.py` | サブコマンド実装 |
| `config.py` | 設定(`KBConfig`) |
| `tokenizer.py` | BM25 用ハイブリッドトークナイザ(技術トークン保持 + 日本語分割) |
| `chunking.py` | 構造ベース markdown チャンカー |
| `loaders.py` | PDF レイアウト解析 + OCR / md・txt・csv |
| `backends.py` | 埋め込み(BGE-M3)+ リランカー(transformers 直叩き)/ モック |
| `index.py` | Chroma(dense・upsert)+ BM25(sparse・全体再構築) |
| `search.py` | dense+sparse→RRF→rerank パイプライン |
| `server.py` | モデル常駐 FastAPI デーモン |
| `eval.py` | 評価ハーネス |
| `notion_sync.py` | Notion API 同期 |
| `ingest.py` / `query.py` | 旧コマンド互換シム(`kb.py` に委譲) |
| `eval_set.example.yaml` | 評価セット雛形 |
| `smoke_test.sh` | オフライン通しテスト |