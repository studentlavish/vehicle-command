import React, { Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { AuthProvider } from "@/context/AuthContext";
import { I18nProvider } from "@/lib/i18n";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import "@/App.css";

// Login and the dashboard ship in the main bundle (nearly every visit needs them);
// every other page — and heavy libraries like the Recharts-based Analytics — load on demand.
const LiveMonitoring = lazy(() => import("@/pages/LiveMonitoring"));
const VehicleRecords = lazy(() => import("@/pages/VehicleRecords"));
const EntryHistory = lazy(() => import("@/pages/EntryHistory"));
const ExitHistory = lazy(() => import("@/pages/ExitHistory"));
const Reports = lazy(() => import("@/pages/Reports"));
const Analytics = lazy(() => import("@/pages/Analytics"));
const SettingsPage = lazy(() => import("@/pages/Settings"));
const CamerasPage = lazy(() => import("@/pages/Cameras"));
const UsersPage = lazy(() => import("@/pages/Users"));
const VehicleDetail = lazy(() => import("@/pages/VehicleDetail"));
const SearchResults = lazy(() => import("@/pages/SearchResults"));
const NotFound = lazy(() => import("@/pages/NotFound"));
const Reviews = lazy(() => import("@/pages/Reviews"));

function PageFallback() {
  return (
    <div className="space-y-6" data-testid="page-loading">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-64 w-full rounded-xl" />
    </div>
  );
}

function Guarded({ children }) {
  return (
    <ProtectedRoute>
      <Layout>
        <Suspense fallback={<PageFallback />}>{children}</Suspense>
      </Layout>
    </ProtectedRoute>
  );
}

function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guarded><Dashboard /></Guarded>} />
      <Route path="/live" element={<Guarded><LiveMonitoring /></Guarded>} />
      <Route path="/reviews" element={<Guarded><Reviews /></Guarded>} />
      <Route path="/vehicles" element={<Guarded><VehicleRecords /></Guarded>} />
      <Route path="/entry-history" element={<Guarded><EntryHistory /></Guarded>} />
      <Route path="/exit-history" element={<Guarded><ExitHistory /></Guarded>} />
      <Route path="/reports" element={<Guarded><Reports /></Guarded>} />
      <Route path="/analytics" element={<Guarded><Analytics /></Guarded>} />
      <Route path="/cameras" element={<Guarded><CamerasPage /></Guarded>} />
      <Route path="/settings" element={<Guarded><SettingsPage /></Guarded>} />
      <Route path="/users" element={<Guarded><UsersPage /></Guarded>} />
      <Route path="/search" element={<Guarded><SearchResults /></Guarded>} />
      <Route path="/vehicle/:vehicleNumber" element={<Guarded><VehicleDetail /></Guarded>} />
      <Route path="*" element={<Guarded><NotFound /></Guarded>} />
    </Routes>
  );
}

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem storageKey="vc-theme" disableTransitionOnChange>
      <I18nProvider>
        <AuthProvider>
          <BrowserRouter>
            <AppRouter />
          </BrowserRouter>
          <Toaster richColors closeButton position="top-right" />
        </AuthProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
