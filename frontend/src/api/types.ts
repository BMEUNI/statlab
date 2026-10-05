export type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject;
export type JsonObject = { [key: string]: JsonValue };

export type ProjectSummary = { id: string; slug: string; name: string; created_at: string };
export type RunMode = 'guided' | 'autonomous';
export type RunStatus =
  | 'pending'
  | 'running'
  | 'awaiting_user'
  | 'paused'
  | 'failed'
  | 'cancelled'
  | 'completed'
  | 'superseded';
export type RunSummary = {
  id: string;
  project_id: string;
  started_at: string;
  mode: RunMode;
  status: RunStatus;
  current_node: string | null;
  forked_from_run_id: string | null;
  ended_at: string | null;
};
export type GateSummary = { id: string; run_id: string; node_execution_id: string; status: string };
export type RunSnapshot = {
  run: RunSummary;
  state: JsonObject;
  gates: GateSummary[];
  execution_failed: boolean;
};
export type RunEvent = { seq: number; run_id: string; ts: string; type: string; payload: JsonObject };
