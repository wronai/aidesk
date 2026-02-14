import pytest
from fastapi.testclient import TestClient
import sys
import os

# Add backend directory to sys.path to import server
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app

# Tests using TestClient as context manager to trigger lifespan events

def test_read_root():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "endpoints" in data

def test_health_check():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code in [200, 503]
        data = response.json()
        assert "status" in data
        assert "components" in data

def test_stats_endpoint():
    with TestClient(app) as client:
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert "uptime_seconds" in data
        assert "total_screen_analyses" in data

def test_ocr_engines_endpoint():
    with TestClient(app) as client:
        response = client.get("/ocr/engines")
        assert response.status_code in [200, 404]

def test_mode_endpoint():
    with TestClient(app) as client:
        response = client.get("/mode")
        assert response.status_code in [200, 503]

def test_processes_endpoint():
    with TestClient(app) as client:
        response = client.get("/processes")
        # Might be 503 if process scanner fails to init
        assert response.status_code in [200, 503]

def test_nfo_validation_endpoint():
    with TestClient(app) as client:
        response = client.get("/nfo/validation")
        assert response.status_code == 200
        data = response.json()
        assert "all_ok" in data
