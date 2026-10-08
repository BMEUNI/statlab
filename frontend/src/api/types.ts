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
  contract_id: string;
  champion_model_id: string | null;
  config_json: string;
  test_scored_at: string | null;
  test_fingerprint: string | null;
};
export type GateSummary = { id: string; run_id: string; node_execution_id: string; status: string };
export type RunSnapshot = {
  run: RunSummary;
  state: JsonObject;
  gates: GateSummary[];
  execution_failed: boolean;
};
export type RunEvent = { seq: number; run_id: string; ts: string; type: string; payload: JsonObject };
export type DatasetRecord = {
  id: string;
  project_id: string;
  filename: string;
  sha256: string;
  n_rows: number;
  n_cols: number;
  created_at: string;
};
export type ContractRecord = { id: string; project_id: string; version: number; created_at: string; contract: JsonObject };
export type MemoryEntry = { id: string; key: string; value: JsonValue; source_run_id: string | null; ts: string };
export type ProjectDetail = { project: ProjectSummary; runs: RunSummary[]; memory: MemoryEntry[] };
export type DatasetProfile = {
  rows: number;
  cols: number;
  columns: string[];
  dtypes: Record<string, string>;
  null_counts: Record<string, number>;
  n_unique: Record<string, number>;
  roles: Record<string, string>;
  role_evidence: Record<string, string>;
  all_null: string[];
};
export type EstimatorInfo = { name: string; task: string };
export type Vocabulary = { contractSchema: JsonObject; estimators: EstimatorInfo[]; inferentialModels: string[] };
export type SetupValidation = { valid: boolean; mismatches: JsonObject[]; warnings: string[] };
export type ContractProposal = { contract: JsonObject; reasoning: string };
