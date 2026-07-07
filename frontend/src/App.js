import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import LiveMonitoring from "@/pages/LiveMonitoring";
import VehicleRecords from "@/pages/VehicleRecords";
import EntryHistory from "@/pages/EntryHistory";
import ExitHistory from "@/pages/ExitHistory";
import Reports from "@/pages/Reports";
import Analytics from "@/pages/Analytics";
import SettingsPage from "@/pages/Settings";
import UsersPage from "@/pages/Users";
import { Toaster } from "@/components/ui/sonner";
import "@/App.css";

function Guarded({ children }) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
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
        </Routes>
      </BrowserRouter>
      <Toaster richColors position="top-right" theme="dark" />
    </AuthProvider>
  );
}
