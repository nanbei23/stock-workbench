export type AnalysisDepth = "quick" | "standard" | "deep" | "custom";

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled"
  | "cancelling";

export interface AnalysisStartRequest {
  trade_date?: string | null;
  depth?: AnalysisDepth;
  selected_analysts?: string[] | null;
  debate_rounds?: number | null;
  risk_rounds?: number | null;
  model_mode?: string | null;
}

export interface AnalysisStartResponse {
  task_id: string | null;
  status: string;
  message?: string | null;
}

export interface BatchAnalyzeRequest extends AnalysisStartRequest {
  codes: string[];
  mode?: string | null;
}

export interface BatchTaskItem {
  task_id: string;
  code: string;
  name?: string | null;
}

export interface BatchAnalyzeResponse {
  count: number;
  tasks: BatchTaskItem[];
  message: string;
  skipped?: Array<{ code: string; reason: string }> | null;
  rejected?: string[] | null;
  rejected_reason?: string | null;
}

export interface StageStatus {
  status: string;
  name: string;
  icon: string;
}

export interface AnalysisStatusResponse {
  task_id: string;
  code?: string | null;
  name?: string | null;
  status: TaskStatus | string;
  progress: string;
  elapsed?: number | null;
  stages: Record<string, StageStatus>;
  error?: string | null;
  queue_status?: string | null;
}

export interface AnalysisResultResponse {
  task_id: string;
  status: TaskStatus | string;
  code?: string | null;
  name?: string | null;
  elapsed?: number | null;
  result?: Record<string, unknown> | null;
  message?: string | null;
  error?: string | null;
}

export interface ActiveTaskResponse {
  task_id: string | null;
  code?: string | null;
  name?: string | null;
  status?: TaskStatus | string | null;
  progress?: string | null;
  stages?: Record<string, string>;
  depth?: AnalysisDepth | null;
  selected_analysts?: string[] | null;
  debate_rounds?: number | null;
  risk_rounds?: number | null;
}

export interface QueueStatusResponse {
  max_concurrent: number;
  max_queue: number;
  running: number;
  queued: number;
  available_slots: number;
}

export interface TaskActionResponse {
  status: string;
  message?: string | null;
  task_id?: string | null;
}

export interface GbrainSaveRequest {
  slug: string;
  title: string;
  content: string;
}

export interface GbrainSaveResponse {
  status: string;
  message?: string | null;
}

export interface AnalysisTaskListItem {
  task_id: string;
  code?: string | null;
  name?: string | null;
  status: string;
  queue_status?: string | null;
  progress: string;
  queue_position: number;
  error?: string | null;
  depth?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  can_cancel: boolean;
  can_retry: boolean;
}

export interface AnalysisTaskListResponse {
  count: number;
  queue: QueueStatusResponse;
  tasks: AnalysisTaskListItem[];
}
