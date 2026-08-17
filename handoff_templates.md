# ハンドオフテンプレート

## 1. 監督 → サブエージェント (タスク割当)

```
## タスク
{1-2文で具体的に}

## ターゲット
{IP}:{PORT} — {サービス名} {バージョン}
既知: {OS、脆弱性、到達方法}

## 認証情報
{user:secret (source) — 使えるもの全て}

## 試行済み (繰り返すな)
{state.py show --host IP の tried をそのまま貼る}

## 未探索 (*untried*)
{前のエージェントが発見したが手をつけていないもの}

## ルール
- 仮説を立てて 1 変数ずつ検証。レシピを盲目的にたどるな
- **攻撃手法に迷ったら `python3 kb/kb.py query "キーワード" --json` で検索せよ**
- 試行 → state.py tried / cred 発見 → state.py cred / 成果 → state.py finding
- **ターゲット状態の改変は「可逆なら許可」** — 元データを保存済みで復元手順がある場合、
  1 メッセージ/1 レコード単位の上書き（例: retained MQTT メッセージの url フィールド書き換え）は
  宣言すれば許可される。**安全側に倒しすぎて「新規のテスト用監視点のみ」に自粛するな** —
  対象が実際に読む既存の監視点こそが唯一の勝ち筋であり得る。改変前に必ず元データを控え、
  検証後に復元できる状態にせよ。
- **重大な発見時は即 alert**:
  - root/flag 取得 → `state.py alert --host IP --type root --detail "..."`
  - 新 cred 発見 → `state.py alert --host IP --type cred --detail "user:pass"`
  - 新ネットワーク発見 → `state.py alert --host IP --type pivot --detail "10.0.0.0/24"`
  - 確実な行き止まり → `state.py alert --host IP --type dead-end --detail "理由"`
  - **タスク完了 → `state.py alert --host IP --type done --detail "結果の要約"`**
- 自然なチェックポイントで relay。最低 15 イテレーション空ける
- フラグ取得時は relay せず finding + alert に記録して終了
- **タスクが完了したら必ず alert --type done で監督に報告してから終了せよ**
```

## 2. サブエージェント → 次のセッション (relay プロトコル)

以下のフォーマットで relay する。ナラティブを削り、事実は省略するな。
前のプロトコルに含まれている情報は繰り返すな。

```
# Relay Protocol {N}

## Current state
- Host: {hostname} ({IP}) {user} shell {shell_type}, {privilege_level}.
- Target: {IP} running {service} {version} on port {port}.
- Workspace: {path} contains {重要なファイル}.

## Recon/enumeration
- {ツール} で {何を発見}; {具体的な値}.
- {エンドポイント} → {ステータス} ({意味}).

## Exploit / foothold
- {CVE/手法}: {コマンド}.
- {結果}: {得たもの — cred, shell, file content}.

## Failed attempts / constraints
- {何を} → {なぜ失敗} ({エラーメッセージ}).
- {回避策があれば記載}.

## Unexplored surface (*untried*)
- Port {N}/tcp not probed beyond nmap; potential {推測}.
- {エンドポイント/機能} not tested.
- {cred} not sprayed against {host}.

## Considerations for next agent
{構文の罠、タイミング制約、ツールの癖、推奨アプローチ — 短く}
```

### 実例 (CHAP 論文より — CVE-2024-23897 Jenkins LFI)

```
# Relay Protocol 1

## Current state
- Host: Kali (aarch64) user shell bash, regular user privileges; no elevated access on target yet.
- Target: 192.168.5.1 running Jenkins 2.441 on Jetty 10.0.18 (port 8080) and DB2/agent listener on 50000/tcp.
- Workspace: /ctf-workspace contains exploit (51993.py) and virtualenv with requests installed.

## Recon/enumeration
- Nmap full TCP scan discovered 8080/tcp (Jenkins) and 50000/tcp; version scan confirmed Jenkins 2.441.
- HTTP enumeration: Jetty 10.0.18 headers, /crumbIssuer/api/json (crumb 47c0f5...), /api/json (no jobs), /asynchPeople/api/json (users SYSTEM, admin). /script redirects to login.
- Gobuster mapped common Jenkins endpoints (login, api, script, manage, cli) with status codes (mostly 302/403/200).
- Login attempts using admin:admin required proper crumb/cookie sequence; still redirected to /loginError.

## Exploit / foothold
- Found CVE-2024-23897 Jenkins LFI exploit via searchsploit 51993; copied to /ctf-workspace.
- Created venv and installed requests through /ctf-workspace/venv/bin/pip (system pip disallowed).
- Exploit usage: /ctf-workspace/venv/bin/python /ctf-workspace/51993.py -u http://192.168.5.1:8080 -p
- /etc/passwd read confirmed remote file access.
- /root/flag.txt yields jrRxbTOlMog1E0I6 (not FLAG{...}; base64 decode -> binary).
- Attempts to read /root/flag, /home/jenkins/flag.txt, /var/jenkins_home/flag.txt, /flag all returned "File not found."
- /proc/1/environ revealed Jenkins env vars (HOME=/root, JENKINS_HOME=/var/jenkins_home).

## Failed attempts / constraints
- Initial Gobuster wordlist path missing; switched to raft-medium from seclists.
- Direct pip install failed due to externally-managed environment; resolved via venv.
- Curl commands with raw parentheses (XPath) needed URL-encoding.
- Jenkins login requires crumb tied to session; admin:admin still failed.

## Unexplored surface (*untried*)
- Port 50000/tcp not probed beyond nmap; potential JNLP/agent interface.
- Jenkins CLI not used beyond exploit; authenticated script console unreachable.
- Directory listing not viable due to LFI restrictions.

## Considerations for next agent
Flag search via LFI ongoing--documented paths produced either binary noise or "File not found"; focus on harvesting other filesystem locations or leveraging Jenkins login via discovered credentials; watch out for XPath brackets in curl (URL-encode) and reuse /tmp/jenkins_cookie for crumbs.
```

## 3. 監督が relay を受け取った時

```
relay 受信: {AGENT_ID} from {HOST}
足場: {確立済み / 未確立}
権限: {低権限 / admin / root}
未探索: {N} 件
判断: {同じモデルで継続 / 異なるモデルで再割当 / dead-end / 別ホスト優先}
→ テンプレート 1 で次のサブエージェントに指示を生成
```
