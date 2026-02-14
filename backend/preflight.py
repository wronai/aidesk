"""
Pre-startup diagnostics — verify model connectivity before main loop.

Runs lightweight ping requests to configured LLM/VLM endpoints to catch
misconfiguration (bad API keys, unreachable providers, wrong model names)
before the pipeline starts producing errors.

Called from bootstrap.py between init_core() and start_tasks().
"""
import io
import os
import sys
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

import nfo
import structlog

logger = structlog.get_logger()

# ANSI colors for terminal output
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _mask_key(key: str) -> str:
    """Mask API key for safe logging: show first 8 + last 4 chars."""
    if not key or len(key) < 16:
        return "***" if key else "(empty)"
    return f"{key[:8]}...{key[-4:]}"


def _fmt_ok(name: str, detail: str = "", latency_ms: float = 0) -> str:
    lat = f" {_DIM}({latency_ms:.0f}ms){_RESET}" if latency_ms else ""
    det = f"  {_DIM}{detail}{_RESET}" if detail else ""
    return f"  {_GREEN}✓{_RESET} {name}{lat}{det}"


def _fmt_fail(name: str, detail: str = "") -> str:
    det = f"  {_DIM}{detail}{_RESET}" if detail else ""
    return f"  {_RED}✗{_RESET} {name}{det}"


def _fmt_skip(name: str, detail: str = "") -> str:
    det = f"  {_DIM}{detail}{_RESET}" if detail else ""
    return f"  {_YELLOW}⊘{_RESET} {name}{det}"


def _fmt_header(title: str) -> str:
    return f"\n{_BLUE}{_BOLD}{'─' * 50}\n  {title}\n{'─' * 50}{_RESET}"


@contextmanager
def _suppress_stdout():
    """Temporarily suppress stdout (litellm prints provider lists)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old


class PreflightResult:
    """Result of a single preflight check."""

    def __init__(self, name: str, ok: bool, skipped: bool = False,
                 detail: str = "", latency_ms: float = 0):
        self.name = name
        self.ok = ok
        self.skipped = skipped
        self.detail = detail
        self.latency_ms = latency_ms

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 1),
        }


class PreflightDiagnostics:
    """
    Pre-startup connectivity checks for all configured AI models.

    Checks:
      1. Vision LLM (litellm acompletion) — the main analysis model
      2. VLM OCR engine (litellm completion) — cloud OCR model
      3. API key presence for configured providers
      4. litellm importability

    Usage:
        preflight = PreflightDiagnostics(settings)
        report = await preflight.run()
        # report["all_ok"] == True means all models reachable
    """

    def __init__(self, settings=None):
        if settings is None:
            from settings import get_settings
            settings = get_settings()
        self.settings = settings

    async def run(self) -> Dict:
        """Run all preflight checks and print results to terminal."""
        print(_fmt_header("PREFLIGHT DIAGNOSTICS"))
        start = time.time()
        results: List[PreflightResult] = []

        # 1. Check litellm availability
        results.append(self._check_litellm())

        # 2. Check API keys
        results.extend(self._check_api_keys())

        # 3. Ping Vision LLM
        results.append(await self._ping_vision_llm())

        # 4. Ping VLM OCR (if configured)
        results.append(await self._ping_vlm_ocr())

        # 5. Check window detection tools
        results.extend(self._check_window_tools())

        elapsed = (time.time() - start) * 1000

        # Print results
        for r in results:
            if r.skipped:
                print(_fmt_skip(r.name, r.detail))
            elif r.ok:
                print(_fmt_ok(r.name, r.detail, r.latency_ms))
            else:
                print(_fmt_fail(r.name, r.detail))

        # Summary
        passed = [r for r in results if r.ok and not r.skipped]
        failed = [r for r in results if not r.ok and not r.skipped]
        skipped = [r for r in results if r.skipped]

        all_ok = len(failed) == 0

        print()
        if all_ok:
            print(f"  {_GREEN}{_BOLD}✓ All preflight checks passed{_RESET} {_DIM}({elapsed:.0f}ms){_RESET}")
        else:
            print(f"  {_RED}{_BOLD}✗ {len(failed)} preflight check(s) failed{_RESET} {_DIM}({elapsed:.0f}ms){_RESET}")
            for f in failed:
                print(f"    {_RED}→ {f.name}: {f.detail}{_RESET}")
        print(f"{_BLUE}{'─' * 50}{_RESET}\n")

        report = {
            "all_ok": all_ok,
            "elapsed_ms": round(elapsed, 1),
            "passed": [r.name for r in passed],
            "failed": [r.name for r in failed],
            "skipped": [r.name for r in skipped],
            "checks": [r.to_dict() for r in results],
        }

        # Log structured result
        logger.info(
            "Preflight diagnostics complete",
            all_ok=all_ok,
            passed=len(passed),
            failed=len(failed),
            skipped=len(skipped),
            elapsed_ms=round(elapsed, 1),
        )

        return report

    def _check_litellm(self) -> PreflightResult:
        """Check that litellm is importable."""
        try:
            with _suppress_stdout():
                import litellm  # noqa: F401
            version = getattr(litellm, "__version__", None)
            if not version:
                try:
                    from importlib.metadata import version as pkg_version
                    version = pkg_version("litellm")
                except Exception:
                    version = "installed"
            return PreflightResult("litellm", ok=True, detail=f"v{version}")
        except ImportError:
            return PreflightResult("litellm", ok=False, detail="Not installed. Run: pip install litellm")

    def _check_api_keys(self) -> List[PreflightResult]:
        """Check that required API keys are present for configured providers."""
        results = []
        s = self.settings

        # Determine which providers need keys
        models_to_check = [s.vision_model]
        if s.ocr_engine == "vlm_ocr":
            models_to_check.append(s.vlm_ocr_model)

        needs_openrouter = any("openrouter" in m for m in models_to_check)
        needs_gemini = any("gemini" in m and "openrouter" not in m for m in models_to_check)
        needs_openai = any(
            (m.startswith("gpt-") or m.startswith("openai/") or m.startswith("o1-") or m.startswith("o3-"))
            and "openrouter" not in m
            for m in models_to_check
        )

        if needs_openrouter:
            key = s.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
            ok = bool(key and len(key) > 10)
            results.append(PreflightResult(
                "OPENROUTER_API_KEY",
                ok=ok,
                detail=_mask_key(key) if ok else "Missing or empty — required for openrouter models",
            ))

        if needs_gemini:
            key = s.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
            ok = bool(key and len(key) > 10)
            results.append(PreflightResult(
                "GEMINI_API_KEY",
                ok=ok,
                detail=_mask_key(key) if ok else "Missing or empty — required for Gemini models",
            ))

        if needs_openai:
            key = s.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            ok = bool(key and len(key) > 10)
            results.append(PreflightResult(
                "OPENAI_API_KEY",
                ok=ok,
                detail=_mask_key(key) if ok else "Missing or empty — required for OpenAI models",
            ))

        # LiteLLM API key (used as override for all providers)
        if s.litellm_api_key:
            results.append(PreflightResult(
                "LITELLM_API_KEY",
                ok=True,
                detail=f"Override: {_mask_key(s.litellm_api_key)}",
            ))

        return results

    async def _ping_vision_llm(self) -> PreflightResult:
        """Send a minimal text-only request to the Vision LLM to verify connectivity."""
        s = self.settings
        model = s.vision_model
        name = f"Vision LLM ({model})"

        # Skip if analysis mode doesn't need LLM
        if s.analysis_mode == "ocr_only":
            return PreflightResult(name, ok=True, skipped=True, detail="ocr_only mode — LLM not used")

        try:
            with _suppress_stdout():
                from litellm import acompletion
                import litellm
                litellm.drop_params = True

            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 5,
                "temperature": 0,
                "timeout": 15.0,
            }
            if s.litellm_api_base:
                kwargs["api_base"] = s.litellm_api_base
            if s.litellm_api_key:
                kwargs["api_key"] = s.litellm_api_key

            with _suppress_stdout():
                start = time.time()
                response = await acompletion(**kwargs)
                latency = (time.time() - start) * 1000

            text = (response.choices[0].message.content or "").strip()
            tokens = 0
            if hasattr(response, "usage") and response.usage:
                tokens = response.usage.total_tokens or 0

            return PreflightResult(
                name, ok=True,
                detail=f"response={text!r}, tokens={tokens}",
                latency_ms=latency,
            )
        except Exception as e:
            return PreflightResult(name, ok=False, detail=str(e))

    async def _ping_vlm_ocr(self) -> PreflightResult:
        """Send a minimal text-only request to the VLM OCR model to verify connectivity."""
        s = self.settings
        model = s.vlm_ocr_model
        name = f"VLM OCR ({model})"

        # Skip if VLM OCR is not the active engine
        if s.ocr_engine != "vlm_ocr":
            return PreflightResult(
                name, ok=True, skipped=True,
                detail=f"OCR engine is '{s.ocr_engine}', not vlm_ocr",
            )

        # Skip if same model as vision LLM (already tested)
        if model == s.vision_model:
            return PreflightResult(
                name, ok=True, skipped=True,
                detail="Same model as Vision LLM — already verified",
            )

        try:
            with _suppress_stdout():
                import litellm
                litellm.drop_params = True

            # Resolve API key
            api_key = s.litellm_api_key or ""
            if not api_key:
                if "openrouter" in model:
                    api_key = s.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
                elif "gemini" in model:
                    api_key = s.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
                elif "openai" in model or "gpt" in model:
                    api_key = s.openai_api_key or os.environ.get("OPENAI_API_KEY", "")

            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 5,
                "temperature": 0,
                "timeout": 15.0,
            }
            if api_key:
                kwargs["api_key"] = api_key

            with _suppress_stdout():
                start = time.time()
                response = litellm.completion(**kwargs)
                latency = (time.time() - start) * 1000

            text = (response.choices[0].message.content or "").strip()
            tokens = 0
            if hasattr(response, "usage") and response.usage:
                tokens = response.usage.total_tokens or 0

            return PreflightResult(
                name, ok=True,
                detail=f"response={text!r}, tokens={tokens}",
                latency_ms=latency,
            )
        except Exception as e:
            return PreflightResult(name, ok=False, detail=str(e))


    def _check_window_tools(self) -> List[PreflightResult]:
        """Check window detection tools: python-xlib and CLI fallbacks."""
        import subprocess
        results = []

        # python-xlib / ewmh (preferred fast path)
        try:
            from ewmh import EWMH
            from Xlib import display as _xdisplay
            disp = _xdisplay.Display()
            ewmh = EWMH(disp)
            clients = ewmh.getClientList() or []
            results.append(PreflightResult(
                "python-xlib + ewmh",
                ok=True,
                detail=f"Direct X11 backend, {len(clients)} windows visible",
            ))
        except ImportError:
            results.append(PreflightResult(
                "python-xlib + ewmh",
                ok=False,
                detail="Not installed. Run: pip install python-xlib ewmh",
            ))
        except Exception as e:
            results.append(PreflightResult(
                "python-xlib + ewmh",
                ok=False,
                detail=f"Init failed: {e} (DISPLAY={os.environ.get('DISPLAY', '')})",
            ))

        # CLI fallbacks
        for tool in ("xdotool", "xprop", "wmctrl"):
            try:
                r = subprocess.run(["which", tool], capture_output=True, timeout=2)
                ok = r.returncode == 0
            except Exception:
                ok = False
            detail = "available" if ok else f"Not found. Install: sudo apt install {tool}"
            results.append(PreflightResult(f"CLI: {tool}", ok=ok, detail=detail))

        return results


@nfo.log_call(level="INFO")
async def run_preflight(settings=None) -> Dict:
    """
    Run pre-startup diagnostics. Returns report dict.

    Called from bootstrap.py before start_tasks().
    """
    preflight = PreflightDiagnostics(settings)
    return await preflight.run()
