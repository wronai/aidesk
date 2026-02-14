#!/bin/bash
# Proxeen Assistant - Startup Script (Linux/macOS)

echo "==================================="
echo "Proxeen Assistant"
echo "==================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f "backend/.env" ]; then
    echo -e "${YELLOW}Warning: backend/.env not found${NC}"
    echo "Copying .env.example to .env..."
    cp backend/.env.example backend/.env
    echo -e "${RED}Please edit backend/.env with your API keys before running!${NC}"
    echo "nano backend/.env"
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 not found${NC}"
    echo "Please install Python 3.11 or higher"
    exit 1
fi

# Check Node.js
if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found${NC}"
    echo "Please install Node.js 18 or higher"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python found: $(python3 --version)"
echo -e "${GREEN}✓${NC} Node.js found: $(node --version)"
echo ""

# Install Python dependencies
if [ ! -d "backend/venv" ]; then
    echo "Creating Python virtual environment..."
    cd backend
    python3 -m venv venv
    source venv/bin/activate
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
    cd ..
else
    echo "Activating Python virtual environment..."
    cd backend
    source venv/bin/activate
    cd ..
fi

# Install Node dependencies
if [ ! -d "overlay/node_modules" ]; then
    echo "Installing Node.js dependencies..."
    cd overlay
    npm install
    cd ..
fi

echo ""
echo "==================================="
echo "Starting Proxeen Assistant..."
echo "==================================="
echo ""

# Start backend in background
echo "Starting backend server..."
cd backend
python server.py &
BACKEND_PID=$!
cd ..

# Wait for backend to be ready
echo "Waiting for backend to be ready..."
sleep 3

# Start overlay
echo "Starting overlay..."
cd overlay
npm start &
OVERLAY_PID=$!
cd ..

echo ""
echo -e "${GREEN}✓ Proxeen Assistant is running!${NC}"
echo ""
echo "Backend PID: $BACKEND_PID"
echo "Overlay PID: $OVERLAY_PID"
echo ""
echo "Keyboard shortcuts:"
echo "  Ctrl+Shift+A - Toggle overlay"
echo "  Ctrl+Shift+Q - Quit"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Wait for user interrupt
wait
