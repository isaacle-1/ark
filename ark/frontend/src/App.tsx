import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import LogsPage from "./pages/Logs";
import Login from "./pages/Login";
import Settings from "./pages/Settings";
import Search from "./pages/Search";

function AdminGuard() {
  const navigate = useNavigate();
  useEffect(() => {
    let alive = true;
    api
      .get<{ user: { role: string } | null }>("/api/auth/me")
      .then((me) => {
        if (!alive) return;
        if (!me.user || me.user.role !== "admin") navigate("/login");
      })
      .catch(() => alive && navigate("/login"));
    return () => {
      alive = false;
    };
  }, [navigate]);
  return null;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/search" element={<Search />} />
          <Route
            path="/logs"
            element={
              <>
                <AdminGuard />
                <LogsPage />
              </>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}