/* Global search placeholder — Ctrl+K palette lands with Phase 1 modules. */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

export default function Search() {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") navigate("/");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-4 text-2xl font-bold">Global search</h1>
      <div className="card">
        <input className="input" placeholder="Searching everywhere…" autoFocus disabled />
        <p className="mt-3 text-sm text-[var(--c-muted)]">
          Ctrl+K search across Library, Manuals, Notes, Docs, Radio and Videos arrives with
          the Phase 1+ modules (lib returns its ZIM index first).
        </p>
        <button className="btn btn-ghost mt-3" onClick={() => navigate("/")}>
          Back to dashboard
        </button>
      </div>
    </div>
  );
}