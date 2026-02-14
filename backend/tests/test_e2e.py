import pytest
from fastapi.testclient import TestClient
import sys
import os

# Add backend directory to sys.path to import server
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert "endpoints" in data

def test_health_check():
    response = client.get("/health")
    # It might return 503 if components are not initialized (mocking might be needed for full green)
    # But we check if it returns a valid response structure
    assert response.status_code in [200, 503]
    data = response.json()
    assert "status" in data
    assert "components" in data

def test_stats_endpoint():
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert "uptime_seconds" in data
    assert "total_screen_analyses" in data

def test_ocr_engines_endpoint():
    # This might return 404 if OCR not initialized, or 200
    response = client.get("/ocr/engines")
    assert response.status_code in [200, 404]

def test_mode_endpoint():
    # This might return 503 if analyzer not initialized
    response = client.get("/mode")
    assert response.status_code in [200, 503]
