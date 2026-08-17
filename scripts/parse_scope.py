#!/usr/bin/env python3
"""
バグバウンティプログラムの HTML/テキストから scope.json を自動生成。

使い方:
  python3 scripts/parse_scope.py program.html
  python3 scripts/parse_scope.py program.html --output state/scope.json
  python3 scripts/parse_scope.py https://url/to/program   # URL も可 (要 requests)
"""
import re, json, argparse, sys
from pathlib import Path


def extract_text(html):
    """HTML からテキストを抽出"""
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', html)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def extract_domains(text):
    """ドメイン名を抽出"""
    # https://xxx.xxx.xxx パターン
    urls = re.findall(r'https?://([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})', text)
    # xxx.xxx.xxx パターン (URL でないもの)
    domains = re.findall(r'(?<!\w)([a-zA-Z0-9*._-]+\.(?:com|org|net|gov|io|es|eu|co\.uk))', text)
    return list(set(urls + domains))


def parse_program(text, raw_html=""):
    """プログラム情報を構造化"""
    scope = {
        "project": "",
        "platform": "",
        "targets": [],
        "out_of_scope": [],
        "rules_of_engagement": "",
        "required_headers": "",
        "rate_limit": "Max 1 req/sec (default)",
        "rewards": "",
    }

    lines = text.split('\n')
    all_domains = extract_domains(text)

    # プロジェクト名 (最初の意味のある行)
    for line in lines:
        line = line.strip()
        if len(line) > 10 and 'bug bounty' in line.lower():
            scope["project"] = line.split(' bug bounty')[0].strip()
            break

    # プラットフォーム検出
    if 'yeswehack' in text.lower():
        scope["platform"] = "YesWeHack"
    elif 'hackerone' in text.lower():
        scope["platform"] = "HackerOne"
    elif 'bugcrowd' in text.lower():
        scope["platform"] = "Bugcrowd"

    # Out of scope セクション
    out_of_scope_section = False
    in_scope_section = False
    for line in lines:
        line = line.strip()
        lower = line.lower()

        if 'out of scope' in lower or 'out-of-scope' in lower:
            out_of_scope_section = True
            in_scope_section = False
            continue

        if out_of_scope_section:
            domains = extract_domains(line)
            scope["out_of_scope"].extend(domains)
            if not domains and len(line) > 5:
                out_of_scope_section = False

    # In-scope = 全ドメイン - out_of_scope
    out_set = set(scope["out_of_scope"])
    scope["targets"] = [d for d in all_domains if d not in out_set
                        and not any(x in d for x in ['yeswehack', 'hackerone', 'bugcrowd', 'github'])]

    # User-Agent 要件 (raw HTML から検索 — タグ除去で消える場合がある)
    search_text = raw_html if raw_html else text
    ua_match = re.search(r'-BugBounty-[A-Za-z0-9_-]+', search_text)
    if ua_match:
        scope["required_headers"] = f"User-Agent must contain: {ua_match.group(0).strip()}"

    # ルール抽出
    rules = []
    rule_keywords = ['forbidden', 'strictly', 'do not', 'must not', 'prohibited',
                     'are not allowed', 'not eligible', 'avoid']
    for line in lines:
        line = line.strip()
        if any(kw in line.lower() for kw in rule_keywords) and len(line) > 20:
            rules.append(line)
    scope["rules_of_engagement"] = ' | '.join(rules[:10])

    return scope


def main():
    parser = argparse.ArgumentParser(description="Parse bug bounty program into scope.json")
    parser.add_argument("source", help="HTML file path or URL")
    parser.add_argument("--output", default="state/scope.json", help="Output path")
    args = parser.parse_args()

    source = args.source

    # URL or file
    if source.startswith('http'):
        try:
            import requests
            r = requests.get(source)
            raw_html = r.text
            text = extract_text(raw_html)
        except ImportError:
            print("pip install requests for URL support")
            sys.exit(1)
    else:
        with open(source, 'r', errors='replace') as f:
            raw_html = f.read()
        text = extract_text(raw_html)

    scope = parse_program(text, raw_html)

    # 出力
    print(json.dumps(scope, indent=2, ensure_ascii=False))

    # ファイルに保存
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(scope, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    main()
