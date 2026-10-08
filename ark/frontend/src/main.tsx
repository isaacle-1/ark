import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./lib/toast";
import { applyTheme, getTheme } from "./lib/theme";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing");

applyTheme(getTheme());

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ErrorBoundary>
  </StrictMode>,
);

// Service worker: production only (dev server has HMR + proxies).
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}