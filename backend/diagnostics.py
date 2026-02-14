"""
Autodiagnostics module - periodic health checks for all services and file generation.
Runs every DIAG_INTERVAL seconds (default 30) and reports status via logs and SSE.
"""
import asyncio
import os
import time
import glob
import sqlite3
from typing import Dict, List, Callable, Optional

import structlog

logger = structlog.get_logger()


class AutoDiagnostics:
    """
    Periodic self-diagnostics that verify:
    - Backend API is responsive
    - Screen capture is producing files
    - Log file is being written
    - SQLite log database is accessible
    - Analyzer component is alive
    - OCR engine is available
    - STT service status
    """

    def __init__(self, app_state: dict, interval: float = 30.0):
        self.app_state = app_state
        self.interval = interval
        self.run_count = 0
        self.last_result: Optional[Dict] = None
        self.history: List[Dict] = []
        self.max_history = 100

    async def run_loop(self, broadcast_fn: Callable):
        """Main diagnostics loop. First run after interval, then repeats."""
        logger.info("Autodiagnostics started", interval=self.interval)

        while True:
            await asyncio.sleep(self.interval)
            self.run_count += 1

            try:
                result = self._run_checks()
                self.last_result = result
                self.history.append(result)
                if len(self.history) > self.max_history:
                    self.history = self.history[-self.max_history:]

                # Log summary
                failed = [c["name"] for c in result["checks"] if not c["ok"]]
                passed = [c["name"] for c in result["checks"] if c["ok"]]

                if failed:
                    logger.warning(
                        "Diagnostics: issues detected",
                        run=self.run_count,
                        failed=failed,
                        passed=passed,
                    )
                else:
                    logger.info(
                        "Diagnostics: all checks passed",
                        run=self.run_count,
                        total=len(result["checks"]),
                    )

                # Broadcast to overlay
                await broadcast_fn("diagnostics", result)

            except Exception as e:
                logger.error("Diagnostics loop error", error=str(e))

    def _run_checks(self) -> Dict:
        """Execute all diagnostic checks and return results."""
        checks = []
        start = time.time()

        checks.append(self._check_capture())
        checks.append(self._check_captures_dir())
        checks.append(self._check_analyzer())
        checks.append(self._check_ocr())
        checks.append(self._check_stt())
        checks.append(self._check_log_file())
        checks.append(self._check_log_db())
        checks.append(self._check_context())

        elapsed = round(time.time() - start, 3)
        all_ok = all(c["ok"] for c in checks)

        return {
            "timestamp": time.time(),
            "run": self.run_count,
            "all_ok": all_ok,
            "elapsed_ms": elapsed * 1000,
            "checks": checks,
        }

    def _check_capture(self) -> Dict:
        """Check if screen capture component is alive and producing data."""
        capture = self.app_state.get("capture")
        if capture is None:
            return {"name": "capture", "ok": False, "detail": "Not initialized"}

        stats = capture.get_stats()
        return {
            "name": "capture",
            "ok": True,
            "detail": {
                "total_captures": stats.get("total_captures", 0),
                "changes_detected": stats.get("changes_detected", 0),
                "is_idle": stats.get("is_idle", False),
                "current_interval": stats.get("current_interval", 0),
            },
        }

    def _check_captures_dir(self) -> Dict:
        """Check if screenshot files are being generated in CAPTURES_DIR."""
        captures_dir = os.getenv("CAPTURES_DIR", "/tmp/aidesk_captures")

        if not os.path.exists(captures_dir):
            return {"name": "captures_dir", "ok": False, "detail": f"Directory does not exist: {captures_dir}"}

        files = glob.glob(os.path.join(captures_dir, "*.jpg"))
        file_count = len(files)

        if file_count == 0:
            return {"name": "captures_dir", "ok": False, "detail": "No screenshot files found"}

        # Check freshness - newest file should be recent
        newest = max(files, key=os.path.getmtime)
        newest_age = time.time() - os.path.getmtime(newest)
        total_size = sum(os.path.getsize(f) for f in files)

        return {
            "name": "captures_dir",
            "ok": True,
            "detail": {
                "path": captures_dir,
                "file_count": file_count,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "newest_file": os.path.basename(newest),
                "newest_age_seconds": round(newest_age, 1),
            },
        }

    def _check_analyzer(self) -> Dict:
        """Check if analyzer component is alive."""
        analyzer = self.app_state.get("analyzer")
        if analyzer is None:
            return {"name": "analyzer", "ok": False, "detail": "Not initialized"}

        stats = analyzer.get_stats()
        total_analyses = self.app_state["stats"].get("total_screen_analyses", 0)

        return {
            "name": "analyzer",
            "ok": True,
            "detail": {
                "total_analyses": total_analyses,
                "provider": stats.get("provider", "unknown"),
                "mode": stats.get("mode", "unknown"),
            },
        }

    def _check_ocr(self) -> Dict:
        """Check OCR engine availability."""
        ocr = self.app_state.get("ocr_manager")
        if ocr is None:
            return {"name": "ocr", "ok": False, "detail": "Not initialized"}

        try:
            stats = ocr.get_stats()
            return {
                "name": "ocr",
                "ok": True,
                "detail": {
                    "active_engine": stats.get("active_engine", "unknown"),
                    "total_extractions": stats.get("total_extractions", 0),
                },
            }
        except Exception as e:
            return {"name": "ocr", "ok": False, "detail": str(e)}

    def _check_stt(self) -> Dict:
        """Check STT service status."""
        stt = self.app_state.get("stt")
        if stt is None:
            enabled = os.getenv("ENABLE_STT", "true").lower() == "true"
            if enabled:
                return {"name": "stt", "ok": False, "detail": "Enabled but not initialized"}
            return {"name": "stt", "ok": True, "detail": "Disabled in config"}

        try:
            stats = stt.get_stats()
            return {
                "name": "stt",
                "ok": True,
                "detail": {
                    "total_transcripts": self.app_state["stats"].get("total_transcripts", 0),
                    "model": stats.get("model", "unknown"),
                },
            }
        except Exception as e:
            return {"name": "stt", "ok": False, "detail": str(e)}

    def _check_log_file(self) -> Dict:
        """Check if log file exists and is being written to."""
        log_file = os.getenv("LOG_FILE", "logs/assistant.log")

        if not os.path.exists(log_file):
            return {"name": "log_file", "ok": False, "detail": f"File does not exist: {log_file}"}

        stat = os.stat(log_file)
        age = time.time() - stat.st_mtime

        return {
            "name": "log_file",
            "ok": age < 120,
            "detail": {
                "path": log_file,
                "size_kb": round(stat.st_size / 1024, 1),
                "last_modified_seconds_ago": round(age, 1),
            },
        }

    def _check_log_db(self) -> Dict:
        """Check if SQLite log database is accessible and has recent entries."""
        db_file = os.getenv("LOG_DB", "logs/logs.sqlite")

        if not os.path.exists(db_file):
            return {"name": "log_db", "ok": False, "detail": f"Database does not exist: {db_file}"}

        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM logs")
            count = cursor.fetchone()[0]
            cursor.execute("SELECT timestamp FROM logs ORDER BY rowid DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()

            return {
                "name": "log_db",
                "ok": count > 0,
                "detail": {
                    "path": db_file,
                    "total_entries": count,
                    "latest_entry": row[0] if row else None,
                },
            }
        except Exception as e:
            return {"name": "log_db", "ok": False, "detail": str(e)}

    def _check_context(self) -> Dict:
        """Check context manager state."""
        ctx = self.app_state.get("context")
        if ctx is None:
            return {"name": "context", "ok": False, "detail": "Not initialized"}

        stats = ctx.get_stats()
        return {
            "name": "context",
            "ok": True,
            "detail": {
                "items": stats.get("total_items", len(ctx.history)),
                "max_items": stats.get("max_items", 0),
            },
        }

    def get_latest(self) -> Optional[Dict]:
        """Return latest diagnostics result."""
        return self.last_result

    def get_history(self) -> List[Dict]:
        """Return diagnostics history."""
        return self.history
