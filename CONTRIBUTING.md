# Contributing to Agent Cockpit

Thanks for your interest in contributing! We welcome pull requests and issues from everyone.
By participating, you're expected to uphold our [Code of Conduct](./CODE_OF_CONDUCT.md).

## Getting Started

1. Fork the repo and clone it:
   ```bash
   git clone https://github.com/<your-username>/claude-cockpit.git
   cd claude-cockpit
   ```

2. Run the install script (requires Python 3.11+ and Node.js 18+):
   ```bash
   ./scripts/install.sh
   ```

3. Start the dev servers:
   ```bash
   ./scripts/dev.sh
   ```
   This starts the backend at http://localhost:8000 and frontend at http://localhost:5173.

## Code Style

- **Backend**: Python with type hints, async/await, pydantic models for validation
- **Frontend**: TypeScript strict mode, ESLint (`npm run lint` in `frontend/`), Tailwind CSS + shadcn/ui

## Submitting Changes

1. Create a branch for your change
2. Make your changes and test them locally
3. Run `cd backend && ruff check app tests && pytest -q`
4. Run `cd frontend && npm run lint && npm test && npm run build`
5. Open a pull request with a clear description of what you changed and why

## Reporting Issues

Found a bug or have a feature idea? [Open an issue](https://github.com/guillaumevandevelde/claude-cockpit/issues) and include:
- What you expected to happen
- What actually happened
- Steps to reproduce (if applicable)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
