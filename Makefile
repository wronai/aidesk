# Proxeen Assistant - Makefile
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

.PHONY: help setup setup-backend setup-overlay env install install-system-deps diag diagnostics run run-backend run-overlay stop clean test test-setup test-units test-e2e status logs

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
	@set +e; \
	$(PYTHON) $(BACKEND_DIR)/server.py & \
	BACKEND_PID=$$!; \
	cleanup() { \
		if kill -0 $$BACKEND_PID 2>/dev/null; then \
			kill -INT $$BACKEND_PID 2>/dev/null || true; \
			for _ in 1 2 3 4 5 6 7 8 9 10; do \
				kill -0 $$BACKEND_PID 2>/dev/null || break; \
				sleep 0.2; \
			done; \
			if kill -0 $$BACKEND_PID 2>/dev/null; then \
				kill -KILL $$BACKEND_PID 2>/dev/null || true; \
			fi; \
			wait $$BACKEND_PID 2>/dev/null || true; \
		fi; \
	}; \
	trap cleanup EXIT; \
	trap 'cleanup; exit 0' INT TERM; \
	sleep 2; \
	echo "🌐 Opening browser windows..."; \
	xdg-open http://127.0.0.1:$(PORT)/config/ui 2>/dev/null || open http://127.0.0.1:$(PORT)/config/ui 2>/dev/null || true; \
	xdg-open http://127.0.0.1:$(PORT)/browser 2>/dev/null || open http://127.0.0.1:$(PORT)/browser 2>/dev/null || true; \
	echo "🖥️  Starting overlay..."; \
	$(NPM) start --prefix $(OVERLAY_DIR); \
	STATUS=$$?; \
	if [ $$STATUS -ne 0 ] && [ $$STATUS -ne 130 ] && [ $$STATUS -ne 143 ]; then \
		exit $$STATUS; \
	fi

run-backend: stop ## Run backend only
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "❌ backend/.env not found. Run 'make setup' first."; exit 1; \
	fi
	$(PYTHON) $(BACKEND_DIR)/server.py

run-overlay: ## Run overlay only
	$(NPM) start --prefix $(OVERLAY_DIR)

# ---- Utilities ----

install: setup-backend setup-overlay env ## Install all dependencies (python + node + system) and run diagnostics
	$(PYTHON) -m pip install -r $(BACKEND_DIR)/requirements.txt --upgrade
	$(MAKE) install-system-deps
	$(MAKE) diagnostics

install-system-deps: ## Install Linux system dependencies (OCR/STT/TTS/window tools)
	@if command -v apt-get >/dev/null 2>&1; then \
		echo "📦 Installing system dependencies (APT)..."; \
		sudo apt update; \
		sudo apt install -y \
			xdotool xprop xrandr wmctrl \
			tesseract-ocr tesseract-ocr-pol \
			libportaudio2 portaudio19-dev \
			libttspico-utils \
			speech-dispatcher speech-dispatcher-pico speech-dispatcher-festival speech-dispatcher-flite speech-dispatcher-rhvoice \
			rhvoice rhvoice-polish rhvoice-english \
			festival festvox-kallpc16k \
			flite espeak-ng alsa-utils ffmpeg; \
	else \
		echo "⚠️ apt-get not found. Install system dependencies manually (see INSTALL.md)."; \
	fi

diag: ## Run full system diagnostics (setup + preflight)
	@echo "🔎 Running setup diagnostics..."
	@set +e; \
	$(PYTHON) $(BACKEND_DIR)/test_setup.py; SETUP_RC=$$?; \
	echo ""; \
	echo "🔎 Running preflight diagnostics..."; \
	$(PYTHON) $(BACKEND_DIR)/preflight.py; PREFLIGHT_RC=$$?; \
	echo ""; \
	if [ $$SETUP_RC -eq 0 ] && [ $$PREFLIGHT_RC -eq 0 ]; then \
		echo "✅ System diagnostics passed"; \
		exit 0; \
	fi; \
	echo "❌ System diagnostics failed"; \
	echo "   - setup checks exit code: $$SETUP_RC"; \
	echo "   - preflight checks exit code: $$PREFLIGHT_RC"; \
	echo "   Review backend/.env, API keys, and missing dependencies shown above."; \
	exit 1

diagnostics: diag ## Backward-compatible alias for full diagnostics

test: test-units test-e2e ## Run all tests (unit + e2e)

test-setup: diagnostics ## Run backend setup diagnostics

test-units: ## Run unit tests (fast, no server)
	$(PYTHON) -m pytest $(BACKEND_DIR)/tests/test_units.py -v

test-e2e: ## Run e2e API tests (with lifespan)
	$(PYTHON) -m pytest $(BACKEND_DIR)/tests/test_e2e.py -v

status: ## Check if backend is running
	@curl -s http://127.0.0.1:$(PORT)/health 2>/dev/null && echo "" || echo "❌ Backend not running on port $(PORT)"

stop: ## Stop backend and overlay applications
	@echo "🛑 Stopping AIDesk..."
	@-pkill -9 -f "wayland_screencast[.]py" 2>/dev/null && echo "  ✓ Wayland screencast stopped" || true
	@-pkill -9 -f "python.*backend/server[.]py" 2>/dev/null && echo "  ✓ Backend stopped" || true
	@ps aux | grep 'overlay/node_modules' | grep -v grep | grep -v make | awk '{print $$2}' | xargs -r kill -9 2>/dev/null && echo "  ✓ Overlay processes stopped" || true
	@sleep 0.3
	@-fuser -k $(PORT)/tcp 2>/dev/null && echo "  ✓ Port $(PORT) freed" || true
	@echo "🛑 AIDesk stopped"

clean: ## Remove venv and node_modules
	rm -rf $(VENV_DIR)
	rm -rf $(OVERLAY_DIR)/node_modules

logs: ## Tail backend logs
	@tail -f logs/assistant.log 2>/dev/null || echo "No log file yet"
