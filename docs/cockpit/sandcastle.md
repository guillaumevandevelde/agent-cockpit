---
title: "Sandcastle Integration"
type: reference
status: active
---

# Sandcastle Integration

Sandcastle is integrated into Claude Cockpit for running AI coding agents in isolated sandboxes (Docker, Podman, or Vercel).

## Features

- **Per-project configuration**: Enable/disable sandcastle per project
- **Multiple sandbox providers**: Docker, Podman, Vercel, or no sandbox
- **Multiple agent providers**: Claude Code, Codex, Cursor, Pi, OpenCode, Copilot
- **Parallel execution**: Run multiple agents simultaneously
- **Kanban integration**: Automatically dispatch kanban cards via sandcastle
- **Scheduled messages**: Trigger sandcastle runs via scheduled messages
- **Real-time monitoring**: View run statistics and logs

## Configuration

### Backend

The sandcastle configuration is stored in the `sandcastle_configs` table:

```python
class SandcastleConfig(Base):
    __tablename__ = "sandcastle_configs"
    
    id: int
    project_path: str  # unique
    enabled: bool
    sandbox_provider: str  # docker | podman | vercel | no-sandbox
    agent_provider: str  # claude-code | codex | cursor | pi | opencode | copilot
    model: str | None
    branch_strategy: str  # head | merge-to-head | branch
    docker_image: str | None
    max_iterations: int
    idle_timeout_seconds: int
    permission_mode: str
```

### Frontend

Navigate to **Sandcastle** in the sidebar to configure:

1. **Enable/Disable**: Toggle sandcastle for the current project
2. **Sandbox Provider**: Select Docker, Podman, Vercel, or No Sandbox
3. **Agent Provider**: Select Claude Code, Codex, Cursor, etc.
4. **Branch Strategy**: Choose how branches are managed

## API Endpoints

### Configuration

- `GET /api/v1/sandcastle/config?project_path=...` - Get config
- `PUT /api/v1/sandcastle/config?project_path=...` - Create/update config
- `PATCH /api/v1/sandcastle/config/{id}/toggle` - Toggle enabled status

### Runs

- `POST /api/v1/sandcastle/runs?project_path=...` - Start a run
- `POST /api/v1/sandcastle/runs/parallel?project_path=...` - Start parallel runs
- `GET /api/v1/sandcastle/runs?project_path=...` - List runs
- `GET /api/v1/sandcastle/runs/{id}` - Get run details
- `GET /api/v1/sandcastle/runs/{id}/logs` - Get run logs
- `GET /api/v1/sandcastle/runs/{id}/stream` - Stream logs (SSE)
- `DELETE /api/v1/sandcastle/runs/{id}` - Cancel run

### System

- `GET /api/v1/sandcastle/health` - Check health
- `POST /api/v1/sandcastle/build-image` - Build Docker image
- `GET /api/v1/sandcastle/stats` - Get run statistics

## Usage

### Starting a Run

1. Navigate to **Sandcastle** in the sidebar
2. Ensure sandcastle is enabled for the current project
3. Click **New Run**
4. Enter a prompt describing the task
5. Optionally specify a branch name
6. Click **Start Run**

### Parallel Runs

1. Click **Parallel Runs**
2. Add multiple prompts
3. Click **Start Runs**

### Kanban Integration

Sandcastle can be used as a transport for kanban dispatch:

1. Enable sandcastle for a project
2. Kanban cards will automatically use sandcastle when enabled
3. The transport is selected based on project configuration

### Scheduled Messages

Create scheduled messages with `target_kind=sandcastle`:

1. Go to **Scheduled Messages**
2. Create a new message
3. Select **Sandcastle run** as the target
4. The message will trigger a sandcastle run at the scheduled time

## Docker Setup

### Building the Image

1. Navigate to **Sandcastle**
2. Click **Build Docker Image** in the health status section
3. Wait for the build to complete

### Custom Dockerfile

The default Dockerfile is at `.sandcastle/Dockerfile`. Customize it to add project-specific dependencies.

## Monitoring

### Health Status

The health status section shows:
- Node.js availability
- Docker/Podman availability
- Docker image status
- npm dependencies status

### Run Statistics

The statistics section shows:
- Total runs
- Completed runs
- Active runs
- Runs in the last 24 hours

### Logs

View logs for any run by expanding the run card in the runs list.

## Troubleshooting

### Run Fails Immediately

1. Check health status for missing prerequisites
2. Ensure Docker/Podman is running
3. Build the Docker image if not built

### Run Times Out

1. Increase `idle_timeout_seconds` in the config
2. Check if the agent is stuck
3. View logs for more details

### Kanban Cards Not Dispatching

1. Ensure sandcastle is enabled for the project
2. Check that the sandcastle config exists
3. Verify Docker/Podman is available