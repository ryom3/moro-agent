#!/usr/bin/env python3
"""
Notion → ナレッジベース同期

Notion API 経由でページを取得し、Markdown に変換して kb/ に保存 → ChromaDB にインジェスト。

初回セットアップ:
  1. https://www.notion.so/profile/integrations で Integration を作成
  2. API トークンを .env に追加: NOTION_API_KEY=ntn_xxxxx
  3. KB に入れたい Notion ページを開き、右上「...」→「接続」→ 作成した Integration を追加
  4. python3 kb/notion_sync.py

使い方:
  python3 kb/notion_sync.py                    # 共有済み全ページを同期
  python3 kb/notion_sync.py --list             # 共有済みページ一覧
  python3 kb/notion_sync.py --select           # 対話式で選択
  python3 kb/notion_sync.py --ingest           # 同期 + ChromaDB インジェスト
"""
import os, json, argparse, re
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("pip install requests")
    exit(1)

KB_DIR = Path(__file__).parent
EXPORT_DIR = KB_DIR / "notion-export"
ENV_FILE = KB_DIR.parent / ".env"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def get_token():
    # .env から読む
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                if line.strip().startswith("NOTION_API_KEY="):
                    return line.strip().split("=", 1)[1]
    return os.environ.get("NOTION_API_KEY", "")


def api(method, path, token, body=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API}{path}"
    if method == "GET":
        r = requests.get(url, headers=headers)
    else:
        r = requests.post(url, headers=headers, json=body or {})
    r.raise_for_status()
    return r.json()


def search_pages(token):
    """Integration に共有されている全ページを取得"""
    pages = []
    body = {"page_size": 100}
    while True:
        result = api("POST", "/search", token, body)
        for item in result.get("results", []):
            if item["object"] == "page":
                title = ""
                props = item.get("properties", {})
                for p in props.values():
                    if p.get("type") == "title":
                        for t in p.get("title", []):
                            title += t.get("plain_text", "")
                if not title:
                    title = item["id"][:8]
                pages.append({
                    "id": item["id"],
                    "title": title,
                    "last_edited": item.get("last_edited_time", ""),
                    "url": item.get("url", ""),
                })
        if not result.get("has_more"):
            break
        body["start_cursor"] = result["next_cursor"]
    return pages


def get_page_blocks(token, page_id):
    """ページの全ブロックを取得"""
    blocks = []
    path = f"/blocks/{page_id}/children?page_size=100"
    while True:
        result = api("GET", path, token)
        blocks.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        path = f"/blocks/{page_id}/children?page_size=100&start_cursor={result['next_cursor']}"
    return blocks


def blocks_to_markdown(blocks, token, depth=0):
    """Notion ブロックを Markdown に変換"""
    md = ""
    for block in blocks:
        btype = block.get("type", "")
        content = block.get(btype, {})

        # テキスト系
        text = ""
        for rt in content.get("rich_text", []):
            text += rt.get("plain_text", "")

        if btype == "paragraph":
            md += text + "\n\n"
        elif btype.startswith("heading_"):
            level = int(btype[-1])
            md += "#" * level + " " + text + "\n\n"
        elif btype == "bulleted_list_item":
            md += "- " + text + "\n"
        elif btype == "numbered_list_item":
            md += "1. " + text + "\n"
        elif btype == "code":
            lang = content.get("language", "")
            md += f"```{lang}\n{text}\n```\n\n"
        elif btype == "quote":
            md += "> " + text + "\n\n"
        elif btype == "callout":
            md += "> " + text + "\n\n"
        elif btype == "to_do":
            checked = "x" if content.get("checked") else " "
            md += f"- [{checked}] {text}\n"
        elif btype == "toggle":
            md += f"<details><summary>{text}</summary>\n\n"
        elif btype == "divider":
            md += "---\n\n"
        elif btype == "table":
            # テーブルは子ブロックで処理
            pass

        # 子ブロックがあれば再帰
        if block.get("has_children") and btype not in ["child_page", "child_database"]:
            try:
                children = get_page_blocks(token, block["id"])
                md += blocks_to_markdown(children, token, depth + 1)
            except:
                pass

    return md


def get_child_pages(token, page_id):
    """子ページを再帰的に取得"""
    children = []
    blocks = get_page_blocks(token, page_id)
    for block in blocks:
        if block.get("type") == "child_page":
            child_id = block["id"]
            child_title = block.get("child_page", {}).get("title", child_id[:8])
            children.append({"id": child_id, "title": child_title})
            # 再帰
            children.extend(get_child_pages(token, child_id))
    return children


def sync_page(token, page, export_dir):
    """1ページを Markdown ファイルとして保存"""
    blocks = get_page_blocks(token, page["id"])
    md = f"# {page['title']}\n\n"
    md += blocks_to_markdown(blocks, token)

    # ファイル名をサニタイズ
    safe_name = re.sub(r'[^\w\s-]', '', page["title"]).strip()[:80]
    safe_name = re.sub(r'[-\s]+', '-', safe_name)
    if not safe_name:
        safe_name = page["id"][:8]

    short_id = page["id"][:8]
    filepath = export_dir / f"{safe_name}_{short_id}.md"
    with open(filepath, 'w') as f:
        f.write(md)

    return filepath


def main():
    parser = argparse.ArgumentParser(description="Notion → KB sync")
    parser.add_argument("--list", action="store_true", help="共有済みページ一覧")
    parser.add_argument("--select", action="store_true", help="対話式で選択して同期")
    parser.add_argument("--ingest", action="store_true", help="同期後に ChromaDB へインジェスト")
    parser.add_argument("--diff", action="store_true", help="前回同期以降に更新されたページのみ取得")
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("NOTION_API_KEY が未設定。.env に追加してください:")
        print("  NOTION_API_KEY=ntn_xxxxx")
        return

    print("Notion ページを検索中...")
    pages = search_pages(token)
    print(f"共有済み: {len(pages)} ページ\n")

    if args.list:
        for i, p in enumerate(pages):
            print(f"  {i+1:3d}. {p['title'][:60]}")
        return

    # 同期対象を決定
    if args.select:
        for i, p in enumerate(pages):
            print(f"  {i+1:3d}. {p['title'][:60]}")
        print()
        selection = input("同期するページ番号 (カンマ区切り, 'all' で全部): ")
        if selection.strip().lower() == "all":
            targets = pages
        else:
            indices = [int(x.strip()) - 1 for x in selection.split(",") if x.strip().isdigit()]
            targets = [pages[i] for i in indices if 0 <= i < len(pages)]
    else:
        targets = pages

    if not targets:
        print("同期対象がありません")
        return

    # 差分フィルタ
    last_sync_file = EXPORT_DIR / ".last_sync"
    if args.diff and last_sync_file.exists():
        with open(last_sync_file) as f:
            last_sync = f.read().strip()
        before_count = len(targets)
        targets = [p for p in targets if p.get("last_edited", "") > last_sync]
        print(f"差分: {before_count} → {len(targets)} ページ (前回同期: {last_sync})")
        if not targets:
            print("更新なし")
            return

    # エクスポート
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"同期中: {len(targets)} ページ → {EXPORT_DIR}/")

    print(f"同期開始: {len(targets)} ページ")

    for i, page in enumerate(targets):
        try:
            filepath = sync_page(token, page, EXPORT_DIR)
            size = filepath.stat().st_size
            if size > 20:
                print(f"  [{i+1}/{len(targets)}] {page['title'][:50]} → {filepath.name} ({size} bytes)")
            else:
                print(f"  [{i+1}/{len(targets)}] {page['title'][:50]} → SKIP (empty)")
                filepath.unlink()  # 空ファイルは削除
        except Exception as e:
            print(f"  [{i+1}/{len(targets)}] {page['title'][:50]} → ERROR: {e}")

    # 同期時刻を記録
    with open(EXPORT_DIR / ".last_sync", "w") as f:
        f.write(datetime.utcnow().isoformat())

    print(f"\nExported to: {EXPORT_DIR}/")

    # インジェスト
    if args.ingest:
        print("\nChromaDB にインジェスト中...")
        import subprocess
        result = subprocess.run(
            ["python3", str(KB_DIR / "kb.py"), "ingest", "--source", str(EXPORT_DIR), "--tag", "notion"],
            capture_output=True, text=True
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)


if __name__ == "__main__":
    main()
