#!/usr/bin/env python3
"""
Pentest Framework 設計ドキュメント生成器 v2
------------------------------------------
議論を経て更新した設計思想 (ランタイム差し替え / 2層分離 / MCP共通契約) を、
自己完結型 HTML (埋め込み SVG, ダークテーマ) として可視化する。

図構成:
  ① AS-IS  現在の実態 (意図と乖離、問題チップ付き)
  ② TO-BE  設計思想 (3原則)
  ③ TO-BE  アーキテクチャ (ランタイム層 + ツール層 + レジストリ + MCP)

使い方: python3 scripts/gen_design.py
"""
import html
import math


def escape(s):
    return html.escape(str(s))


class SVG:
    def __init__(self, w, h, bg="#0b0f1a"):
        self.w, self.h = w, h
        self.elems = []
        self.defs = []
        self.bg = bg

    def add(self, s):
        self.elems.append(s)

    def rect(self, x, y, w, h, r=14, fill="#161c2e", stroke="none", sw=1.5,
             dash=None, opacity=1.0, shadow=False):
        d = f'stroke-dasharray="{dash}"' if dash else ''
        o = f'fill-opacity="{opacity}"' if opacity < 1.0 else ''
        flt = ' filter="url(#gShadow)"' if shadow else ''
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {d} {o}{flt}/>')

    def text(self, x, y, s, size=13, fill="#e6edf7", anchor="start", weight=600,
             ls=None, maxw=None):
        lines = str(s).split("\n")
        if maxw:
            import textwrap
            wrapped = []
            for ln in lines:
                wrapped.extend(textwrap.wrap(ln, maxw))
            lines = wrapped
        for i, ln in enumerate(lines):
            self.add(f'<text x="{x}" y="{y + i*(ls or size*1.35)}" '
                     f'font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
                     f'font-weight="{weight}">{escape(ln)}</text>')

    def line(self, x1, y1, x2, y2, color="#3b4560", w=2, dash=None, arrow=True):
        d = f'stroke-dasharray="{dash}"' if dash else ''
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'stroke="{color}" stroke-width="{w}" {d}/>')
        if arrow:
            ang = math.atan2(y2 - y1, x2 - x1)
            L = 10
            a1 = ang + math.radians(150)
            a2 = ang - math.radians(150)
            self.add(f'<polygon points="{x2},{y2} {x2+L*math.cos(a1)},{y2+L*math.sin(a1)} '
                     f'{x2+L*math.cos(a2)},{y2+L*math.sin(a2)}" fill="{color}"/>')

    def render(self):
        body = "\n".join(self.elems)
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" '
                f'style="background:{self.bg};border-radius:16px;'
                f'font-family:ui-sans-serif,system-ui,\'Segoe UI\',sans-serif">'
                f'{body}</svg>')


def define_gradients(s):
    s.add('<defs>')
    s.add('<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
          '<stop offset="0%" stop-color="#1b2340"/><stop offset="100%" stop-color="#141a2e"/></linearGradient>')
    s.add('<linearGradient id="gGreen" x1="0" y1="0" x2="0" y2="1">'
          '<stop offset="0%" stop-color="#123b2e"/><stop offset="100%" stop-color="#0d2a21"/></linearGradient>')
    s.add('<linearGradient id="gAmber" x1="0" y1="0" x2="0" y2="1">'
          '<stop offset="0%" stop-color="#4a3410"/><stop offset="100%" stop-color="#33250b"/></linearGradient>')
    s.add('<linearGradient id="gRed" x1="0" y1="0" x2="0" y2="1">'
          '<stop offset="0%" stop-color="#4a1420"/><stop offset="100%" stop-color="#331018"/></linearGradient>')
    s.add('<linearGradient id="gBlue" x1="0" y1="0" x2="0" y2="1">'
          '<stop offset="0%" stop-color="#12304a"/><stop offset="100%" stop-color="#0d2333"/></linearGradient>')
    s.add('<filter id="gShadow" x="-20%" y="-20%" width="140%" height="140%">'
          '<feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#000" flood-opacity="0.45"/></filter>')
    s.add('</defs>')


def section_title(s, text, color="#8fd0ff"):
    s.text(32, 46, text, 20, color, weight=700)


# ---------------------------------------------------------------------------
# 図 1: AS-IS 現在の実態 (意図と乖離) — パイプライン + 問題チップ
# ---------------------------------------------------------------------------
def fig_asis():
    s = SVG(960, 720)
    define_gradients(s)
    section_title(s, "① AS-IS — 現在の実態（意図と現実の乖離）", "#f2a7a7")

    LX, LW = 30, 360
    CX = LX + LW // 2  # 210

    def node(y, h, fill, stroke, title, sub, tc="#fff"):
        s.rect(LX, y, LW, h, fill=fill, stroke=stroke, sw=1.5, r=14, shadow=True)
        s.text(CX, y + h//2 - 12, title, 15, tc, anchor="middle", weight=700)
        s.text(CX, y + h//2 + 12, sub, 11, "#9fb0cc", anchor="middle", weight=500,
               maxw=26)

    def chip(x, y, w, h, icon, title, desc, accent):
        s.rect(x, y, w, h, fill="url(#gRed)" if icon == "🔴" else
               ("url(#gAmber)" if icon == "🟠" else "url(#g)"),
               stroke=accent, sw=1.3, r=11, shadow=True)
        s.text(x + 12, y + 22, icon + " " + title, 13.5, "#fff", weight=700, maxw=34)
        s.text(x + 12, y + 42, desc, 10.5, "#c9d3e8", weight=500, maxw=40)

    nodes = [
        (70, 70, "url(#g)", "#4b5878", "人間 (オペレータ)", "start.sh + CLAUDE.md"),
        (170, 80, "url(#gGreen)", "#2f8f6f", "監督AI = Claude Code CLI", "Anthropic 製ループ"),
        (280, 76, "url(#g)", "#3f5a8f", "run.sh (runtime 分岐)", "claude-code / codex / aider"),
        (386, 88, "url(#g)", "#3f5a8f", "サブエージェント (複数)", "CLI を tmux で並列 spawn"),
        (520, 80, "url(#gBlue)", "#2f7fa8", "state/ + kb/ (共有)", "JSON + flock / ローカルRAG"),
    ]
    for (y, h, f, st, t, sub) in nodes:
        node(y, h, f, st, t, sub)

    # 縦の矢印
    for y1, y2 in [(140, 170), (250, 280), (356, 386), (474, 520)]:
        s.line(CX, y1, CX, y2, "#4b6aa8", 2)

    # 問題チップ (右カラム)
    RX, RW = 430, 500
    chips = [
        (170, "🔴", "ループが Anthropic 製 CLI に固定", "ループ=Claude Code内蔵。改造不可。", "#e05a6f"),
        (232, "🟠", "監督 = 単一障害点 + 手動ポーリング", "watch で目視。alert はファイル追記のみ。", "#e0a25a"),
        (294, "🟠", "起動経路の分裂", "run.sh と run_codex.sh が別実装。", "#e0a25a"),
        (350, "🔴", "シークレット漏洩", "eval 展開で APIトークンが ps に平文露出。", "#e05a6f"),
        (412, "🟠", "tmux に二重責務", "実行コンテナ + 観察窓が同一物。", "#e0a25a"),
        (468, "🟠", "OOM の泥縄対処", "node heap 5-6GB → NODE_OPTIONS=2560。", "#e0a25a"),
        (524, "🔴", "JSON 並行競合", "flock 不全 + len+1 で ID 重複。", "#e05a6f"),
        (580, "🔴", "安全はプロンプトのみ", "--dangerously-skip-permissions。", "#e05a6f"),
    ]
    for (y, icon, t, d, accent) in chips:
        chip(RX, y, RW, 48, icon, t, d, accent)

    # 監督→run.sh は「差し替え」の起点だった、を注記
    s.text(RX, 300, "※ run.sh の分岐は「ランタイム差し替え」の萌芽だが、", 11, "#8fa0c0", weight=500)
    s.text(RX, 316, "   アドホックで分裂している。これをレジストリに昇格するのが TO-BE。", 11, "#8fa0c0", weight=500)

    # 下段バナー: 救い
    s.rect(30, 636, 900, 72, fill="url(#gGreen)", stroke="#2f8f6f", sw=1.5, r=14, shadow=True)
    s.text(52, 662, "💡 救い (AS-IS の中にある TO-BE の下地)", 14, "#7fd1a8", weight=700)
    s.text(52, 686, "script -q -f で全出力を logs/ に保存済み / state.py append_log はイベント思考 / kb.py は単一CLIに集約済み",
           12, "#c9d3e8", weight=500)
    return s


# ---------------------------------------------------------------------------
# 図 2: TO-BE 設計思想 (3原則)
# ---------------------------------------------------------------------------
def fig_philosophy():
    s = SVG(960, 420)
    define_gradients(s)
    section_title(s, "② TO-BE — 設計思想（3つの大原則）", "#8fd0ff")

    cards = [
        ("gBlue", "#3f8fd0", "ランタイム差し替え",
         "ループ（頭脳）は「選ぶもの」。\n監督もサブも DSH / Claude Code /\nCodex / aider から自由に。\n混在も可。"),
        ("gBlue", "#3f8fd0", "実行と観察の分離",
         "tmux / Web GUI は「窓」であり\n実行とは無関係。\nループが何であっても\n画面とログの形は不変。"),
        ("gBlue", "#3f8fd0", "ツール・状態は DSH が吸収",
         "MCP + イベントストリームを\n「共通契約」に。\n全ループが同じツールを\n同じ形で呼ぶ。"),
    ]
    w, h, gap = 292, 280, 22
    x0, y0 = 20, 84
    for i, (key, accent, title, desc) in enumerate(cards):
        x = x0 + i * (w + gap)
        s.rect(x, y0, w, h, fill=f"url(#{key})", stroke=accent, sw=1.5, r=18, shadow=True)
        s.text(x + w//2, y0 + 34, f"0{i+1}", 26, accent, anchor="middle", weight=800)
        s.text(x + w//2, y0 + 64, title, 16, "#fff", anchor="middle", weight=700)
        s.text(x + 20, y0 + 96, desc, 12, "#c9d3e8", weight=500, ls=20)
    return s


# ---------------------------------------------------------------------------
# 図 3: TO-BE アーキテクチャ (2層 + レジストリ + MCP)
# ---------------------------------------------------------------------------
def fig_tobe():
    s = SVG(960, 680)
    define_gradients(s)
    section_title(s, "③ TO-BE — アーキテクチャ（ランタイム層 + ツール層）", "#8fd0ff")

    # --- ランタイム層 ---
    s.rect(30, 66, 900, 220, fill="url(#g)", stroke="#3f5a8f", sw=1.5, r=18, shadow=True)
    s.text(52, 92, "ランタイム層 — ループ = 差し替え可能な頭脳", 14, "#8fb8ff", weight=700)
    s.text(52, 112, "どのエージェントがどの頭脳を使うかは設定で決める", 11, "#8fa0c0", weight=500)

    # 監督 row
    s.text(52, 150, "監督 AI (どれか1つ)", 12, "#c9d3e8", weight=600)
    sup = [("DSH", "url(#gGreen)", "#2f8f6f", "自前ループ + pi-ai任意モデル"),
           ("Claude Code", "url(#g)", "#e0a25a", "Anthropic 製ループ"),
           ("Codex", "url(#g)", "#3f5a8f", "OpenAI 製ループ")]
    for i, (name, fl, ac, sub) in enumerate(sup):
        x = 52 + i * 288
        s.rect(x, 160, 272, 52, fill=fl, stroke=ac, sw=1.3, r=10, shadow=True)
        s.text(x + 136, 180, name, 14, "#fff", anchor="middle", weight=700)
        s.text(x + 136, 198, sub, 10, "#9fb0cc", anchor="middle", weight=500)

    # サブ row
    s.text(52, 236, "サブエージェント (自由に混在)", 12, "#c9d3e8", weight=600)
    sub_agents = [("Claude Code", "#e0a25a"), ("Codex", "#3f5a8f"),
                  ("DSH native", "#2f8f6f"), ("aider", "#8a7fd0")]
    for i, (name, ac) in enumerate(sub_agents):
        x = 52 + i * 212
        s.rect(x, 246, 196, 34, fill="url(#g)", stroke=ac, sw=1.2, r=8)
        s.text(x + 98, 268, name, 12, "#e6edf7", anchor="middle", weight=600)

    # --- レジストリ ---
    ry = 316
    s.rect(250, ry, 460, 58, fill="url(#gBlue)", stroke="#2f7fa8", sw=1.5, r=14, shadow=True)
    s.text(480, ry + 24, "ランタイムレジストリ", 14, "#8fd0ff", anchor="middle", weight=700)
    s.text(480, ry + 44, "spawn_subagent(runtime, model, prompt)", 11, "#9fb0cc",
           anchor="middle", weight=500)
    s.line(480, 286, 480, ry, "#2f7fa8", 2)

    # --- ツール層 ---
    s.rect(30, 404, 900, 200, fill="url(#gBlue)", stroke="#2f7fa8", sw=1.5, r=18, shadow=True)
    s.text(52, 430, "ツール層 — DSH が吸収する土台 (共通契約)", 14, "#8fd0ff", weight=700)
    s.text(52, 450, "共通契約 = MCP ツール + イベントストリーム。全ループが同じ形で呼ぶ", 11, "#8fa0c0", weight=500)

    # MCP server box (中央上)
    s.rect(300, 462, 360, 46, fill="url(#gAmber)", stroke="#e0a25a", sw=1.3, r=10, shadow=True)
    s.text(480, 482, "★ MCP サーバ: state.py + kb.py", 13.5, "#ffd9a0", anchor="middle", weight=700)
    s.text(480, 500, "同一ツール面を Claude Code / Codex / DSH が共用", 10, "#c9d3e8",
           anchor="middle", weight=500)

    # 下部 5 chips
    tools = [("サンドボックス", "Landlock 実強制"), ("承認ゲート", "human-in-the-loop"),
             ("イベントストリーム", "生ログ + 構造化"), ("セッション", "イベントソーシング"),
             ("観察", "Web GUI / 履歴")]
    for i, (t, sub) in enumerate(tools):
        x = 52 + i * 172
        s.rect(x, 524, 156, 56, fill="url(#g)", stroke="#2f7fa8", sw=1.2, r=9)
        s.text(x + 78, 546, t, 12, "#e6edf7", anchor="middle", weight=600)
        s.text(x + 78, 564, sub, 9.5, "#8fa0c0", anchor="middle", weight=500)

    # レジストリ → ツール層 の矢印と注記
    s.line(480, ry + 58, 480, 404, "#2f7fa8", 2)
    s.text(495, 396, "共通契約で接続", 10.5, "#8fa0c0", weight=500)

    # 観察への破線 (ツール層 → 外)
    s.text(738, 640, "観察レイヤーは実行と無関係 (tail -f / Web GUI)", 10,
           "#8fa0c0", weight=500)
    return s


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def page(figs, title, subtitle):
    css = """
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body { margin:0; background:#070a12; color:#e6edf7;
      font-family: ui-sans-serif, system-ui, 'Segoe UI', 'Helvetica Neue', sans-serif;
      padding:40px 24px 80px; }
    .wrap { max-width:1020px; margin:0 auto; }
    header { text-align:center; margin-bottom:26px; }
    h1 { font-size:28px; margin:0 0 6px; letter-spacing:-.02em;
      background:linear-gradient(90deg,#7fd1a8,#8fd0ff,#f2a7a7);
      -webkit-background-clip:text; background-clip:text; color:transparent; }
    .sub { color:#8fa0c0; font-size:14px; }
    .badges { display:flex; gap:10px; justify-content:center; margin:18px 0 8px; flex-wrap:wrap; }
    .badge { font-size:12px; padding:4px 12px; border-radius:999px;
      background:#161c2e; border:1px solid #2a3450; color:#9fb0cc; }
    figure { margin:0 0 32px; }
    figcaption { font-size:12px; color:#5d6a87; margin-top:8px; text-align:center; }
    h2 { font-size:18px; color:#8fd0ff; margin:46px 0 14px; letter-spacing:-.01em; }
    table { width:100%; border-collapse:collapse; margin-top:6px; font-size:13px; }
    th,td { text-align:left; padding:9px 12px; border-bottom:1px solid #1c2438; vertical-align:top; }
    th { color:#8fd0ff; font-weight:600; background:#0e1424; }
    td:first-child { color:#c9d3e8; font-weight:600; white-space:nowrap; }
    code { color:#8fd0ff; background:#141b2e; padding:1px 6px; border-radius:5px; font-size:12px; }
    .legend-row { display:flex; gap:14px; flex-wrap:wrap; margin:12px 0 4px; font-size:12px; color:#8fa0c0; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }
    """
    rows = "\n".join(f'<figure>{f.render()}<figcaption>{c}</figcaption></figure>'
                     for f, c in figs)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body><div class="wrap">
<header>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="badges">
    <span class="badge">🔴 AS-IS 乖離</span>
    <span class="badge">🔵 TO-BE / ランタイム差し替え</span>
    <span class="badge">🟢 DSH が吸収する土台</span>
  </div>
</header>
<div class="legend-row">
  <span><span class="dot" style="background:#e05a6f"></span>赤/橙 = 実態の乖離・リスク</span>
  <span><span class="dot" style="background:#3f8fd0"></span>青 = 改善案 (ランタイム差し替え)</span>
  <span><span class="dot" style="background:#2f8f6f"></span>緑 = 正しい方向 / 下地</span>
</div>
{rows}

<h2>④ 監督 AI の差し替え (まとめ)</h2>
<table>
<thead><tr><th>監督 AI</th><th>ループ (機構)</th><th>頭脳 (モデル)</th><th>DSH の立ち位置</th></tr></thead>
<tbody>
<tr><td><b>DSH</b></td><td>DSH 製 (<code>dsh-agent-loop</code>)</td><td>pi-ai 経由で任意 (DeepSeek/Claude/GLM/Fugu)</td><td>全部を統合。goal/サブ/観察が監督自身に効く</td></tr>
<tr><td><b>Claude Code</b></td><td>Anthropic 製</td><td>Claude モデル</td><td>外部ランタイム。DSH はツール・状態・安全を供給</td></tr>
<tr><td><b>Codex</b></td><td>OpenAI 製</td><td>Fugu 含む Codex 対応モデル</td><td>同上</td></tr>
</tbody></table>
<p style="color:#8fa0c0;font-size:12px">サブエージェントは独立に選択可能 (Claude Code / Codex / DSH native / aider を混在)。</p>

<h2>⑤ DSH 移行マッピング</h2>
<table>
<thead><tr><th>今のもの (AS-IS)</th><th>吸収後の場所 (TO-BE)</th><th>何が変わるか</th></tr></thead>
<tbody>
<tr><td><code>run.sh</code> のモデル分岐</td><td>ランタイムレジストリ</td><td>分岐が設定値に。新ランタイム追加 = 1行</td></tr>
<tr><td><code>state.py</code> / <code>kb.py</code></td><td>MCP サーバ or DSH ツール</td><td>全ランタイムが統一して呼べる</td></tr>
<tr><td><code>logs/*.log</code> + script</td><td>イベントストリーム</td><td>ループが何でも同じ形のログ</td></tr>
<tr><td>tmux ペイン</td><td>観察レイヤー</td><td>ループ差し替えでも画面は不変</td></tr>
<tr><td>CLAUDE.md (憲法)</td><td>システムプロンプト / ペルソナ</td><td>ツール経由で実強制可能に</td></tr>
<tr><td>models.json</td><td>DSH settings + pi-ai</td><td>モデル解決を人任せに</td></tr>
<tr><td>VDP「お願い」</td><td>サンドボックス + 承認</td><td>お願い → 実強制</td></tr>
</tbody></table>

<p style="color:#5d6a87;font-size:12px;margin-top:24px;text-align:center">
generated by <code>scripts/gen_design.py</code> — 修正はスクリプトを直して再実行</p>
</div></body></html>"""


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="DESIGN.html")
    args = ap.parse_args()

    figs = [
        (fig_asis(), "現在の実態 — パイプライン上の問題点 (左) と救いとなる下地"),
        (fig_philosophy(), "TO-BE 設計思想 — ランタイム差し替え / 実行と観察の分離 / DSH吸収"),
        (fig_tobe(), "TO-BE アーキテクチャ — ランタイム層 + ツール層をレジストリとMCPで接続"),
    ]
    html = page(figs, "Pentest Framework — AS-IS から TO-BE への可視化",
                "AI マルチエージェント VDP 基盤 · ランタイム差し替え + DSH ツール吸収")
    with open(args.out, "w") as f:
        f.write(html)
    print(f"wrote {args.out} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
