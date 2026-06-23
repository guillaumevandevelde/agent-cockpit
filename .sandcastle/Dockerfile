FROM node:22-slim AS builder

# Metadata
LABEL maintainer="Claude Cockpit"
LABEL description="Sandcastle agent container for isolated AI coding"

# Security: versie pinnen via apt
ARG GIT_VERSION=1:2.39.*
ARG CURL_VERSION=7.88.*
ARG JQ_VERSION=1.6.*

# Install system dependencies met versie pinning
RUN apt-get update && apt-get install -y --no-install-recommends \
    git=${GIT_VERSION} \
    curl=${CURL_VERSION} \
    jq=${JQ_VERSION} \
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

# Security: niet-root user aanmaken
RUN groupadd -r agent && useradd -r -g agent -m -s /bin/bash agent \
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