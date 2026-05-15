import { create } from 'zustand';
import type { WebSocketEvent, WebSocketEventType } from '@/types';

interface EventState {
  events: WebSocketEvent[];
  latestEvent: WebSocketEvent | null;
  maxEvents: number;

  addEvent: (event: WebSocketEvent) => void;
  clearEvents: () => void;
  getEventsByType: (eventType: WebSocketEventType) => WebSocketEvent[];
  getEventsBySession: (sessionId: string) => WebSocketEvent[];
}

export const useEventStore = create<EventState>((set, get) => ({
  events: [],
  latestEvent: null,
  maxEvents: 200,

  addEvent: (event) => {
    set((state) => {
      const updated = [...state.events, event];
      if (updated.length > state.maxEvents) {
        updated.splice(0, updated.length - state.maxEvents);
      }
      return {
        events: updated,
        latestEvent: event,
      };
    });
  },

  clearEvents: () => {
    set({ events: [], latestEvent: null });
  },

  getEventsByType: (eventType) => {
    return get().events.filter((e) => e.event_type === eventType);
  },

  getEventsBySession: (sessionId) => {
    return get().events.filter((e) => e.session_id === sessionId);
  },
}));
