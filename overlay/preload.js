/**
 * AI Desktop Assistant - Electron Preload Script
 * 
 * Exposes safe APIs to the renderer process.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
  // Send log messages to main process
  log: (message) => {
    ipcRenderer.send('log', message);
  },

  // Mouse event forwarding for click-through overlay
  setIgnoreMouseEvents: (ignore, opts) => {
    ipcRenderer.send('set-ignore-mouse-events', ignore, opts);
  },

  // Move overlay to next screen
  moveToNextScreen: () => {
    ipcRenderer.send('move-to-next-screen');
  },

  // Resize overlay window
  resizeOverlay: (width, height) => {
    ipcRenderer.send('resize-overlay', width, height);
  },

  // Make overlay focusable (for text input in selection panel)
  setFocusable: (focusable) => {
    ipcRenderer.send('set-focusable', focusable);
  },

  // Pause/resume auto-selection watcher (pause while user types in textarea)
  setSelectionWatcher: (enabled) => {
    ipcRenderer.send('set-selection-watcher', enabled);
  },

  // Listen for selection analysis trigger from main process
  onAnalyzeSelection: (callback) => {
    ipcRenderer.on('analyze-selection', (event, text) => callback(text));
  },

  // App info
  versions: {
    node: process.versions.node,
    chrome: process.versions.chrome,
    electron: process.versions.electron,
  },
});

console.log('Preload script loaded');
