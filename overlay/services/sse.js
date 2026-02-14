/**
 * Service to manage Server-Sent Events (SSE) connection.
 */
export class SSEService {
  constructor(url, reconnectInterval = 3000) {
    this.url = url;
    this.reconnectInterval = reconnectInterval;
    this.eventSource = null;
    this.reconnectTimer = null;
    this.listeners = new Map();
    this.isConnected = false;
  }

  connect() {
    if (this.eventSource) {
      this.eventSource.close();
    }

    console.log('Connecting to backend...');
    this.emit('connecting');

    this.eventSource = new EventSource(this.url);

    this.eventSource.addEventListener('connected', (e) => {
      console.log('Connected to backend');
      this.isConnected = true;
      this.emit('connected');
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    });

    // Handle generic open event
    this.eventSource.onopen = () => {
      // Some backends might not send explicit 'connected' event
      if (!this.isConnected) {
        this.isConnected = true;
        this.emit('connected');
      }
    };

    this.eventSource.onerror = (error) => {
      console.error('SSE error:', error);
      this.isConnected = false;
      this.emit('error', error);
      this.eventSource.close();

      // Attempt reconnection
      if (!this.reconnectTimer) {
        console.log(`Reconnecting in ${this.reconnectInterval / 1000}s...`);
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.connect();
        }, this.reconnectInterval);
      }
    };

    // Re-attach listeners
    this.listeners.forEach((callbacks, event) => {
      callbacks.forEach(callback => {
        this.eventSource.addEventListener(event, (e) => {
          try {
            const data = JSON.parse(e.data);
            callback(data);
          } catch (err) {
            console.error(`Error parsing SSE data for ${event}:`, err);
          }
        });
      });
    });
  }

  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);

    if (this.eventSource) {
      this.eventSource.addEventListener(event, (e) => {
        try {
          const data = JSON.parse(e.data);
          callback(data);
        } catch (err) {
          console.error(`Error parsing SSE data for ${event}:`, err);
        }
      });
    }
  }

  off(event, callback) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).delete(callback);
    }
    // Note: cannot easily remove listener from EventSource instance without keeping wrapper reference
  }

  // Internal event emitter for status updates
  emit(event, data) {
    const customEvent = new CustomEvent(`sse:${event}`, { detail: data });
    window.dispatchEvent(customEvent);
  }
}

export const sseService = new SSEService('http://localhost:8001/stream');
