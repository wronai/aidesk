/**
 * AI Desktop Assistant - Electron Main Process
 */
const { app, BrowserWindow, screen, globalShortcut, ipcMain } = require('electron');
const path = require('path');

let overlay;

function createOverlay() {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;

  overlay = new BrowserWindow({
    width: 400,
    height: 620,
    x: width - 420,
    y: height - 640,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    skipTaskbar: true,
    resizable: false,
    movable: true,
    focusable: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Click-through by default; renderer tells us when mouse is over interactive areas
  overlay.setIgnoreMouseEvents(true, { forward: true });
  
  // Keep on top of all windows, even fullscreen
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  overlay.setAlwaysOnTop(true, 'screen-saver', 1);

  overlay.loadFile('index.html');

  // Open DevTools in development (comment out for production)
  // overlay.webContents.openDevTools({ mode: 'detach' });

  console.log('Overlay window created');
}

app.whenReady().then(() => {
  createOverlay();

  // Global shortcuts
  
  // Ctrl+Shift+A: Toggle overlay visibility
  globalShortcut.register('CommandOrControl+Shift+A', () => {
    if (overlay.isVisible()) {
      overlay.hide();
      console.log('Overlay hidden');
    } else {
      overlay.show();
      console.log('Overlay shown');
    }
  });

  // Ctrl+Shift+Q: Quit application
  globalShortcut.register('CommandOrControl+Shift+Q', () => {
    console.log('Quitting application');
    app.quit();
  });

  console.log('Global shortcuts registered:');
  console.log('  Ctrl+Shift+A: Toggle overlay');
  console.log('  Ctrl+Shift+Q: Quit');
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createOverlay();
  }
});

app.on('will-quit', () => {
  // Unregister all shortcuts
  globalShortcut.unregisterAll();
  console.log('Shortcuts unregistered');
});

// Handle IPC from renderer
ipcMain.on('log', (event, message) => {
  console.log('[Renderer]', message);
});

// Mouse event forwarding: make interactive elements clickable
ipcMain.on('set-ignore-mouse-events', (event, ignore, opts) => {
  if (overlay) {
    overlay.setIgnoreMouseEvents(ignore, opts || {});
  }
});
