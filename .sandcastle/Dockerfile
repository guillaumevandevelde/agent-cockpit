FROM node:22-slim AS builder

# Metadata
LABEL maintainer="Claude Cockpit"
LABEL description="Sandcastle agent container for isolated AI coding"

# Use working mirror (deb.debian.org may be unreachable in some environments)
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && echo "deb http://ftp.be.debian.org/debian bookworm bookworm-updates main" > /etc/apt/sources.list \
    && echo "deb http://security.debian.org/debian-security bookworm-security main" >> /etc/apt/sources.list

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    jq \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install GitHub CLI - GPG key veilig ophalen
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Claude Code CLI - pin versie voor reproduceerbaarheid
ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force

# Cleanup npm cache
RUN rm -rf /root/.npm /tmp/*

# Security: niet-root user aanmaken (uid 1000 voor --userns=keep-id compatibiliteit)
RUN groupdel node 2>/dev/null; userdel node 2>/dev/null; true \
    && groupadd -g 1000 agent && useradd -u 1000 -g agent -m -s /bin/bash agent \
    && mkdir -p /home/agent/.config /home/agent/.local \
    && chown -R agent:agent /home/agent

# Wer用户 instellen
USER agent
WORKDIR /home/agent

# Environment variabelen
ENV HOME=/home/agent
ENV PATH=$HOME/.local/bin:$PATH
ENV NODE_ENV=production

# Security: stdin open houden voor interactieve sessies (maar niet vereist)
# STOPSIGNAL voor graceful shutdown
STOPSIGNAL SIGTERM

# Healthcheck - controleren of node en claude-code werken
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD node --version && claude --version || exit 1

# Default command (kan worden overschreven)
CMD ["bash"]