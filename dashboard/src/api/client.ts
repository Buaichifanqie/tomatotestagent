import axios from 'axios';
import { message } from 'antd';
import type {
  TestSession,
  PaginatedResponse,
  CreateSessionRequest,
  TestPlan,
  TestTask,
  SkillDefinition,
  MCPServer,
  MCPRegisterRequest,
  RAGQueryRequest,
  RAGQueryResult,
  RAGIndexRequest,
  TrendsResponse,
  TrendMetric,
  QualitySummary,
  Defect,
  DefectUpdateRequest,
} from '@/types';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token');
      window.location.href = '/login';
    } else if (error.response?.status === 500) {
      message.error(error.response.data?.message || '服务器内部错误，请稍后重试');
    }
    return Promise.reject(error);
  },
);

export const api = {
  sessions: {
    create: (data: CreateSessionRequest) =>
      apiClient.post<TestSession>('/sessions', data).then((r) => r.data),

    get: (id: string) =>
      apiClient.get<TestSession>(`/sessions/${id}`).then((r) => r.data),

    list: (params?: { page?: number; page_size?: number; status?: string }) =>
      apiClient.get<PaginatedResponse<TestSession>>('/sessions', { params }).then((r) => r.data),

    cancel: (id: string) =>
      apiClient.post<TestSession>(`/sessions/${id}/cancel`).then((r) => r.data),
  },

  plans: {
    get: (sessionId: string) =>
      apiClient.get<TestPlan>(`/sessions/${sessionId}/plan`).then((r) => r.data),

    list: (params?: { page?: number; page_size?: number }) =>
      apiClient.get<PaginatedResponse<TestPlan>>('/plans', { params }).then((r) => r.data),
  },

  results: {
    get: (taskId: string) =>
      apiClient.get<TestTask>(`/results/${taskId}`).then((r) => r.data),

    list: (sessionId: string, params?: { page?: number; page_size?: number }) =>
      apiClient.get<PaginatedResponse<TestTask>>(`/sessions/${sessionId}/results`, { params }).then((r) => r.data),
  },

  skills: {
    list: (params?: { status?: string }) =>
      apiClient.get<SkillDefinition[]>('/skills', { params }).then((r) => r.data),

    get: (name: string) =>
      apiClient.get<SkillDefinition>(`/skills/${name}`).then((r) => r.data),
  },

  mcp: {
    list: () =>
      apiClient.get<MCPServer[]>('/mcp/servers').then((r) => r.data),

    register: (data: MCPRegisterRequest) =>
      apiClient.post<MCPServer>('/mcp/servers', data).then((r) => r.data),

    health: (name: string) =>
      apiClient.get<{ status: string }>(`/mcp/servers/${name}/health`).then((r) => r.data),
  },

  rag: {
    index: (data: RAGIndexRequest) =>
      apiClient.post<{ collection: string; indexed_count: number }>('/rag/index', data).then((r) => r.data),

    query: (data: RAGQueryRequest) =>
      apiClient.post<RAGQueryResult[]>('/rag/query', data).then((r) => r.data),
  },

  quality: {
    trends: (metric: TrendMetric = 'pass_rate', days: number = 30) =>
      apiClient.get<TrendsResponse>('/quality/trends', { params: { metric, days } }).then((r) => r.data),

    summary: () =>
      apiClient.get<{ data: QualitySummary }>('/quality/summary').then((r) => r.data.data),
  },

  defects: {
    list: (params?: { page?: number; page_size?: number; category?: string; status?: string }) =>
      apiClient.get<PaginatedResponse<Defect>>('/defects', { params }).then((r) => r.data),

    get: (id: string) =>
      apiClient.get<Defect>(`/defects/${id}`).then((r) => r.data),

    update: (id: string, data: DefectUpdateRequest) =>
      apiClient.patch<Defect>(`/defects/${id}`, data).then((r) => r.data),
  },
};

export default apiClient;
