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
export type GateSummary = {
  id: string;
  run_id: string;
  node_execution_id: string;
  status: string;
  question_json: string;
  answer_json: string | null;
  ts: string;
};
export type GateAction =
  | 'approve'
  | 'approve_with_edits'
  | 'instruct'
  | 'approve_all_remaining'
  | 'skip_node'
  | 'rewind'
  | 'abort';
export type GatePhase = 'before' | 'after' | 'failure';
export type GateQuestion = {
  node: string;
  candidateId: string | null;
  phase: GatePhase;
  proposal: JsonObject;
  decisionSchema: JsonObject;
  evidence: JsonValue[];
  reasoning: string;
  editable: string[];
  defaultAction: GateAction;
  options: GateAction[];
  context: string[];
  revision: number;
  history: JsonValue[];
  failure: { errorClass: string; message: string } | null;
};
export type GateSubmission = {
  revision: number;
  action: GateAction;
  edits?: JsonObject;
  instruction?: string;
  reason?: string;
  target?: string;
};
export type ControlReply = { text: string; category: string; newRunId: string | null };
export type ExecutionRecord = {
  id: string;
  candidateId: string | null;
  node: string;
  seq: number;
  attempt: number;
  status: string;
  summary: JsonObject | null;
  error: string | null;
  startedAt: string;
  endedAt: string | null;
};
export type ToolCallRecord = {
  id: string;
  executionId: string;
  tool: string;
  args: JsonValue | null;
  result: JsonValue | null;
  durationMs: number;
  ts: string;
};
export type LlmCallRecord = {
  id: string;
  executionId: string;
  signature: string;
  model: string;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  costUsd: number;
  durationMs: number;
  ts: string;
};
export type RunExecutions = {
  executions: ExecutionRecord[];
  toolCalls: ToolCallRecord[];
  llmCalls: LlmCallRecord[];
  costUsd: number;
};
export type RunSnapshot = {
  run: RunSummary;
  state: JsonObject;
  gates: GateSummary[];
  execution_failed: boolean;
  view_only: boolean;
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
export type ProjectDetail = { project: ProjectSummary; runs: RunSummary[]; switched_runs: string[]; memory: MemoryEntry[] };
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

export type ArtifactEntry = {
  id: string;
  kind: string;
  label: string | null;
  split: string | null;
  rows: number | null;
  cols: number | null;
  producedBy: string;
  ts: string;
  pruned: boolean;
  locked: boolean;
};

export type ArtifactPreview = {
  rows: number;
  cols: number;
  columns: string[];
  dtypes: Record<string, string>;
  head: JsonObject[];
};

export type SetupState = {
  project_id: string | null;
  dataset_id: string | null;
  target: string | null;
  contract_id: string | null;
  mode: RunMode | null;
};
export type ChatTurn = { role: 'user' | 'assistant'; text: string };
export type AssistantReply = { reply: string; state: SetupState; start: boolean; issues: string[] };
export type ValidationRow = {
  row: JsonValue;
  actual: JsonValue;
  predicted: JsonValue;
  confidence: number | null;
  error: number | null;
  correct: boolean | null;
  values: JsonObject;
};
export type ValidationPage = {
  task: 'classification' | 'regression';
  total: number;
  shownTotal: number;
  offset: number;
  limit: number;
  correct: number | null;
  meanAbsError: number | null;
  threshold: number | null;
  columns: string[];
  rows: ValidationRow[];
};
export type PredictionPreview = {
  task: 'classification' | 'regression';
  rows: number;
  columns: string[];
  head: JsonObject[];
  file: string;
  missing: string[];
  ignored: string[];
};

export type AuthUser = { id: string; email: string; name: string; is_admin: boolean };
export type AuthConfig = { signup_open: boolean; has_users: boolean };
export type Meter = { spent_usd: number; limit_usd: number | null };
export type RunBudget = {
  run: Meter;
  project: Meter;
  person: Meter;
  started_by: string | null;
  blocked: string | null;
};
export type MemberRole = 'owner' | 'member';
export type Member = { user_id: string; email: string; name: string; role: MemberRole; added_at: string };
export type AdminSettings = {
  default_monthly_limit_usd: number | null;
  default_project_limit_usd: number | null;
  default_run_limit_usd: number | null;
  signup_open: boolean;
};
export type AdminUser = {
  id: string;
  email: string;
  name: string;
  is_admin: boolean;
  disabled: boolean;
  created_at: string;
  last_login_at: string | null;
  monthly_limit_usd: number | null;
  month: Meter;
  total_spent_usd: number;
  projects: number;
  runs: number;
};
export type AdminProject = {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  owner_id: string | null;
  owner_name: string | null;
  owner_email: string | null;
  members: number;
  runs: number;
  budget: Meter;
  limit_usd: number | null;
  run_limit_usd: number | null;
  effective_run_limit_usd: number | null;
};
export type AdminRun = {
  id: string;
  project_id: string;
  project_name: string;
  status: RunStatus;
  started_at: string;
  limit_usd: number | null;
  budget: RunBudget;
};
export type AdminOverview = {
  month: string;
  month_spent_usd: number;
  total_spent_usd: number;
  users: number;
  admins: number;
  disabled_users: number;
  projects: number;
  runs: number;
  open_runs: number;
  blocked_runs: AdminRun[];
  settings: AdminSettings;
};
export type UserPatch = { is_admin?: boolean; disabled?: boolean; monthly_limit_usd?: number | null };
export type ProjectPatch = { limit_usd?: number | null; run_limit_usd?: number | null };
