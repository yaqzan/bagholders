import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Register service worker for PWA (home-screen shortcut + offline shell).
// Skip on localhost — the cache-first /static/* strategy masks dev-mode rebuilds
// (Ctrl+Shift+R bypasses HTTP cache but NOT service workers), causing stale UI.
const isLocalhost = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
if ('serviceWorker' in navigator && !isLocalhost) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/service-worker.js')
      .catch((err) => console.error('SW registration failed:', err));
  });
} else if ('serviceWorker' in navigator && isLocalhost) {
  // If a SW was registered previously on localhost, unregister it so the next
  // load behaves as plain dev — otherwise stale caches persist for weeks.
  navigator.serviceWorker.getRegistrations().then((regs) => {
    regs.forEach((r) => r.unregister());
  });
}
