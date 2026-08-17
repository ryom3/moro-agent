# AD Attack Playbook — 窃取・リレー系を優先するレシピ集

> このドキュメントは「推測・スプレー先行」をやめ、「窃取・リレー・証明書攻撃」を
> 優先するためのリファレンス。Active Directory / Windows ターゲットの定石。

## 1. 背景: なぜ推測を避けるべきか

- スプレー/ブルートフォースはロックアウト・アラートを誘発し、ノイズが大きい。
- 正解パスにパスワード「推測」が 1 回も要らないボックスが存在する。
  cred は「推測」ではなく「窃取（設定ファイル・メモリ・レスポンスから実値回収）」と
  「リレー（認証の横流し）」で得るのが王道。

## 2. 認証を要求してくる主体は「リレー対象」

- HTTP エンドポイントや vhost が **401 + WWW-Authenticate: NTLM** を返す、あるいは
  サービスが「URL を取得しに来る」「SMB に接続する」挙動がある場合、
  それは「cred 待ち」ではなく「認証を横流しできる対象」。
- Windows サービスが URL を fetch しに来るなら、**200 OK を返すのではなく
  NTLM を挑発してハッシュをキャプチャ / リレー**できる。

## 3. healthcheck / webhook / 内部 fetcher → NTLM リレー（よくある勝ち筋）

MQTT の retained メッセージ、webhook 設定、ヘルスチェック URL など、
「内部の何かが外部 URL を取得しに来る」経路を見つけたら、以下を試す。

1. 監視しているのが**既存の監視点**（内部チェッカーが実際に読むトピック/設定）か確認。
   （新規のテスト用トピックに plant しても、内部は自分のトピックしか読まない → 永遠に陰性）
2. `sudo responder -I <interface>` を裏で常時起動。NTLM ハッシュを拾う。
3. `ntlmrelayx` で拾った認証を別サービス（SMB/HTTP）へリレーしてログイン。
   ```
   sudo impacket-ntlmrelayx -t <target-host> -smb2support
   ```
4. 取得した権限で内部アプリ/ファイルサービスを閲覧し、実値 cred（KeePass 等）を回収。

## 4. AD 攻撃のコアツール群（覚えておく）

| ツール | 用途 |
|---|---|
| `responder` | ネットワーク上の LLMNR/NBT-NS/mDNS ブロードキャスト + HTTP/SMB への NTLM 挑発 |
| `ntlmrelayx` (impacket) | キャプチャした NTLM 認証を別ホストへリレー |
| `coercer` | PetitPotam 等で対象マシンの認証を強制（マシンアカウントの NTLM を吐かせる） |
| `certipy` | ADCS（証明書サービス）攻撃。ESC1〜ESC11 の構成ミスを列挙・悪用 |
| `secretsdump` (impacket) | リレーや DCSync でハッシュ・チケットを回収 |
| `kerbrute` | Kerberos ユーザー列挙（推測はここまでが限界・最終手段として） |

## 5. 判断フロー（優先順）

```
認証を要求 / fetch してくる経路を発見
  ├─ responder 常時起動 → ハッシュ capture
  ├─ ntlmrelayx でリレー → サービスアカウントとして侵入
  ├─ coercer + certipy (ESC 系) → DC 証明書 → secretsdump
  └─ 実値 cred 窃取 (設定ファイル / KeePass / メモリ)
※ スプレー・ブルートフォースはこれらが尽きた後の最終手段
```

## 6. 認可・レートの注意

- 上記は認可された検証環境（HTB / lab / RoE 内）でのみ実施。
- レスポンス・レートに敏感な本番系では、relay も最小限・宣言制で。
