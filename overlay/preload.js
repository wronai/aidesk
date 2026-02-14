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
  
  // App info
  versions: {
    node: process.versions.node,
    chrome: process.versions.chrome,
    electron: process.versions.electron,
  },
});

console.log('Preload script loaded');
