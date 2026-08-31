"""Basic import and sanity tests for proxeen."""

import importlib
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_backend_module_importable():
    """Verify the backend package can be imported."""
    try:
        import backend
        assert backend is not None
    except ImportError as e:
        # Allow missing optional heavy dependencies (cv2, paddle, etc.)
        heavy_deps = ['cv2', 'paddleocr', 'easyocr', 'torch', 'pyaudio']
        if any(dep in str(e) for dep in heavy_deps):
            import pytest
            pytest.skip(f"Optional dependency not installed: {e}")
        raise


def test_version_file_exists():
    """VERSION file must be present and non-empty."""
    version_path = os.path.join(os.path.dirname(__file__), '..', 'VERSION')
    assert os.path.exists(version_path), "VERSION file must exist"
    content = open(version_path).read().strip()
    assert content, "VERSION file must not be empty"
    # Must follow semver pattern x.y.z
    parts = content.split('.')
    assert len(parts) == 3, f"VERSION must be x.y.z format, got: {content}"
    assert all(p.isdigit() for p in parts), f"VERSION parts must be numeric, got: {content}"


def test_readme_exists():
    """README.md must be present."""
    readme_path = os.path.join(os.path.dirname(__file__), '..', 'README.md')
    assert os.path.exists(readme_path), "README.md must exist"
    content = open(readme_path).read()
    assert len(content) > 100, "README.md must have meaningful content"


def test_license_exists():
    """LICENSE file must be present."""
    license_path = os.path.join(os.path.dirname(__file__), '..', 'LICENSE')
    assert os.path.exists(license_path), "LICENSE file must exist"


def test_changelog_exists():
    """CHANGELOG.md must be present."""
    changelog_path = os.path.join(os.path.dirname(__file__), '..', 'CHANGELOG.md')
    assert os.path.exists(changelog_path), "CHANGELOG.md must exist"
