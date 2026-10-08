/* Toast notifications — API failures surface the request_id here. */
import { createContext, useCallback, useContext, useRef, useState, ReactNode } from "react";
import type { ApiError } from "../api/client";

export type ToastKind = "info" | "error" | "success";

interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}

interface ToastCtx {
  notify: (text: string, kind?: ToastKind) => void;
  notifyError: (e: unknown, fallback?: string) => void;
}

const Ctx = createContext<ToastCtx | null>(null);

export function useToast(): ToastCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    const tm = timers.current.get(id);
    if (tm) window.clearTimeout(tm);
    timers.current.delete(id);
  }, []);

  const notify = useCallback(
    (text: string, kind: ToastKind = "info") => {
      const id = nextId++;
      setToasts((t) => [...t.slice(-5), { id, kind, text }]);
      const tm = window.setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4000);
      timers.current.set(id, tm);
    },
    [dismiss],
  );

  const notifyError = useCallback(
    (e: unknown, fallback = "Request failed") => {
      if (e instanceof Error && (e as ApiError).status) {
        const err = e as ApiError;
        notify(
          `${err.message ?? fallback}${err.requestId ? ` (request_id: ${err.requestId})` : ""}`,
          "error",
        );
      } else {
        notify(`${fallback}: ${e instanceof Error ? e.message : String(e)}`, "error");
      }
    },
    [notify],
  );

  const tone: Record<ToastKind, string> = {
    info: "toast",
    success: "toast toast-success",
    error: "toast toast-error",
  };

  return (
    <Ctx.Provider value={{ notify, notifyError }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <button
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={tone[t.kind]}
            title="Click to dismiss"
          >
            {t.text}
          </button>
        ))}
      </div>
    </Ctx.Provider>
  );
}