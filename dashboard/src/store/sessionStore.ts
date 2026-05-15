import { create } from 'zustand';
import { api } from '@/api/client';
import type { TestSession, TestPlan, TestTask, CreateSessionRequest, PaginatedResponse } from '@/types';

interface SessionState {
  sessions: TestSession[];
  currentSession: TestSession | null;
  currentPlan: TestPlan | null;
  currentTasks: TestTask[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;

  fetchSessions: (params?: { page?: number; page_size?: number; status?: string }) => Promise<void>;
  createSession: (data: CreateSessionRequest) => Promise<TestSession>;
  getSession: (id: string) => Promise<void>;
  cancelSession: (id: string) => Promise<void>;
  fetchPlan: (sessionId: string) => Promise<void>;
  fetchTasks: (sessionId: string, params?: { page?: number; page_size?: number }) => Promise<void>;
  updateSessionFromEvent: (sessionId: string, updates: Partial<TestSession>) => void;
  updateTaskFromEvent: (taskId: string, updates: Partial<TestTask>) => void;
  reset: () => void;
}

const initialState = {
  sessions: [],
  currentSession: null,
  currentPlan: null,
  currentTasks: [],
  total: 0,
  page: 1,
  pageSize: 10,
  loading: false,
  error: null,
};

export const useSessionStore = create<SessionState>((set) => ({
  ...initialState,

  fetchSessions: async (params) => {
    set({ loading: true, error: null });
    try {
      const data: PaginatedResponse<TestSession> = await api.sessions.list(params);
      set({
        sessions: data.items,
        total: data.total,
        page: data.page,
        pageSize: data.page_size,
        loading: false,
      });
    } catch {
      set({ loading: false, error: '获取会话列表失败' });
    }
  },

  createSession: async (data) => {
    const session = await api.sessions.create(data);
    set((state) => ({
      sessions: [session, ...state.sessions],
      total: state.total + 1,
    }));
    return session;
  },

  getSession: async (id) => {
    set({ loading: true, error: null });
    try {
      const session = await api.sessions.get(id);
      set({ currentSession: session, loading: false });
    } catch {
      set({ loading: false, error: '获取会话详情失败' });
    }
  },

  cancelSession: async (id) => {
    const session = await api.sessions.cancel(id);
    set((state) => ({
      currentSession: state.currentSession?.id === id ? session : state.currentSession,
      sessions: state.sessions.map((s) => (s.id === id ? session : s)),
    }));
  },

  fetchPlan: async (sessionId) => {
    set({ loading: true, error: null });
    try {
      const plan = await api.plans.get(sessionId);
      set({ currentPlan: plan, loading: false });
    } catch {
      set({ loading: false, error: '获取测试计划失败' });
    }
  },

  fetchTasks: async (sessionId, params) => {
    set({ loading: true, error: null });
    try {
      const data = await api.results.list(sessionId, params);
      set({
        currentTasks: data.items,
        total: data.total,
        page: data.page,
        pageSize: data.page_size,
        loading: false,
      });
    } catch {
      set({ loading: false, error: '获取任务列表失败' });
    }
  },

  updateSessionFromEvent: (sessionId, updates) => {
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === sessionId ? { ...s, ...updates } : s,
      ),
      currentSession:
        state.currentSession?.id === sessionId
          ? { ...state.currentSession, ...updates }
          : state.currentSession,
    }));
  },

  updateTaskFromEvent: (taskId, updates) => {
    set((state) => ({
      currentTasks: state.currentTasks.map((t) =>
        t.id === taskId ? { ...t, ...updates } : t,
      ),
    }));
  },

  reset: () => set(initialState),
}));
