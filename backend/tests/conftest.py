"""Shared test configuration — ensure backend modules are importable."""
import sys
import os

# Add backend directory to sys.path BEFORE any test imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
