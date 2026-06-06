import type {
  ActiveTaskResponse,
  AnalysisResultResponse,
  AnalysisStartRequest,
  AnalysisStartResponse,
  AnalysisStatusResponse,
  AnalysisTaskListResponse,
  BatchAnalyzeRequest,
  BatchAnalyzeResponse,
  GbrainSaveRequest,
  GbrainSaveResponse,
  QueueStatusResponse,
  TaskActionResponse
} from "./contracts/ai";
import { getJson, postJson } from "./lib/api";

export const AiTaskClient = {
  start(code: string, payload: AnalysisStartRequest = {}): Promise<AnalysisStartResponse> {
    return postJson<AnalysisStartResponse, AnalysisStartRequest>(`/api/ai/analyze/${encodeURIComponent(code)}`, payload);
  },

  batch(payload: BatchAnalyzeRequest): Promise<BatchAnalyzeResponse> {
    return postJson<BatchAnalyzeResponse, BatchAnalyzeRequest>("/api/ai/batch-analyze", payload);
  },

  status(taskId: string): Promise<AnalysisStatusResponse> {
    return getJson<AnalysisStatusResponse>(`/api/ai/analyze/${encodeURIComponent(taskId)}/status`);
  },

  result(taskId: string): Promise<AnalysisResultResponse> {
    return getJson<AnalysisResultResponse>(`/api/ai/analyze/${encodeURIComponent(taskId)}/result`);
  },

  cancel(taskId: string): Promise<TaskActionResponse> {
    return postJson<TaskActionResponse>(`/api/ai/analyze/${encodeURIComponent(taskId)}/cancel`);
  },

  resume(taskId: string): Promise<TaskActionResponse> {
    return postJson<TaskActionResponse>(`/api/ai/analyze/${encodeURIComponent(taskId)}/resume`);
  },

  queueStatus(): Promise<QueueStatusResponse> {
    return getJson<QueueStatusResponse>("/api/ai/queue/status");
  },

  activeTask(): Promise<ActiveTaskResponse> {
    return getJson<ActiveTaskResponse>("/api/ai/active-task");
  },

  stream(taskId: string): EventSource {
    return new EventSource(`/api/ai/analyze/${encodeURIComponent(taskId)}/stream`);
  },

  saveToGbrain(payload: GbrainSaveRequest): Promise<GbrainSaveResponse> {
    return postJson<GbrainSaveResponse, GbrainSaveRequest>("/api/ai/gbrain/save", payload);
  },

  tasks(params?: { limit?: number; status?: string }): Promise<AnalysisTaskListResponse> {
    const search = new URLSearchParams();
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.status) search.set("status", params.status);
    const query = search.toString() ? `?${search.toString()}` : "";
    return getJson<AnalysisTaskListResponse>(`/api/ai/tasks${query}`);
  },

  retry(taskId: string): Promise<TaskActionResponse> {
    return postJson<TaskActionResponse>(`/api/ai/tasks/${encodeURIComponent(taskId)}/retry`);
  },

  cancelFromCenter(taskId: string): Promise<TaskActionResponse> {
    return postJson<TaskActionResponse>(`/api/ai/tasks/${encodeURIComponent(taskId)}/cancel`);
  }
};

declare global {
  interface Window {
    AiTaskClient: typeof AiTaskClient;
  }
}

window.AiTaskClient = AiTaskClient;
