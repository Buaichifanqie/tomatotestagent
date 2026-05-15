export interface TestSession {
  id: string;
  status: 'pending' | 'planning' | 'executing' | 'analyzing' | 'completed' | 'failed';
  test_type: 'api' | 'web' | 'app';
  skill_name: string;
  environment: string;
  total_tasks: number;
  passed_tasks: number;
  failed_tasks: number;
  flaky_tasks: number;
  skipped_tasks: number;
  created_at: string;
  completed_at?: string;
}

export interface TestTask {
  id: string;
  session_id: string;
  name: string;
  status: 'queued' | 'running' | 'passed' | 'failed' | 'flaky' | 'skipped' | 'retrying';
  category: string;
  duration_ms: number;
  retry_count: number;
  error_message?: string;
  created_at: string;
}

export interface Defect {
  id: string;
  session_id: string;
  task_id: string;
  title: string;
  category: 'bug' | 'flaky' | 'environment' | 'configuration';
  severity: 'critical' | 'major' | 'minor' | 'trivial';
  status: 'open' | 'investigating' | 'fixed' | 'closed';
  error_message: string;
  root_cause?: string;
  created_at: string;
}

export interface SkillDefinition {
  name: string;
  version: string;
  description: string;
  trigger: string;
  required_mcp_servers: string[];
  required_rag_collections: string[];
  status: 'active' | 'degraded' | 'inactive';
}

export interface KnowledgeDocument {
  id: string;
  collection: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface WebSocketStatus {
  connected: boolean;
  session_id?: string;
}

export interface DashboardStats {
  total_sessions: number;
  active_sessions: number;
  total_tasks: number;
  pass_rate: number;
  defects_open: number;
  trends: {
    date: string;
    pass_rate: number;
    total_tasks: number;
  }[];
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreateSessionRequest {
  test_type: 'api' | 'web' | 'app';
  skill_name: string;
  environment: string;
}

export interface TestPlan {
  id: string;
  session_id: string;
  tasks: TestTask[];
  strategy: string;
  created_at: string;
}

export interface MCPServer {
  name: string;
  status: 'healthy' | 'unhealthy' | 'starting';
  version: string;
  last_heartbeat: string;
  tools_count: number;
}

export interface MCPRegisterRequest {
  name: string;
  command: string;
  args: string[];
  env?: Record<string, string>;
}

export interface RAGQueryRequest {
  query: string;
  collections: string[];
  top_k: number;
  filters?: Record<string, string>;
}

export interface RAGQueryResult {
  doc_id: string;
  title: string;
  content: string;
  score: number;
  collection: string;
}

export interface RAGIndexRequest {
  collection: string;
  documents: {
    title: string;
    content: string;
    metadata?: Record<string, unknown>;
  }[];
}

export interface QualityTrend {
  date: string;
  total: number;
  passed: number;
  failed: number;
  flaky: number;
  pass_rate: number;
}

export interface DefectDensityTrend {
  date: string;
  total: number;
  critical: number;
  major: number;
  minor: number;
  trivial: number;
}

export interface CoverageTrend {
  date: string;
  api_coverage: number;
  web_coverage: number;
  app_coverage: number;
  total_coverage: number;
}

export type TrendMetric = 'pass_rate' | 'defect_density' | 'coverage' | 'flaky_rate';

export interface TrendsResponse {
  metric: TrendMetric;
  days: number;
  trends: QualityTrend[] | DefectDensityTrend[] | CoverageTrend[];
}

export interface QualitySummary {
  overall_pass_rate: number;
  total_defects_30d: number;
  total_tests_30d: number;
  pass_rate_change_7d: number;
  defect_change_30d: number;
  latest_coverage: number;
  latest_flaky_rate: number;
  period: string;
}

export interface DefectUpdateRequest {
  status?: 'open' | 'investigating' | 'fixed' | 'closed';
  severity?: 'critical' | 'major' | 'minor' | 'trivial';
  root_cause?: string;
}

export type WebSocketEventType =
  | 'session.started'
  | 'plan.generated'
  | 'task.started'
  | 'task.progress'
  | 'task.completed'
  | 'task.self_healing'
  | 'result.analyzed'
  | 'defect.filed'
  | 'session.completed'
  | 'task.snapshot_saved'
  | 'task.resuming'
  | 'resource.usage'
  | 'quality.trend_update';

export interface WebSocketEvent {
  event_type: WebSocketEventType;
  session_id: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface RAGCollection {
  name: string;
  document_count: number;
  last_index_time: string;
  access: string;
}

export interface APIKeyItem {
  name: string;
  key_preview: string;
  updated_at: string;
}

export interface LLMProviderConfig {
  provider: 'openai' | 'local';
  model: string;
  api_base?: string;
}

export interface DatabaseConfig {
  type: 'sqlite' | 'postgresql';
  host?: string;
  port?: number;
  database?: string;
}

export interface RAGConfig {
  vector_store: 'chromadb' | 'milvus';
  embedding_mode: 'local' | 'api';
  embedding_model: string;
}

export interface ResourceUsage {
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  active_containers: number;
  active_sessions: number;
  disk_percent: number;
  timestamp: string;
}

export interface TaskProgress {
  task_id: string;
  session_id: string;
  progress_percent: number;
  status: string;
  message?: string;
}
