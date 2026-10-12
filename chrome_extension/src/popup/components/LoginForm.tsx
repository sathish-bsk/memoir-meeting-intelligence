import React, { useState } from "react";
import { sendMessage } from "../../shared/messaging";
import { WEB_BASE } from "../../shared/constants";

interface Props {
  onSuccess: () => void;
  onError: (msg: string) => void;
  error: string | null;
}

export function LoginForm({ onSuccess, onError, error }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;

    setLoading(true);
    const res = await sendMessage({ type: "LOGIN", email, password });
    setLoading(false);

    if (res.success) {
      onSuccess();
    } else {
      onError(res.error ?? "Login failed");
    }
  };

  const handleSignup = () => {
    chrome.tabs.create({ url: `${WEB_BASE}/signup` });
  };

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <div style={styles.logo}>M</div>
        <h1 style={styles.title}>Memoir</h1>
        <p style={styles.subtitle}>Sign in to start recording meetings</p>
      </div>

      <form onSubmit={handleSubmit} style={styles.form}>
        {error && <div style={styles.error}>{error}</div>}

        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={styles.input}
          autoFocus
          required
        />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={styles.input}
          required
        />

        <button
          type="submit"
          disabled={loading || !email || !password}
          style={{
            ...styles.button,
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? "Signing in..." : "Sign In"}
        </button>
      </form>

      <p style={styles.signupText}>
        Don't have an account?{" "}
        <button onClick={handleSignup} style={styles.signupLink}>
          Sign up
        </button>
      </p>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "32px 24px 24px",
  },
  header: {
    textAlign: "center",
    marginBottom: 24,
  },
  logo: {
    width: 48,
    height: 48,
    borderRadius: 12,
    background: "#5C5CE0",
    color: "white",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 24,
    fontWeight: 700,
    marginBottom: 12,
  },
  title: {
    fontSize: 20,
    fontWeight: 700,
    color: "#1F2937",
    margin: "0 0 4px",
  },
  subtitle: {
    fontSize: 13,
    color: "#6B7280",
    margin: 0,
  },
  form: {
    width: "100%",
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  input: {
    width: "100%",
    padding: "10px 12px",
    border: "1px solid #D1D5DB",
    borderRadius: 8,
    fontSize: 13,
    outline: "none",
    fontFamily: "inherit",
    background: "white",
  },
  button: {
    width: "100%",
    padding: "10px 0",
    background: "#5C5CE0",
    color: "white",
    border: "none",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "inherit",
    marginTop: 4,
  },
  error: {
    background: "#FEF2F2",
    color: "#DC2626",
    padding: "8px 12px",
    borderRadius: 8,
    fontSize: 12,
  },
  signupText: {
    marginTop: 20,
    fontSize: 12,
    color: "#6B7280",
  },
  signupLink: {
    background: "none",
    border: "none",
    color: "#5C5CE0",
    fontWeight: 600,
    cursor: "pointer",
    fontSize: 12,
    fontFamily: "inherit",
    textDecoration: "underline",
    padding: 0,
  },
};
