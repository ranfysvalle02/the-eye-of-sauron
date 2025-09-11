// --- Leader Election for Single Tab Experience ---
// This script ensures only one tab is actively running the application.
// It must be loaded AFTER all other app scripts.

const myTabId = `sauron-tab-${Date.now()}-${Math.random()}`;
let isLeader = false;
let isAppInitialized = false;
let leaderCheckInterval = null;
let eventSource = null; // This will be assigned by initializeApp() in ui.js

function showMultiTabOverlay() {
  let overlay = document.getElementById('multi-tab-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'multi-tab-overlay';
    overlay.style.position = 'fixed';
    overlay.style.inset = '0';
    overlay.style.background = '#121921';
    overlay.style.color = '#F9FAFB';
    overlay.style.display = 'grid';
    overlay.style.placeContent = 'center';
    overlay.style.textAlign = 'center';
    overlay.style.fontFamily = 'sans-serif';
    overlay.style.zIndex = '9999';
    overlay.innerHTML = `<style>@keyframes pulse_sauron { 0%, 100% { transform: scale(1); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; } }</style>
      <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" alt="Pulsating Eye" style="width:150px;height:auto;margin:auto;margin-bottom:20px;animation:pulse_sauron 2s infinite ease-in-out;">
      <h1 style="color:#00ED64;font-size:1.8rem;margin:0;">One Tab to Rule Them All</h1>
      <p style="font-size:1.1rem;margin-top:8px;">This application is already open. Please close this tab to continue.</p>`;
    document.body.appendChild(overlay);
  }
  overlay.style.display = 'grid';
}

function hideMultiTabOverlay() {
  const overlay = document.getElementById('multi-tab-overlay');
  if (overlay) {
    overlay.style.display = 'none';
  }
}

function shutdownApp() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
    console.log(`Tab ${myTabId} (follower): SSE connection closed.`);
  }
}

function disableApp() {
  if (isLeader || isAppInitialized) {
    shutdownApp();
    isLeader = false;
    isAppInitialized = false;
  }
  showMultiTabOverlay();
}

function becomeLeader() {
  if (!isLeader) {
    console.log(`Tab ${myTabId} is now the LEADER.`);
    isLeader = true;
    localStorage.setItem('sauron_leader_heartbeat', Date.now());
    hideMultiTabOverlay();
    if (!isAppInitialized) {
      initializeApp(); // Calls the function from app.js
      isAppInitialized = true;
    }
  }
  localStorage.setItem('sauron_leader_heartbeat', Date.now());
}

function checkLeader() {
  const leaderId = localStorage.getItem('sauron_leader_tab_id');
  const lastHeartbeat = parseInt(localStorage.getItem('sauron_leader_heartbeat') || '0', 10);
  const isHeartbeatStale = (Date.now() - lastHeartbeat) > 4000;

  if (!leaderId || isHeartbeatStale) {
    // Attempt to become the leader
    localStorage.setItem('sauron_leader_tab_id', myTabId);
    setTimeout(() => {
      // Check if we are still the leader after a short delay
      if (localStorage.getItem('sauron_leader_tab_id') === myTabId) {
        becomeLeader();
      } else {
        disableApp();
      }
    }, 50);
  } else if (leaderId === myTabId) {
    becomeLeader();
  } else {
    disableApp();
  }
}

window.addEventListener('storage', (e) => {
  if (e.key === 'sauron_leader_tab_id' || e.key === 'sauron_leader_heartbeat') {
    checkLeader();
  }
});

window.addEventListener('beforeunload', () => {
  if (isLeader) {
    localStorage.removeItem('sauron_leader_tab_id');
    localStorage.removeItem('sauron_leader_heartbeat');
  }
});

// --- Start the leader election process on page load ---
document.addEventListener('DOMContentLoaded', () => {
  leaderCheckInterval = setInterval(checkLeader, 2000);
  checkLeader();
});