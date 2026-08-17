# tools/re — リバースエンジニアリング補助ツール

RE 版フレームワーク（`CLAUDE-re.md`）で使う、バイナリ解析・自動化ツール群。

現状は **radare2（導入済み）で即動く** `r2_recon.py` を軸にしつつ、**Ghidra は導入前提**
の設計。ツールは「サブエージェントが bash を直接叩く」のではなく、
**出力を安定化した薄いラッパー** として提供する（ペンテスト版の `state.py` と同じ思想:
決まった入力 → 安定した出力）。

## 1. r2_recon.py（今すぐ動く静止解析）

radare2 のヘッドレス実行をラップ。ANSI エスケープを除去した素のテキストを返す。

```bash
python3 tools/re/r2_recon.py info    ./vuln            # file + NX/PIE/Canary/RELRO
python3 tools/re/r2_recon.py funcs   ./vuln            # 関数一覧
python3 tools/re/r2_recon.py strings ./vuln            # 文字列
python3 tools/re/r2_recon.py imports ./vuln            # インポート
python3 tools/re/r2_recon.py disasm  ./vuln main       # 逆アセンブル
python3 tools/re/r2_recon.py xrefs   ./vuln 0x401234   # 参照元/参照先
```

保護機構判定は `file` + `readelf` ベースで自前実装（checksec 依存なし）。
PIE / NX / Canary / RELRO を判定（テストバイナリで検証済み）。

## 2. Ghidra ヘッドレス自動化（深掘り）

Ghidra は高精度なデコンパイル＋型・データフロー解析が強み。導入すれば
r2 より高品質な静的解析を自動化できる。ただし **GUI なしのヘッドレス実行** が前提。

### 2.1 analyzeHeadless の構造

Ghidra には `support/analyzeHeadless` というヘッドレス起動ラッパーがある。
「プロジェクト作成 → バイナリインポート → 自動解析 → スクリプト実行」を一括で行う。

```
support/analyzeHeadless <projectDir> <projectName> \
  -import <binary> \
  -scriptPath <scriptsDir> \
  -postScript <Analyze.java> \
  -deleteProject
```

- `-import`: インポートするバイナリ
- `-scriptPath`: GhidraScript を置いたディレクトリ
- `-postScript`: インポート＋自動解析後に実行するスクリプト（拡張子除いたクラス名）
- `-deleteProject`: 使い捨てプロジェクトを自動削除（毎回の実行で一意なプロジェクト名にして回避）

### 2.2 GhidraScript でできること（自動化の核）

GhidraScript は Java（または Jython `.py`）で書き、`analyzeHeadless` が実行する。
主要 API:

```java
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.app.decompiler.*;

public class Analyze extends GhidraScript {
    public void run() throws Exception {
        Program p = getCurrentProgram();
        FunctionManager fm = p.getFunctionManager();
        // 全関数の列挙 + デコンパイル
        for (Function f : fm.getFunctions(true)) {
            DecompInterface dc = new DecompInterface();
            dc.openProgram(p);
            DecompileResults r = dc.decompileFunction(f, 60, monitor);
            println("== " + f.getName() + " ==");
            println(r.getDecompiledFunction().getC());
        }
        // xref (参照元)
        ReferenceManager rm = p.getReferenceManager();
        // rm.getReferencesTo(addr) で呼び出し元を取得
    }
}
```

`println()` はヘッドレス実行時に stdout（ログ）へ出る。これをラッパーが回収して state/ に記録。

### 2.3 ghidra_bridge（対話的・GUI 前提）

`ghidra_bridge` は、**実行中の Ghidra（GUI 付き）** に Python から接続して対話操作する方式。

- 利点: リアルタイムで「この関数をデコンパイルして」と問い合わせられる
- 欠点: **GUI が起動している必要がある**（ヘッドレス不可）。エージェントの自律解析には不向き

→ **エージェント向けは analyzeHeadless（バッチ）**、**人間の手動解析は GUI + ghidra_bridge** と使い分けるのが正解。

### 2.4 Ghidra 導入方法

```bash
# 方式A: apt（Kali ならリポジトリにあり）
sudo apt install ghidra          # 要 Java 17 (openjdk-17-jdk)

# 方式B: 公式 zip（https://github.com/NationalSecurityAgency/ghidra/releases）
# unzip して support/analyzeHeadless を使う。同様に Java 17 必須
```

前提: **Java 17**。古い Java 8 だと起動しない。Kali なら `apt install ghidra` が最も楽。

## 3. Ghidra vs radare2 — 使い分け

| | radare2 (r2) | Ghidra |
|---|---|---|
| 導入 | 済み（環境にあり） | 未導入（Java 17 要） |
| デコンパイル品質 | 中（pdg 疑似コード） | **高**（型推論・データフロー） |
| ヘッドレス | `r2 -q -c` で即・軽量 | `analyzeHeadless`（重い・プロジェクト管理要） |
| 大規模バイナリ | 苦手（メモリ・遅い） | **得意**（ファームウェア等も） |
| 用途 | 軽い初期トリアージ・CTF 小バイナリ | 本格的な脆弱性探索・データフロー追跡 |

**設計指針**: 初期トリアージ（checksec/文字列/関数）は r2、本格的デコンパイル＋
データフロー解析は Ghidra、と 2 段構え。`r2_recon.py` は前者、GhidraScript は後者。

## 4. state スキーマの RE 読み替え

`state.py` は改修せず、**フィールドの意味を RE に読み替える**（雛形段階の低コスト方針）。

| state.py のキー | ペンテストの意味 | RE の読み替え |
|---|---|---|
| `hosts.json`（`--ip`） | ホスト IP | **バイナリパス/名前** |
| `services` | サービス（port/proto） | **発見した関数・シンボル** |
| `creds.json` | クレデンシャル | **アドレス・ガジェット・libc ベース**（必要時のみ） |
| `findings.json` | 脆弱性 | **発見したバグ（BOF/UAF/…）** + エクスプロイト段階 |
| `tried` | 攻撃手法 | **試した解析・攻撃ベクトル**（ret2libc 等の重複排除） |
| `relay` / `resume` | セッション引き継ぎ | **そのまま流用**（RE は長文脈で最重要） |

`cred`/`spray`（ペンテストのドメイン概念）は RE では基本使わない。`finding`/`tried`/`relay` が主力。

## 5. MCP 化の設計（共通契約に RE ツールを乗せる）

`mcp/server.py` の `state_*` / `kb_query` の隣に、RE ツールを追加する（同じラッパー思想）。

```python
# mcp/server.py に追加するツール例
@mcp.tool()
def re_checksec(binary: str) -> str:
    """バイナリの保護機構 (NX/PIE/Canary/RELRO) を判定する"""
    return _run([PYTHON, "tools/re/r2_recon.py", "info", binary])

@mcp.tool()
def re_disasm(binary: str, func: str) -> str:
    """指定関数を逆アセンブルする (r2)"""
    return _run([PYTHON, "tools/re/r2_recon.py", "disasm", binary, func])

@mcp.tool()
def re_decompile(binary: str, func: str) -> str:
    """指定関数をデコンパイルする (Ghidra analyzeHeadless 経由)"""
    # Ghidra 導入後に有効化。analyzeHeadless + GhidraScript をサブプロセス実行
    return _run(["support/analyzeHeadless", ...])
```

これで、Claude Code / Codex / DSH のどのランタイムからも、
**「vuln の main をデコンパイルして」「checksec して」** を自然言語で呼べる。

## 6. 実行環境の構成（Kali 主体 + Windows RE 併用）

**方針: フレームワーク（moro-agent）は Kali Linux 前提で運用。RE の動的解析・
PE エクスプロイトの一部のみ、Windows ネイティブを併用する。**

```
Kali Linux (主体)
├── moro-agent 全体 (監督AI / run.sh / state / MCP)   ← 全部ここで動く
├── Ghidra / r2 / gdb / pwntools                        ← 静的解析 + ELF エクスプロイト
└── PE (.exe/.dll) の静的解析 (Ghidra/r2 はクロスプラットフォーム) ← ここで完結可

Windows ネイティブ (併用・RE 動的解析のみ)
└── x64dbg / WinDbg / API フック / サンドボックス      ← PE の動的解析・デバッグ
```

### 役割分担

| 作業 | 環境 | ツール |
|---|---|---|
| フレームワーク運用（監督・並列・状態） | **Kali** | moro-agent（bash/tmux/state/MCP） |
| 静的解析（逆アセンブル・デコンパイル） | **Kali** | Ghidra / r2（PE も可） |
| ELF エクスプロイト開発 | **Kali** | gdb + pwntools |
| PE 動的解析（デバッグ・API 追跡） | **Windows** | x64dbg / WinDbg |
| PE エクスプロイト（DLL 注入等） | **Windows** | ネイティブツール |

### 連携パターン（現実的）

1. **静的解析は Kali で完結** — Ghidra / r2 は PE を読めるので、まず Kali 側の
   `re_*` ツールで関数・文字列・インポート・保護機構を把握
2. **動的解析は人間介在の Windows 連携** — エージェントが「この関数の実行時挙動を
   観測したい」と判断 → 人間が x64dbg でブレークポイントを張る。完全自動の
   「Kali エージェント → Windows デバッガ」連携は、リモートデバッグ or
   x64dbg の Python プラグインで可能だが、まず人間介在から始める
3. **結果は state/ にフィードバック** — Windows で得た観測（レジスタ値・クラッシュ
   アドレス）を `state.py finding` / `log` に記録し、Kali 側のエージェントが続きを読む

## 7. 次のステップ

1. **Ghidra 導入**（要 Java 17）→ `analyzeHeadless` を試す
2. **GhidraScript の雛形**（`tools/re/ghidra_decompile.java`）を作り、デコンパイルを自動化
3. **MCP に `re_*` ツール追加**（上記設計を実装）
4. **エクスプロイト開発ツール**（gdb / pwntools 導入 → `tools/re/` にラッパー）
5. **実ターゲット（CTF バイナリ）で通し検証**
