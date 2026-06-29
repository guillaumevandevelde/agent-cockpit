#!/bin/bash
# Initial setup script
# Creates virtual environment, installs dependencies, initializes database

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Setting up Claude Cockpit..."

# Check Python version — require 3.11+ (StrEnum, match-statement, etc.)
PYTHON_CMD=""
for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_CMD="$candidate"
        break
    fi
done
if [ -z "$PYTHON_CMD" ]; then
    echo "Error: Python 3.11+ not found. Please install Python 3.11 or newer."
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo "Found Python $PYTHON_VERSION (using $PYTHON_CMD)"

# Reject a venv built with the wrong Python (e.g. system python3 = 3.10).
if [ -f "backend/venv/bin/python" ]; then
    VENV_VER=$("$PROJECT_ROOT/backend/venv/bin/python" --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    REQ_VER=$($PYTHON_CMD --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    if [ "$VENV_VER" != "$REQ_VER" ]; then
        echo "Existing venv uses Python $VENV_VER but we need $REQ_VER — recreating."
        $PYTHON_CMD -m venv --clear "$PROJECT_ROOT/backend/venv"
    fi
fi

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "Error: Node.js not found. Please install Node.js 18+."
    exit 1
fi

NODE_VERSION=$(node --version)
echo "Found Node.js $NODE_VERSION"

# Setup backend
echo ""
echo "Setting up backend..."
cd "$PROJECT_ROOT/backend"

if [ ! -f "venv/bin/activate" ]; then
    echo "Creating Python virtual environment..."
    rm -rf venv
    $PYTHON_CMD -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing Python dependencies..."
pip install -r requirements-dev.txt

echo "Installing backend Node.js dependencies..."
npm install

# Database schema is created automatically on first run by the FastAPI
# lifespan (Base.metadata.create_all); no migration step required.
echo "Backend setup complete!"

# Setup frontend
echo ""
echo "Setting up frontend..."
cd "$PROJECT_ROOT/frontend"

echo "Installing Node.js dependencies..."
npm install

echo "Frontend setup complete!"

# Setup documentation site
echo ""
echo "Setting up documentation site..."
cd "$PROJECT_ROOT/docs"

echo "Installing documentation dependencies..."
npm install

echo "Documentation setup complete!"

# Create required directories
echo ""
echo "Creating required directories..."
mkdir -p ~/.claude-registry/backups
# cockpit.sh writes supervisor.log here; create it now so WSL's mkdir -p on
# /mnt/c doesn't have to create the parent and child in a single call (WSL bug).
mkdir -p "$PROJECT_ROOT/logs"

echo ""
echo "Setup complete!"
echo ""
echo "To start development servers:"
echo "  ./scripts/cockpit.sh start   # Supervised background (recommended)"
echo "  ./scripts/dev.sh             # Attached / interactive"
