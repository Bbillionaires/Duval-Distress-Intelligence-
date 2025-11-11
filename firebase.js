// firebase.js
// Central Firebase config, initialization, and auth guard for Distress Intelligence.

// TODO: REPLACE THIS with your real config from the Firebase console.
const firebaseConfig = {
      apiKey: "AIzaSyDTMTNQceT5sbzJVnZknWOWF27G0KzYgPE",
      authDomain: "duval-distress-intelligence.firebaseapp.com",
      projectId: "duval-distress-intelligence",
      storageBucket: "duval-distress-intelligence.firebasestorage.app",
      messagingSenderId: "374769856603",
      appId: "1:374769856603:web:342062e92ef5d1c289cf7e"
};

/**
 * Initialize Firebase safely.
 * - Doesn't crash if SDK failed to load.
 * - Avoids double-initializing.
 */
function ensureFirebaseApp() {
  if (typeof firebase === "undefined") {
    console.error("Firebase SDK not loaded (firebase global missing).");
    return null;
  }

  try {
    if (firebase.apps && firebase.apps.length > 0) {
      return firebase.app();
    }
    return firebase.initializeApp(firebaseConfig);
  } catch (e) {
    console.error("Error initializing Firebase:", e);
    return null;
  }
}

(function () {
  function setupAuthUI() {
    const app = ensureFirebaseApp();
    if (!app || !firebase.auth) {
      console.warn("Firebase auth not available on this page.");
      return;
    }

    const path = window.location.pathname || "";
    const onLoginPage =
      path.endsWith("login.html") || path === "/login" || path === "/login/";

    const userStatus = document.getElementById("userStatus");
    const logoutBtn = document.getElementById("logoutBtn");

    firebase.auth().onAuthStateChanged((user) => {
      if (!user) {
        // Not logged in
        if (userStatus) userStatus.textContent = "";
        if (logoutBtn) logoutBtn.classList.add("hidden");

        // Protect dashboard: if not on login page, force login
        if (!onLoginPage) {
          window.location.href = "/login";
        }
        return;
      }

      // Logged in
      if (userStatus) {
        userStatus.textContent = user.email || "Signed in";
      }

      if (logoutBtn) {
        logoutBtn.classList.remove("hidden");
        if (!logoutBtn.dataset.bound) {
          logoutBtn.addEventListener("click", async () => {
            try {
              await firebase.auth().signOut();
              window.location.href = "/login";
            } catch (err) {
              console.error("Logout failed:", err);
            }
          });
          logoutBtn.dataset.bound = "true";
        }
      }

      // If on login page while logged in, bounce to dashboard
      if (onLoginPage) {
        window.location.href = "/";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", setupAuthUI);
})();

/**
 * Helper to get current user email safely (for skiptrace requests).
 */
function getCurrentUserEmailSafe() {
  try {
    if (typeof firebase === "undefined" || !firebase.auth) return "unknown";
    const user = firebase.auth().currentUser;
    return (user && user.email) || "unknown";
  } catch (e) {
    console.warn("Error reading Firebase user:", e);
    return "unknown";
  }
            }
