/**
 * AI Desktop Assistant - Electron Main Process
 */
const { app, BrowserWindow, screen, globalShortcut, ipcMain, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');

let overlay;

// Persistent position/size storage
const PREFS_PATH = path.join(app.getPath('userData'), 'overlay-prefs.json');

function loadPrefs() {
  try {
    if (fs.existsSync(PREFS_PATH)) {
      return JSON.parse(fs.readFileSync(PREFS_PATH, 'utf8'));
    }
  } catch (e) { /* ignore */ }
  return null;
}

function savePrefs() {
  if (!overlay) return;
  try {
    const bounds = overlay.getBounds();
    fs.writeFileSync(PREFS_PATH, JSON.stringify(bounds), 'utf8');
  } catch (e) { /* ignore */ }
}

function createOverlay() {
  const prefs = loadPrefs();
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;

  const defaults = {
    width: 420,
    height: 700,
    x: width - 440,
    y: height - 720,
  };

  const bounds = prefs || defaults;

  overlay = new BrowserWindow({
    ...bounds,
    minWidth: 320,
    minHeight: 400,
    maxWidth: 900,
    maxHeight: 1200,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    skipTaskbar: true,
    resizable: true,
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

  // Save position/size on move or resize
  overlay.on('moved', savePrefs);
  overlay.on('resized', savePrefs);

  // Open DevTools in development (comment out for production)
  // overlay.webContents.openDevTools({ mode: 'detach' });

  console.log('Overlay window created', bounds);
}

/**
 * Move overlay to the next screen (cycles through displays).
 */
function moveToNextScreen() {
  if (!overlay) return;
  const displays = screen.getAllDisplays();
  if (displays.length <= 1) return;

  const current = screen.getDisplayMatching(overlay.getBounds());
  const idx = displays.findIndex(d => d.id === current.id);
  const next = displays[(idx + 1) % displays.length];
  const { x, y, width, height } = next.workArea;
  const [w, h] = overlay.getSize();

  // Place in bottom-right corner of target screen
  overlay.setPosition(x + width - w - 20, y + height - h - 20);
  savePrefs();
  console.log('Moved overlay to display', next.id);
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

  // Ctrl+Shift+S: Analyze selected text (reads clipboard)
  globalShortcut.register('CommandOrControl+Shift+S', () => {
    const text = clipboard.readText('selection') || clipboard.readText();
    if (text && text.trim()) {
      overlay.webContents.send('analyze-selection', text.trim());
      if (!overlay.isVisible()) overlay.show();
      console.log('Selection analysis triggered', text.substring(0, 40));
    }
  });

  // Ctrl+Shift+M: Move overlay to next screen
  globalShortcut.register('CommandOrControl+Shift+M', () => {
    moveToNextScreen();
  });

  // Ctrl+Shift+Q: Quit application
  globalShortcut.register('CommandOrControl+Shift+Q', () => {
    console.log('Quitting application');
    app.quit();
  });

  console.log('Global shortcuts registered:');
  console.log('  Ctrl+Shift+A: Toggle overlay');
  console.log('  Ctrl+Shift+S: Analyze selection');
  console.log('  Ctrl+Shift+M: Move to next screen');
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

// Move to next screen from renderer
ipcMain.on('move-to-next-screen', () => {
  moveToNextScreen();
});

// Resize overlay from renderer
ipcMain.on('resize-overlay', (event, width, height) => {
  if (overlay) {
    overlay.setSize(Math.round(width), Math.round(height));
    savePrefs();
  }
});

// Make overlay focusable temporarily (for text input)
ipcMain.on('set-focusable', (event, focusable) => {
  if (overlay) {
    overlay.setFocusable(focusable);
    if (focusable) {
      overlay.setIgnoreMouseEvents(false);
      overlay.focus();
    }
  }
});
