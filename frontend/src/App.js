import React from "react";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import AuthCallback from "@/pages/AuthCallback";
import Dashboard from "@/pages/Dashboard";
import LiveMonitoring from "@/pages/LiveMonitoring";
import VehicleRecords from "@/pages/VehicleRecords";
import EntryHistory from "@/pages/EntryHistory";
import ExitHistory from "@/pages/ExitHistory";
import Reports from "@/pages/Reports";
import Analytics from "@/pages/Analytics";
import SettingsPage from "@/pages/Settings";
import UsersPage from "@/pages/Users";
import VehicleDetail from "@/pages/VehicleDetail";
import SearchResults from "@/pages/SearchResults";
import { Toaster } from "@/components/ui/sonner";
import "@/App.css";

function Guarded({ children }) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  );
}

function AppRouter() {
  const location = useLocation();
  // OAuth session_id arrives in the URL fragment — detect synchronously during
  // render (not useEffect) so the exchange happens before any guard runs.
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guarded><Dashboard /></Guarded>} />
      <Route path="/live" element={<Guarded><LiveMonitoring /></Guarded>} />
      <Route path="/vehicles" element={<Guarded><VehicleRecords /></Guarded>} />
      <Route path="/entry-history" element={<Guarded><EntryHistory /></Guarded>} />
      <Route path="/exit-history" element={<Guarded><ExitHistory /></Guarded>} />
      <Route path="/reports" element={<Guarded><Reports /></Guarded>} />
      <Route path="/analytics" element={<Guarded><Analytics /></Guarded>} />
      <Route path="/settings" element={<Guarded><SettingsPage /></Guarded>} />
      <Route path="/users" element={<Guarded><UsersPage /></Guarded>} />
      <Route path="/search" element={<Guarded><SearchResults /></Guarded>} />
      <Route path="/vehicle/:vehicleNumber" element={<Guarded><VehicleDetail /></Guarded>} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
      <Toaster richColors position="top-right" theme="dark" />
    </AuthProvider>
  );
}
