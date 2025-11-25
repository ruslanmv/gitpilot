// frontend/components/LoginPage.jsx
import React, { useState, useEffect, useRef } from "react";

/**
 * GitPilot – Enterprise Agentic Login
 * Theme: "Claude Code" / Anthropic Enterprise (Dark + Warm Orange)
 */

export default function LoginPage({ onAuthenticated }) {
  // Auth State
  const [authProcessing, setAuthProcessing] = useState(false);
  const [error, setError] = useState("");
  
  // Mode State: 'loading' | 'web' (Has Secret) | 'device' (No Secret)
  const [mode, setMode] = useState("loading");
  
  // Device Flow State
  const [deviceData, setDeviceData] = useState(null);
  const pollTimer = useRef(null);

  // Web Flow State
  const [missingClientId, setMissingClientId] = useState(false);
  
  // REF FIX: Prevents React StrictMode from running the auth exchange twice
  const processingRef = useRef(false);

  // 1. Initialization Effect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");

    // A. If returning from GitHub (Web Flow Callback)
    if (code) {
      if (!processingRef.current) {
        processingRef.current = true;
        setMode("web"); // Implicitly web mode if we have a code
        consumeOAuthCallback(code, state);
      }
      return;
    }

    // B. Otherwise, check Server Capabilities to decide UI Mode
    fetch("/api/auth/status")
      .then((res) => res.json())
      .then((data) => {
        // If the server has a secret, we prefer Web Flow. 
        // If not, we fallback to Device Flow.
        setMode(data.mode === "web" ? "web" : "device");
      })
      .catch((err) => {
        console.warn("Auth status check failed, defaulting to device flow:", err);
        setMode("device");
      });

    // Cleanup polling on unmount
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ===========================================================================
  // WEB FLOW LOGIC (Standard OAuth2)
  // ===========================================================================

  async function consumeOAuthCallback(code, state) {
    const expectedState = sessionStorage.getItem("gitpilot_oauth_state");
    if (state && expectedState && expectedState !== state) {
      console.warn("OAuth state mismatch - proceeding with caution.");
    }

    setAuthProcessing(true);
    setError("");

    // Clean URL
    window.history.replaceState({}, document.title, window.location.pathname);

    try {
      const response = await fetch("/api/auth/callback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state: state || "" }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.detail || data.error || "Authentication handshake failed.");
      }

      handleSuccess(data);
    } catch (err) {
      console.error("Login Error:", err);
      setError(err instanceof Error ? err.message : "Login failed.");
      setAuthProcessing(false);
    }
  }

  async function handleSignInWithGitHub() {
    setError("");
    setMissingClientId(false);
    setAuthProcessing(true);

    try {
      const response = await fetch("/api/auth/url");
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 404 || response.status === 500) {
           setMissingClientId(true);
           setAuthProcessing(false);
           return;
        }
        throw new Error(data.detail || "Could not reach auth endpoint.");
      }

      if (data.state) {
        sessionStorage.setItem("gitpilot_oauth_state", data.state);
      }

      window.location.href = data.authorization_url;
    } catch (err) {
      console.error("Auth Start Error:", err);
      setError(err instanceof Error ? err.message : "Could not start sign-in.");
      setAuthProcessing(false);
    }
  }

  // ===========================================================================
  // DEVICE FLOW LOGIC (No Client Secret Required)
  // ===========================================================================

  const startDeviceFlow = async () => {
    setError("");
    setAuthProcessing(true);
    try {
      const res = await fetch("/api/auth/device/code", { method: "POST" });
      const data = await res.json();
      
      // Handle Errors
      if (data.error) {
        // Helpful hint for the "400 Bad Request" error caused by disabled Device Flow
        if (data.error.includes("400") || data.error.includes("Bad Request")) {
           throw new Error("Device Flow is disabled in GitHub. Please go to your GitHub App Settings > 'Identifying and authorizing users' and check the box 'Enable Device Flow'.");
        }
        throw new Error(data.error);
      }
      
      if (!data.device_code) throw new Error("Invalid device code response");

      setDeviceData(data);
      setAuthProcessing(false); // Stop "button" loading, show "UI" loading

      // Start Polling
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = setInterval(
        () => checkDeviceToken(data.device_code), 
        (data.interval || 5) * 1000
      );
    } catch (err) {
      setError(err.message);
      setAuthProcessing(false);
    }
  };

  const checkDeviceToken = async (deviceCode) => {
    try {
      const res = await fetch("/api/auth/device/poll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceCode })
      });
      
      // 202 = Pending (Do nothing)
      if (res.status === 202) return;

      // 200 = Success
      if (res.status === 200) {
        const data = await res.json();
        clearInterval(pollTimer.current);
        handleSuccess(data);
        return;
      }

      // 400+ = Error (or expired)
      if (res.status >= 400) {
        const errData = await res.json();
        // If it's just pending/slow_down (sometimes 400), ignore. 
        // If expired or denied, stop polling.
        if (errData.error === "expired_token" || errData.error === "access_denied") {
            clearInterval(pollTimer.current);
            setError(errData.error === "expired_token" ? "Code expired. Please try again." : "Access denied.");
            setDeviceData(null); // Reset UI
        }
      }
    } catch (e) {
      console.error("Poll error", e);
    }
  };

  // ===========================================================================
  // SHARED HELPERS
  // ===========================================================================

  function handleSuccess(data) {
    if (!data.access_token || !data.user) {
      setError("Server returned incomplete session data.");
      return;
    }

    try {
      localStorage.setItem("github_token", data.access_token);
      localStorage.setItem("github_user", JSON.stringify(data.user));
    } catch (e) {
      console.warn("LocalStorage access denied:", e);
    }

    if (typeof onAuthenticated === "function") {
      onAuthenticated({
        access_token: data.access_token,
        user: data.user,
      });
    }
  }

  // --- Design Token System ---
  const theme = {
    bg: "#131316",         
    cardBg: "#1C1C1F",     
    border: "#27272A",     
    accent: "#D95C3D",     
    accentHover: "#C44F32",
    textPrimary: "#EDEDED",
    textSecondary: "#A1A1AA",
    font: '"Söhne", "Inter", -apple-system, sans-serif',
  };

  const styles = {
    container: {
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: theme.bg,
      fontFamily: theme.font,
      color: theme.textPrimary,
      letterSpacing: "-0.01em",
    },
    card: {
      backgroundColor: theme.cardBg,
      width: "100%",
      maxWidth: "440px",
      borderRadius: "12px",
      border: `1px solid ${theme.border}`,
      boxShadow: "0 24px 48px -12px rgba(0, 0, 0, 0.6)",
      padding: "48px 40px",
      textAlign: "center",
      position: "relative",
    },
    logoBadge: {
      width: "48px",
      height: "48px",
      backgroundColor: "rgba(217, 92, 61, 0.15)",
      color: theme.accent,
      borderRadius: "10px",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontSize: "22px",
      fontWeight: "700",
      margin: "0 auto 32px auto",
      border: "1px solid rgba(217, 92, 61, 0.2)",
    },
    h1: {
      fontSize: "24px",
      fontWeight: "600",
      marginBottom: "12px",
      color: theme.textPrimary,
    },
    p: {
      fontSize: "14px",
      color: theme.textSecondary,
      lineHeight: "1.6",
      marginBottom: "40px",
    },
    button: {
      width: "100%",
      height: "48px",
      backgroundColor: theme.accent,
      color: "#FFFFFF",
      border: "none",
      borderRadius: "8px",
      fontSize: "14px",
      fontWeight: "500",
      cursor: (authProcessing || (mode === 'loading')) ? "not-allowed" : "pointer",
      opacity: (authProcessing || (mode === 'loading')) ? 0.7 : 1,
      transition: "background-color 0.2s ease",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      gap: "10px",
      boxShadow: "0 4px 12px rgba(217, 92, 61, 0.25)",
    },
    errorBox: {
      backgroundColor: "rgba(185, 28, 28, 0.15)",
      border: "1px solid rgba(185, 28, 28, 0.3)",
      color: "#FCA5A5",
      padding: "12px",
      borderRadius: "8px",
      fontSize: "13px",
      marginBottom: "24px",
      textAlign: "left",
    },
    configCard: {
      textAlign: "left",
      backgroundColor: "#111",
      border: "1px solid #333",
      padding: "24px",
      borderRadius: "8px",
      marginBottom: "24px",
    },
    codeDisplay: {
      backgroundColor: "#27272A",
      color: theme.accent,
      fontSize: "20px",
      fontWeight: "700",
      padding: "12px",
      borderRadius: "6px",
      textAlign: "center",
      letterSpacing: "2px",
      margin: "12px 0",
      border: `1px dashed ${theme.accent}`,
      cursor: "pointer",
    },
    footer: {
      marginTop: "48px",
      fontSize: "12px",
      color: "#52525B",
    }
  };

  // --- RENDER: Device Flow UI ---
  const renderDeviceFlow = () => {
    if (!deviceData) {
      return (
        <button
          onClick={startDeviceFlow}
          disabled={authProcessing}
          style={styles.button}
          onMouseOver={(e) => !authProcessing && (e.currentTarget.style.backgroundColor = theme.accentHover)}
          onMouseOut={(e) => !authProcessing && (e.currentTarget.style.backgroundColor = theme.accent)}
        >
           {authProcessing ? "Connecting..." : "Sign in with GitHub"}
        </button>
      );
    }

    return (
      <div style={styles.configCard}>
        <h3 style={{marginTop:0, color: '#FFF', fontSize: '16px'}}>Authorize Device</h3>
        <p style={{color: '#AAA', fontSize: '13px', marginBottom:'16px'}}>
          GitPilot needs authorization to access your repositories.
        </p>

        <div style={{marginBottom: '16px'}}>
          <div style={{color: '#AAA', fontSize: '12px', marginBottom: '4px'}}>1. Copy code:</div>
          <div 
            style={styles.codeDisplay}
            onClick={() => {
                navigator.clipboard.writeText(deviceData.user_code);
            }}
            title="Click to copy"
          >
            {deviceData.user_code}
          </div>
        </div>

        <div>
          <div style={{color: '#AAA', fontSize: '12px', marginBottom: '4px'}}>2. Paste at GitHub:</div>
          <a 
            href={deviceData.verification_uri} 
            target="_blank" 
            rel="noreferrer"
            style={{
                display: 'block',
                backgroundColor: '#FFF',
                color: '#000',
                textDecoration: 'none',
                padding: '10px',
                borderRadius: '6px',
                textAlign: 'center',
                fontWeight: '600',
                fontSize: '14px'
            }}
          >
            Open Activation Page ↗
          </a>
        </div>
        
        <div style={{marginTop: '20px', fontSize: '12px', color: '#666', textAlign: 'center', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px'}}>
           <span style={{animation: 'spin 1s linear infinite', display: 'inline-block'}}>↻</span> 
           Waiting for authorization...
           <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
        </div>
      </div>
    );
  };

  // --- RENDER: Config Error ---
  if (missingClientId) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={{...styles.logoBadge, color: "#F59E0B", backgroundColor: "rgba(245, 158, 11, 0.1)", borderColor: "rgba(245, 158, 11, 0.2)"}}>⚠️</div>
          <h1 style={styles.h1}>Configuration Error</h1>
          <p style={styles.p}>Could not connect to GitHub Authentication services.</p>
          <button onClick={() => setMissingClientId(false)} style={{...styles.button, backgroundColor: "#3F3F46"}}>Retry</button>
        </div>
      </div>
    );
  }

  // --- RENDER: Main ---
  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <div style={styles.logoBadge}>GP</div>
        
        <h1 style={styles.h1}>GitPilot Enterprise</h1>
        <p style={styles.p}>
          Agentic AI workflow for your repositories.<br/>
          Secure. Context-aware. Automated.
        </p>

        {error && <div style={styles.errorBox}>{error}</div>}

        {mode === "loading" && (
           <div style={{color: '#666', fontSize: '14px'}}>Initializing...</div>
        )}

        {mode === "web" && (
          <button
            onClick={handleSignInWithGitHub}
            disabled={authProcessing}
            style={styles.button}
            onMouseOver={(e) => !authProcessing && (e.currentTarget.style.backgroundColor = theme.accentHover)}
            onMouseOut={(e) => !authProcessing && (e.currentTarget.style.backgroundColor = theme.accent)}
          >
            {authProcessing ? "Connecting..." : (
               <>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405 1.02 0 2.04.135 3 .405 2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" /></svg>
                Sign in with GitHub
               </>
            )}
          </button>
        )}

        {mode === "device" && renderDeviceFlow()}

        <div style={styles.footer}>
          &copy; {new Date().getFullYear()} GitPilot Inc.
        </div>
      </div>
    </div>
  );
}