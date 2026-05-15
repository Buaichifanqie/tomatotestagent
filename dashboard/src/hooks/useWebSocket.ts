import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '@/store';
import { useEventStore } from '@/store/eventStore';
import { useSessionStore } from '@/store/sessionStore';
import { useResourceStore } from '@/store/resourceStore';
import type { WebSocketEvent, ResourceUsage, QualityTrend } from '@/types';

const WS_RECONNECT_INTERVAL_MS = 3000;
const WS_MAX_RETRIES = 10;

function getWebSocketUrl(): string {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
  const wsBase = baseUrl.replace(/^http/, 'ws');
  const token = localStorage.getItem('auth_token');
  const authParam = token ? `?token=${token}` : '';
  return `${wsBase}/ws${authParam}`;
}

function handleSessionEvent(event: WebSocketEvent): void {
  const { session_id: sessionId, data } = event;
  const store = useSessionStore.getState();

  switch (event.event_type) {
    case 'session.started':
    case 'session.completed':
      store.updateSessionFromEvent(sessionId, data as Record<string, unknown>);
      break;

    case 'plan.generated':
      store.updateSessionFromEvent(sessionId, { status: 'executing' });
      break;

    case 'task.started': {
      const taskData = data as { task_id: string; name?: string; status?: string };
      store.updateTaskFromEvent(taskData.task_id, {
        status: (taskData.status || 'running') as 'running',
      });
      break;
    }

    case 'task.progress': {
      const progressData = data as { task_id: string };
      store.updateTaskFromEvent(progressData.task_id, {});
      break;
    }

    case 'task.completed': {
      const completedData = data as {
        task_id: string;
        status: 'passed' | 'failed' | 'flaky' | 'skipped';
      };
      store.updateTaskFromEvent(completedData.task_id, {
        status: completedData.status,
      });
      break;
    }

    case 'task.self_healing': {
      const healingData = data as { task_id: string };
      store.updateTaskFromEvent(healingData.task_id, {
        status: 'retrying',
      });
      break;
    }

    case 'task.snapshot_saved': {
      const snapshotData = data as { task_id: string };
      store.updateTaskFromEvent(snapshotData.task_id, {});
      break;
    }

    case 'task.resuming': {
      const resumeData = data as { task_id: string };
      store.updateTaskFromEvent(resumeData.task_id, {
        status: 'running',
      });
      break;
    }

    case 'result.analyzed':
      store.updateSessionFromEvent(sessionId, { status: 'analyzing' });
      break;

    case 'defect.filed':
      break;
  }
}

function handleResourceEvent(event: WebSocketEvent): void {
  switch (event.event_type) {
    case 'resource.usage': {
      const usage = event.data as unknown as ResourceUsage;
      useResourceStore.getState().updateResourceUsage(usage);
      break;
    }

    case 'quality.trend_update': {
      const trend = event.data as unknown as QualityTrend;
      const store = useResourceStore.getState();
      store.setQualityTrends([...store.qualityTrends, trend]);
      break;
    }
  }
}

function dispatchEvent(event: WebSocketEvent): void {
  useEventStore.getState().addEvent(event);

  switch (event.event_type) {
    case 'session.started':
    case 'session.completed':
    case 'plan.generated':
    case 'task.started':
    case 'task.progress':
    case 'task.completed':
    case 'task.self_healing':
    case 'task.snapshot_saved':
    case 'task.resuming':
    case 'result.analyzed':
    case 'defect.filed':
      handleSessionEvent(event);
      break;

    case 'resource.usage':
    case 'quality.trend_update':
      handleResourceEvent(event);
      break;
  }
}

export function useWebSocket(): { connected: boolean; reconnect: () => void } {
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectRef = useRef<(() => void) | null>(null);
  const setWsStatus = useAppStore((state) => state.setWsStatus);
  const wsStatus = useAppStore((state) => state.wsStatus);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    const url = getWebSocketUrl();
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryCountRef.current = 0;
      setWsStatus({ connected: true });
    };

    ws.onmessage = (messageEvent) => {
      try {
        const parsed = JSON.parse(messageEvent.data) as WebSocketEvent;
        dispatchEvent(parsed);
      } catch {
        console.warn('[WebSocket] Failed to parse message:', messageEvent.data);
      }
    };

    ws.onclose = () => {
      setWsStatus({ connected: false });
      wsRef.current = null;

      if (retryCountRef.current < WS_MAX_RETRIES) {
        retryTimerRef.current = setTimeout(() => {
          retryCountRef.current += 1;
          connectRef.current?.();
        }, WS_RECONNECT_INTERVAL_MS * Math.min(retryCountRef.current + 1, 5));
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [setWsStatus]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const reconnect = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    retryCountRef.current = 0;
    if (wsRef.current) {
      wsRef.current.close();
    }
    connect();
  }, [connect]);

  useEffect(() => {
    connect();

    return () => {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return {
    connected: wsStatus.connected,
    reconnect,
  };
}
