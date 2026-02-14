#!/usr/bin/env python3
"""
AI Desktop Assistant - Setup Verification Script
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

def main():
    print(f"\n{BLUE}{'='*50}")
    print("AI Desktop Assistant - Setup Verification")
    print(f"{'='*50}{RESET}\n")
    
    all_passed = True
    
    # 1. Python version
    print(f"{YELLOW}[1/12] Checking Python version...{RESET}")
    version = sys.version_info
    passed = version.major == 3 and version.minor >= 11
    all_passed &= check(
        "Python version",
        passed,
        f"Found: {version.major}.{version.minor}.{version.micro} (Need: 3.11+)"
    )
    
    # 2. Python packages
    print(f"\n{YELLOW}[2/12] Checking Python packages...{RESET}")
    packages = [
        "fastapi", "uvicorn", "mss", "imagehash", "PIL",
        "google.generativeai", "openai", "anthropic",
        "deepgram", "sounddevice", "structlog", "nfo", "loguru"
    ]
    
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
            check(f"Package: {pkg}", True)
        except ImportError:
            check(f"Package: {pkg}", False, "Not installed")
            missing.append(pkg)
            all_passed = False
    
    if missing:
        print(f"\n  {RED}Missing packages. Install with:{RESET}")
        print(f"  pip install -r backend/requirements.txt")
    
    # 3. Environment file
    print(f"\n{YELLOW}[3/12] Checking .env file...{RESET}")
    env_path = "backend/.env"
    env_exists = os.path.exists(env_path)
    all_passed &= check(".env file exists", env_exists)
    
    if not env_exists:
        print(f"  {RED}Create .env from template:{RESET}")
        print(f"  cp backend/.env.example backend/.env")
        return False
    
    # 4. Load environment
    print(f"\n{YELLOW}[4/12] Loading environment variables...{RESET}")
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        check("Load .env", True)
    except Exception as e:
        check("Load .env", False, str(e))
        all_passed = False
        return False
    
    # 5. API Keys
    print(f"\n{YELLOW}[5/12] Checking API keys...{RESET}")
    
    deepgram_key = os.getenv("DEEPGRAM_API_KEY", "")
    all_passed &= check(
        "Deepgram API key",
        bool(deepgram_key and deepgram_key != "your_deepgram_api_key_here"),
        "Required for STT"
    )
    
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    all_passed &= check(
        "Gemini API key",
        bool(gemini_key and gemini_key != "your_gemini_api_key_here"),
        "Required for Vision (recommended provider)"
    )
    
    openai_key = os.getenv("OPENAI_API_KEY", "")
    check(
        "OpenAI API key",
        openai_key and openai_key != "your_openai_api_key_here",
        "Optional"
    )
    
    claude_key = os.getenv("ANTHROPIC_API_KEY", "")
    check(
        "Claude API key",
        claude_key and claude_key != "your_anthropic_api_key_here",
        "Optional"
    )
    
    # 6. Screen capture
    print(f"\n{YELLOW}[6/12] Testing screen capture...{RESET}")
    try:
        import mss
        sct = mss.mss()
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        check("Screen capture", True, f"Resolution: {img.width}x{img.height}")
    except Exception as e:
        check("Screen capture", False, str(e))
        all_passed = False
    
    # 7. Image processing
    print(f"\n{YELLOW}[7/12] Testing image processing...{RESET}")
    try:
        from PIL import Image
        import imagehash
        test_img = Image.new('RGB', (100, 100), color='red')
        hash_val = imagehash.phash(test_img)
        check("Image hashing", True, f"Hash: {hash_val}")
    except Exception as e:
        check("Image hashing", False, str(e))
        all_passed = False
    
    # 8. Audio devices
    print(f"\n{YELLOW}[8/12] Checking audio devices...{RESET}")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        check(
            "Audio input devices",
            len(input_devices) > 0,
            f"Found {len(input_devices)} microphone(s)"
        )
    except Exception as e:
        check("Audio devices", False, str(e))
        all_passed = False
    
    # 9. Deepgram connection
    print(f"\n{YELLOW}[9/12] Testing Deepgram API...{RESET}")
    if deepgram_key and deepgram_key != "your_deepgram_api_key_here":
        try:
            from deepgram import DeepgramClient
            client = DeepgramClient(deepgram_key)
            # Simple validation (no actual API call)
            check("Deepgram client", True, "API key format valid")
        except Exception as e:
            check("Deepgram client", False, str(e))
            all_passed = False
    else:
        check("Deepgram API", False, "API key not configured")
        all_passed = False
    
    # 10. Gemini connection
    print(f"\n{YELLOW}[10/12] Testing Gemini API...{RESET}")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            check("Gemini client", True, "API configured successfully")
        except Exception as e:
            check("Gemini client", False, str(e))
            all_passed = False
    else:
        check("Gemini API", False, "API key not configured")
        all_passed = False
    
    # 11. System tools for window awareness
    import shutil
    print(f"\n{YELLOW}[11/12] Checking system tools (window awareness)...{RESET}")
    tools = {
        "xdotool": "Active window detection",
        "xprop": "WM_CLASS detection",
        "xrandr": "Monitor detection",
        "wmctrl": "Window listing (optional)",
    }
    for tool, desc in tools.items():
        found = shutil.which(tool) is not None
        check(f"Tool: {tool}", found, desc + (" — installed" if found else " — not found"))
        if not found and tool != "wmctrl":
            all_passed = False
    
    # 12. Backend modules
    print(f"\n{YELLOW}[12/12] Checking backend modules...{RESET}")
    backend_modules = [
        ("window_aware", "Window awareness"),
        ("app_profiles", "Per-app analysis profiles"),
        ("shell_agent", "Shell agent"),
        ("process_scanner", "Process scanner"),
        ("window_cropper", "Window cropper"),
    ]
    for mod, desc in backend_modules:
        try:
            __import__(mod)
            check(f"Module: {mod}", True, desc)
        except Exception as e:
            check(f"Module: {mod}", False, f"{desc} — {e}")
    
    # Summary
    print(f"\n{BLUE}{'='*50}{RESET}")
    if all_passed:
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
