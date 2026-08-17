#!/usr/bin/env python3
"""
moro-agent — レポート生成

findings.json → Markdown

使い方:
  python3 scripts/gen_report.py
  python3 scripts/gen_report.py --output report.md
"""
import json, argparse, os
from datetime import datetime

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")

def load(name):
    with open(os.path.join(STATE_DIR, name)) as f:
        return json.load(f)

def generate():
    scope = load("scope.json")
    findings = load("findings.json")
    creds = load("creds.json")
    hosts = load("hosts.json")

    lines = []
    lines.append(f"# Penetration Test Report")
    lines.append(f"Project: {scope.get('project', 'N/A')}")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")

    # Group findings by host
    by_host = {}
    for f in findings["findings"]:
        by_host.setdefault(f["host"], []).append(f)

    lines.append("## Findings\n")
    for ip, host_findings in by_host.items():
        h = hosts["hosts"].get(ip, {})
        services = ", ".join(h.get("services", []))
        lines.append(f"### {ip}")
        if services:
            lines.append(f"Services: {services}")
        lines.append("")

        for finding in sorted(host_findings, key=lambda f: f.get("step", 0)):
            lines.append(f"#### {finding['heading']}")
            if finding.get("narrative"):
                lines.append(finding["narrative"])
                lines.append("")
            if finding.get("commands"):
                lines.append("```")
                for cmd in finding["commands"]:
                    lines.append(cmd)
                lines.append("```")
                lines.append("")
            if finding.get("output_summary"):
                lines.append(f"**Output:** {finding['output_summary']}")
                lines.append("")
            if finding.get("screenshot"):
                lines.append(f"![{finding['heading']}]({finding['screenshot']})")
                lines.append("")

    # Credentials
    if creds["credentials"]:
        lines.append("## Credentials\n")
        lines.append("| User | Type | Source |")
        lines.append("|---|---|---|")
        for c in creds["credentials"]:
            lines.append(f"| {c['user']} | {c.get('secret_type','?')} | {c['source'][:40]} |")
        lines.append("")

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="report.md")
    args = parser.parse_args()

    md = generate()
    with open(args.output, "w") as f:
        f.write(md)
    print(f"Generated: {args.output}")

if __name__ == "__main__":
    main()
