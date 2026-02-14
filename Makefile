# AI Desktop Assistant - Makefile
# ================================

SHELL       := /bin/bash
VENV_DIR    := venv
VENV_BIN    := $(VENV_DIR)/bin
PYTHON      := $(VENV_BIN)/python
PIP         := $(VENV_BIN)/pip
BACKEND_DIR := backend
OVERLAY_DIR := overlay

# Resolve npm via nvm if not in PATH
NVM_DIR     := $(HOME)/.nvm
NPM         := $(shell which npm 2>/dev/null || echo "source $(NVM_DIR)/nvm.sh && npm")

# Fix miniconda libstdc++ conflict with system portaudio/JACK
export LD_PRELOAD := /usr/lib/x86_64-linux-gnu/libstdc++.so.6

# Read PORT from .env (default 8000)
PORT := $(shell grep -s '^PORT=' $(BACKEND_DIR)/.env | cut -d= -f2 | tr -d ' ')
ifeq ($(PORT),)
  PORT := 8000
endif

.PHONY: help setup setup-backend setup-overlay env install run run-backend run-overlay stop clean test status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- Setup ----

setup: setup-backend setup-overlay env ## Full project setup (venv + npm + .env)
	@echo "\n✅ Setup complete. Edit backend/.env with your API keys, then run: make run"

setup-backend: $(VENV_DIR)/bin/activate ## Create venv and install Python deps
	$(PIP) install -r $(BACKEND_DIR)/requirements.txt

$(VENV_DIR)/bin/activate:
	python3 -m venv $(VENV_DIR)

setup-overlay: ## Install Node.js deps for overlay
	$(NPM) install --prefix $(OVERLAY_DIR)

env: ## Create .env from .env.example (if missing)
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		cp $(BACKEND_DIR)/.env.example $(BACKEND_DIR)/.env; \
		echo "📝 Created backend/.env from .env.example — edit it with your API keys"; \
	else \
		echo "ℹ️  backend/.env already exists"; \
	fi

# ---- Run ----

run: stop ## Run backend + overlay + open browser windows
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "❌ backend/.env not found. Run 'make setup' first."; exit 1; \
	fi
	@echo "🚀 Starting backend on port $(PORT)..."
	$(PYTHON) $(BACKEND_DIR)/server.py &
	@sleep 2
	@echo "🌐 Opening browser windows..."
	@xdg-open http://127.0.0.1:$(PORT)/browser 2>/dev/null || open http://127.0.0.1:$(PORT)/browser 2>/dev/null || true
	@xdg-open http://127.0.0.1:$(PORT)/status 2>/dev/null || open http://127.0.0.1:$(PORT)/status 2>/dev/null || true
	@echo "🖥️  Starting overlay..."
	$(NPM) start --prefix $(OVERLAY_DIR)

run-backend: stop ## Run backend only
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "❌ backend/.env not found. Run 'make setup' first."; exit 1; \
	fi
	$(PYTHON) $(BACKEND_DIR)/server.py

run-overlay: ## Run overlay only
	$(NPM) start --prefix $(OVERLAY_DIR)

# ---- Utilities ----

install: setup-backend ## Reinstall Python deps
	$(PYTHON) -m pip install -r $(BACKEND_DIR)/requirements.txt --upgrade

test: ## Run backend test_setup.py
	$(PYTHON) $(BACKEND_DIR)/test_setup.py

status: ## Check if backend is running
	@curl -s http://127.0.0.1:$(PORT)/health 2>/dev/null && echo "" || echo "❌ Backend not running on port $(PORT)"

stop: ## Stop backend and overlay applications
	@-fuser -k -9 $(PORT)/tcp 2>/dev/null && echo "🛑 Backend stopped (port $(PORT))" || true
	@-pkill -f "python.*backend/server.py" 2>/dev/null || true
	@-pkill -f "electron.*overlay" 2>/dev/null || true
	@echo "🛑 Overlay stopped"

clean: ## Remove venv and node_modules
	rm -rf $(VENV_DIR)
	rm -rf $(OVERLAY_DIR)/node_modules

logs: ## Tail backend logs
	@tail -f logs/assistant.log 2>/dev/null || echo "No log file yet"
