#!/usr/bin/env python3
"""
Proxeen Assistant - Setup Verification Script
Tests all components and API connections.
"""
import sys
import os

# Colors for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def check(name, passed, details=""):
    """Print check result."""
    status = f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"
    print(f"{status} {name}")
    if details:
        print(f"  {details}")
    return passed

def _is_real_key(value: str, placeholder_prefix: str = "your_") -> bool:
    """Return True if value looks like a real API key (not empty/placeholder)."""
    return bool(value and not value.startswith(placeholder_prefix))


def _check_python_version() -> bool:
    print(f"{YELLOW}[1/12] Checking Python version...{RESET}")
    v = sys.version_info
    return check(
        "Python version",
        v.major == 3 and v.minor >= 11,
        f"Found: {v.major}.{v.minor}.{v.micro} (Need: 3.11+)",
    )


def _check_packages() -> bool:
    print(f"\n{YELLOW}[2/12] Checking Python packages...{RESET}")
    packages = [
        "fastapi", "uvicorn", "mss", "imagehash", "PIL",
        "google.generativeai", "openai", "anthropic",
        "deepgram", "sounddevice", "structlog", "nfo", "loguru",
    ]
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
            check(f"Package: {pkg}", True)
        except ImportError:
            check(f"Package: {pkg}", False, "Not installed")
            missing.append(pkg)
    if missing:
        print(f"\n  {RED}Missing packages. Install with:{RESET}")
        print(f"  pip install -r backend/requirements.txt")
    return len(missing) == 0


def _check_env_file(env_path: str) -> bool:
    print(f"\n{YELLOW}[3/12] Checking .env file...{RESET}")
    exists = os.path.exists(env_path)
    check(".env file exists", exists)
    if not exists:
        print(f"  {RED}Create .env from template:{RESET}")
        print(f"  cp backend/.env.example backend/.env")
    return exists


def _load_env(env_path: str) -> bool:
    print(f"\n{YELLOW}[4/12] Loading environment variables...{RESET}")
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        check("Load .env", True)
        return True
    except Exception as e:
        check("Load .env", False, str(e))
        return False


def _check_api_keys() -> bool:
    print(f"\n{YELLOW}[5/12] Checking API keys...{RESET}")
    ok = True
    ok &= check("Deepgram API key", _is_real_key(os.getenv("DEEPGRAM_API_KEY", "")), "Required for STT")
    ok &= check("Gemini API key", _is_real_key(os.getenv("GEMINI_API_KEY", "")), "Required for Vision (recommended provider)")
    check("OpenAI API key", _is_real_key(os.getenv("OPENAI_API_KEY", "")), "Optional")
    check("Claude API key", _is_real_key(os.getenv("ANTHROPIC_API_KEY", "")), "Optional")
    return ok


def _check_screen_capture() -> bool:
    print(f"\n{YELLOW}[6/12] Testing screen capture...{RESET}")
    try:
        import mss
        sct = mss.mss()
        img = sct.grab(sct.monitors[1])
        check("Screen capture", True, f"Resolution: {img.width}x{img.height}")
        return True
    except Exception as e:
        check("Screen capture", False, str(e))
        return False


def _check_image_processing() -> bool:
    print(f"\n{YELLOW}[7/12] Testing image processing...{RESET}")
    try:
        from PIL import Image
        import imagehash
        test_img = Image.new('RGB', (100, 100), color='red')
        hash_val = imagehash.phash(test_img)
        check("Image hashing", True, f"Hash: {hash_val}")
        return True
    except Exception as e:
        check("Image hashing", False, str(e))
        return False


def _check_audio_devices() -> bool:
    print(f"\n{YELLOW}[8/12] Checking audio devices...{RESET}")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        check("Audio input devices", len(input_devices) > 0, f"Found {len(input_devices)} microphone(s)")
        return len(input_devices) > 0
    except Exception as e:
        check("Audio devices", False, str(e))
        return False


def _check_api_client(name: str, step: str, key_env: str, factory) -> bool:
    print(f"\n{YELLOW}[{step}] Testing {name} API...{RESET}")
    key = os.getenv(key_env, "")
    if not _is_real_key(key):
        check(f"{name} API", False, "API key not configured")
        return False
    try:
        factory(key)
        check(f"{name} client", True, "API key format valid")
        return True
    except Exception as e:
        check(f"{name} client", False, str(e))
        return False


def _check_system_tools() -> bool:
    import shutil
    print(f"\n{YELLOW}[11/12] Checking system tools (window awareness)...{RESET}")
    tools = {
        "xdotool": ("Active window detection", True),
        "xprop": ("WM_CLASS detection", True),
        "xrandr": ("Monitor detection", True),
        "wmctrl": ("Window listing (optional)", False),
    }
    ok = True
    for tool, (desc, required) in tools.items():
        found = shutil.which(tool) is not None
        check(f"Tool: {tool}", found, desc + (" — installed" if found else " — not found"))
        if not found and required:
            ok = False
    return ok


def _check_backend_modules() -> bool:
    print(f"\n{YELLOW}[12/12] Checking backend modules...{RESET}")
    modules = [
        ("window_aware", "Window awareness"),
        ("app_profiles", "Per-app analysis profiles"),
        ("shell_agent", "Shell agent"),
        ("process_scanner", "Process scanner"),
        ("window_cropper", "Window cropper"),
    ]
    for mod, desc in modules:
        try:
            __import__(mod)
            check(f"Module: {mod}", True, desc)
        except Exception as e:
            check(f"Module: {mod}", False, f"{desc} — {e}")
    return True


def main():
    print(f"\n{BLUE}{'='*50}")
    print("Proxeen Assistant - Setup Verification")
    print(f"{'='*50}{RESET}\n")

    ok = True
    ok &= _check_python_version()
    ok &= _check_packages()

    env_path = "backend/.env"
    if not _check_env_file(env_path):
        return False
    if not _load_env(env_path):
        return False

    ok &= _check_api_keys()
    ok &= _check_screen_capture()
    ok &= _check_image_processing()
    ok &= _check_audio_devices()

    def _deepgram_factory(key):
        from deepgram import DeepgramClient
        DeepgramClient(key)

    def _gemini_factory(key):
        import google.generativeai as genai
        genai.configure(api_key=key)
        genai.GenerativeModel("gemini-2.0-flash-exp")

    ok &= _check_api_client("Deepgram", "9/12", "DEEPGRAM_API_KEY", _deepgram_factory)
    ok &= _check_api_client("Gemini", "10/12", "GEMINI_API_KEY", _gemini_factory)
    ok &= _check_system_tools()
    ok &= _check_backend_modules()

    # Summary
    print(f"\n{BLUE}{'='*50}{RESET}")
    if ok:
        print(f"{GREEN}✓ All checks passed! Ready to run.{RESET}")
        print(f"\nStart the assistant with:")
        print(f"  ./start.sh   (Linux/macOS)")
        print(f"  start.bat    (Windows)")
        return True
    else:
        print(f"{RED}✗ Some checks failed. Please fix the issues above.{RESET}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
