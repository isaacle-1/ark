/* Global search placeholder — Ctrl+K palette lands with Phase 1 modules. */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Search as SearchIcon } from "lucide-react";
import { Button, Panel, PageTitle } from "../components/ui";

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
      <PageTitle kicker="04 · query" title="Global search" />

      <Panel no="01" title="Search everything">
        <div className="flex items-center gap-2">
          <SearchIcon size={16} className="shrink-0 text-[var(--c-muted)]" />
          <input className="input" placeholder="Searching everywhere…" autoFocus disabled />
        </div>
        <p className="fine mt-3">
          Ctrl+K search across Library, Manuals, Notes, Docs, Radio and Videos arrives with the
          Phase 1+ modules (lib returns its ZIM index first).
        </p>
        <Button onClick={() => navigate("/")} className="mt-3">
          <ArrowLeft size={12} /> back to dashboard
        </Button>
      </Panel>
    </div>
  );
}