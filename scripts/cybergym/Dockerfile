# moro-agent — CyberGym 評価用エージェントコンテナ
#
# ビルド (フレームワークルートから):
#   docker build -t moro-agent:cybergym -f scripts/cybergym/Dockerfile .
#
# Claude Code CLI + tmux + 解析ツールチェーン を含む。
# シークレット (.env) はイメージに含めない — 実行時に環境変数で渡す。

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV NODE_VERSION=22

# --- 基本ツール + 解析チェーン ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git tmux bash-completion \
        python3 python3-pip python3-venv \
        build-essential gdb strace ltrace \
        zip unzip tar xz-utils file jq ripgrep \
        clang llvm pkg-config automake autoconf libtool \
        libssl-dev zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# --- Node.js (Claude Code CLI のため) ---
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    npm cache clean --force

# --- フレームワーク本体 (シークレット除外は .dockerignore で担保) ---
WORKDIR /opt/moro-agent
COPY config models.json CLAUDE-cybergym.md handoff_templates.md ./
COPY scripts/ ./scripts/
COPY mcp/ ./mcp/
COPY kb/README.md ./kb/README.md

# tmux 既定セッション名 (run.sh が要求)
ENV TMUX_SESSION=pentest
ENV FRAMEWORK_DIR=/opt/moro-agent

# エージェント作業ディレクトリ (タスクがバインドされる)
WORKDIR /workspace

# コンテナ内エントリポイント: tmux サーバを立ててからヘッドレス実行
COPY scripts/cybergym/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
