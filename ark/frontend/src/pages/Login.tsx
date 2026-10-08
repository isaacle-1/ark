import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { useToast } from "../lib/toast";

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
    <div className="mx-auto mt-10 max-w-sm">
      <div className="card">
        <div className="mb-4 flex items-center gap-2">
          <img src="/icon.svg" alt="" className="h-8 w-8" />
          <div>
            <div className="text-lg font-bold">ARK</div>
            <div className="text-xs text-[var(--c-muted)]">admin sign-in</div>
          </div>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <input
            className="input"
            autoComplete="username"
            placeholder="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            placeholder="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button className="btn btn-primary justify-center" disabled={busy} data-testid="login-btn">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="mt-3 text-xs text-[var(--c-muted)]">
          First login: the generated admin credentials are in{" "}
          <code>data/config/initial-credentials.txt</code> on the server.
        </p>
      </div>
    </div>
  );
}