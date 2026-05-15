import { create } from 'zustand';
import type { WebSocketStatus, DashboardStats } from '@/types';

interface AppState {
  sidebarCollapsed: boolean;
  wsStatus: WebSocketStatus;
  dashboardStats: DashboardStats | null;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setWsStatus: (status: WebSocketStatus) => void;
  setDashboardStats: (stats: DashboardStats) => void;
}

export const useAppStore = create<AppState>((set) => ({
  sidebarCollapsed: false,
  wsStatus: { connected: false },
  dashboardStats: null,

  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setWsStatus: (status) => set({ wsStatus: status }),
  setDashboardStats: (stats) => set({ dashboardStats: stats }),
}));
