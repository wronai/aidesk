/**
 * Overlay configuration — single source of truth for backend URL and settings.
 *
 * Change BACKEND_URL here to point the overlay at a different backend instance.
 */
export const BACKEND_URL = 'http://localhost:8001';
export const SSE_URL = `${BACKEND_URL}/stream`;
