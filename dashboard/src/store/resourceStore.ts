import { create } from 'zustand';
import type { ResourceUsage, QualitySummary, QualityTrend, DefectDensityTrend, CoverageTrend } from '@/types';

interface ResourceState {
  resourceUsage: ResourceUsage | null;
  qualitySummary: QualitySummary | null;
  qualityTrends: QualityTrend[];
  passRateTrends: QualityTrend[];
  defectDensityTrends: DefectDensityTrend[];
  coverageTrends: CoverageTrend[];
  loading: boolean;
  error: string | null;

  updateResourceUsage: (usage: ResourceUsage) => void;
  setQualitySummary: (summary: QualitySummary) => void;
  setQualityTrends: (trends: QualityTrend[]) => void;
  setPassRateTrends: (trends: QualityTrend[]) => void;
  setDefectDensityTrends: (trends: DefectDensityTrend[]) => void;
  setCoverageTrends: (trends: CoverageTrend[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const initialState = {
  resourceUsage: null,
  qualitySummary: null,
  qualityTrends: [],
  passRateTrends: [],
  defectDensityTrends: [],
  coverageTrends: [],
  loading: false,
  error: null,
};

export const useResourceStore = create<ResourceState>((set) => ({
  ...initialState,

  updateResourceUsage: (usage) => {
    set({ resourceUsage: usage });
  },

  setQualitySummary: (summary) => {
    set({ qualitySummary: summary });
  },

  setQualityTrends: (trends) => {
    set({ qualityTrends: trends });
  },

  setPassRateTrends: (trends) => {
    set({ passRateTrends: trends });
  },

  setDefectDensityTrends: (trends) => {
    set({ defectDensityTrends: trends });
  },

  setCoverageTrends: (trends) => {
    set({ coverageTrends: trends });
  },

  setLoading: (loading) => {
    set({ loading });
  },

  setError: (error) => {
    set({ error });
  },

  reset: () => set(initialState),
}));
