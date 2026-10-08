import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { ApiError, api } from "../api/client";
import { useToast } from "../lib/toast";
import { Button, Field, Panel } from "../components/ui";

export default function Login() {
  const { notifyError } = useToast();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/auth/login", { username, password });
      navigate("/");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        notifyError(e, "Invalid username or password");
      } else {
        notifyError(e, "Login failed");
      }
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <Panel className="w-full max-w-sm">
        <header className="panel-head">
          <div className="flex items-baseline gap-2">
            <KeyRound size={14} className="translate-y-[2px] text-[var(--c-accent)]" />
            <span className="panel-head-title">Authentication required</span>
          </div>
          <span className="panel-head-meta">ark access control</span>
        </header>
        <div className="panel-body">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Field label="username">
              <input
                className="input"
                autoComplete="username"
                placeholder="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </Field>
            <Field label="password">
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                placeholder="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            <Button variant="primary" type="submit" disabled={busy} data-testid="login-btn" className="mt-1 w-full">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
          <p className="fine mt-3">
            First login: the generated admin credentials are in{" "}
            <code>data/config/initial-credentials.txt</code> on the server.
          </p>
        </div>
      </Panel>
    </div>
  );
}