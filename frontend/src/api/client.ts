import type {
  AdminOverview,
  AdminProject,
  AdminRun,
  AdminSettings,
  AdminUser,
  AssistantReply,
  AuthConfig,
  AuthUser,
  Member,
  Meter,
  ProjectPatch,
  RunBudget,
  UserPatch,
  PredictionPreview,
  ValidationPage,
  ValidationRow,
  ChatTurn,
  SetupState,
  ArtifactEntry,
  ArtifactPreview,
  ContractProposal,
  ContractRecord,
  DatasetProfile,
  DatasetRecord,
  ControlReply,
  EstimatorInfo,
  ExecutionRecord,
  GateSubmission,
  GateSummary,
  JsonObject,
  JsonValue,
  LlmCallRecord,
  MemoryEntry,
  ProjectDetail,
  ProjectSummary,
  RunMode,
  RunExecutions,
  RunSnapshot,
  RunStatus,
  RunSummary,
  SetupValidation,
  ToolCallRecord,
  Vocabulary,
} from './types.ts';

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (typeof value === 'object') return Object.values(value).every(isJsonValue);
  return false;
}

export function isJsonObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && isJsonValue(value);
}

function invalidResponse(): ApiError {
  return new ApiError('Backend returned an invalid response.');
}

function requiredString(object: JsonObject, key: string): string {
  const value = object[key];
  if (typeof value !== 'string') throw invalidResponse();
  return value;
}

function nullableString(object: JsonObject, key: string): string | null {
  const value = object[key];
  if (value !== null && typeof value !== 'string') throw invalidResponse();
  return value;
}

function requiredBoolean(object: JsonObject, key: string): boolean {
  const value = object[key];
  if (typeof value !== 'boolean') throw invalidResponse();
  return value;
}

function isRunMode(value: JsonValue | undefined): value is RunMode {
  return value === 'guided' || value === 'autonomous';
}

function isRunStatus(value: JsonValue | undefined): value is RunStatus {
  return (
    value === 'pending' || value === 'running' || value === 'awaiting_user' || value === 'paused' ||
    value === 'failed' || value === 'cancelled' || value === 'completed' || value === 'superseded'
  );
}

function requiredNumber(object: JsonObject, key: string): number {
  const value = object[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) throw invalidResponse();
  return value;
}

function stringArray(value: JsonValue | undefined): string[] {
  if (!Array.isArray(value)) throw invalidResponse();
  return value.map((item) => {
    if (typeof item !== 'string') throw invalidResponse();
    return item;
  });
}

function stringRecord(value: JsonValue | undefined): Record<string, string> {
  if (!isJsonObject(value)) throw invalidResponse();
  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'string') throw invalidResponse();
    result[key] = item;
  }
  return result;
}

function numberRecord(value: JsonValue | undefined): Record<string, number> {
  if (!isJsonObject(value)) throw invalidResponse();
  const result: Record<string, number> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'number' || !Number.isFinite(item)) throw invalidResponse();
    result[key] = item;
  }
  return result;
}

function parseJsonText(text: string): JsonValue {
  try {
    return JSON.parse(text) as JsonValue;
  } catch {
    throw invalidResponse();
  }
}

function parseJsonObjectText(text: string): JsonObject {
  const parsed = parseJsonText(text);
  if (!isJsonObject(parsed)) throw invalidResponse();
  return parsed;
}

function objectArray(value: unknown): JsonObject[] {
  if (!Array.isArray(value) || !value.every(isJsonObject)) throw invalidResponse();
  return value;
}

function parseProject(project: JsonObject): ProjectSummary {
  return {
    id: requiredString(project, 'id'),
    slug: requiredString(project, 'slug'),
    name: requiredString(project, 'name'),
    created_at: requiredString(project, 'created_at'),
  };
}

export function parseProjects(value: unknown): ProjectSummary[] {
  return objectArray(value).map(parseProject);
}

export function parseProjectRow(value: unknown): ProjectSummary {
  if (!isJsonObject(value)) throw invalidResponse();
  return parseProject(value);
}

function parseRunFields(run: JsonObject, id: string): RunSummary {
  const mode = run.mode;
  const status = run.status;
  if (!isRunMode(mode) || !isRunStatus(status)) throw invalidResponse();
  return {
    id,
    project_id: requiredString(run, 'project_id'),
    started_at: requiredString(run, 'started_at'),
    mode,
    status,
    current_node: nullableString(run, 'current_node'),
    forked_from_run_id: nullableString(run, 'forked_from_run_id'),
    ended_at: nullableString(run, 'ended_at'),
    contract_id: requiredString(run, 'contract_id'),
    champion_model_id: nullableString(run, 'champion_model_id'),
    config_json: requiredString(run, 'config_json'),
    test_scored_at: nullableString(run, 'test_scored_at'),
    test_fingerprint: nullableString(run, 'test_fingerprint'),
  };
}

export function parseRun(value: JsonObject): RunSummary {
  return parseRunFields(value, requiredString(value, 'id'));
}

export function parseRunSnapshot(value: unknown, runId: string): RunSnapshot {
  if (!isJsonObject(value)) throw invalidResponse();
  const run = value.run;
  const state = value.state;
  const gates = value.gates;
  if (!isJsonObject(run) || !isJsonObject(state) || !Array.isArray(gates) || typeof value.execution_failed !== 'boolean' || typeof value.view_only !== 'boolean') {
    throw invalidResponse();
  }
  const id = requiredString(run, 'id');
  if (id !== runId) throw invalidResponse();
  const parsedGates: GateSummary[] = gates.map((gate) => {
    if (!isJsonObject(gate)) throw invalidResponse();
    const gateRunId = requiredString(gate, 'run_id');
    if (gateRunId !== runId) throw invalidResponse();
    return {
      id: requiredString(gate, 'id'),
      run_id: gateRunId,
      node_execution_id: requiredString(gate, 'node_execution_id'),
      status: requiredString(gate, 'status'),
      question_json: requiredString(gate, 'question_json'),
      answer_json: nullableString(gate, 'answer_json'),
      ts: requiredString(gate, 'ts'),
    };
  });
  return {
    run: parseRunFields(run, id),
    state,
    gates: parsedGates,
    execution_failed: value.execution_failed,
    view_only: value.view_only,
  };
}

function parseMemory(entry: JsonObject): MemoryEntry {
  return {
    id: requiredString(entry, 'id'),
    key: requiredString(entry, 'key'),
    value: parseJsonText(requiredString(entry, 'value_json')),
    source_run_id: nullableString(entry, 'source_run_id'),
    ts: requiredString(entry, 'ts'),
  };
}

export function parseProjectDetail(value: unknown): ProjectDetail {
  if (!isJsonObject(value)) throw invalidResponse();
  const project = value.project;
  if (!isJsonObject(project)) throw invalidResponse();
  return {
    project: parseProject(project),
    runs: objectArray(value.runs).map(parseRun),
    switched_runs: stringArray(value.switched_runs),
    memory: objectArray(value.memory).map(parseMemory),
  };
}

export function parseDatasetRecord(value: unknown): DatasetRecord {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    id: requiredString(value, 'id'),
    project_id: requiredString(value, 'project_id'),
    filename: requiredString(value, 'filename'),
    sha256: requiredString(value, 'sha256'),
    n_rows: requiredNumber(value, 'n_rows'),
    n_cols: requiredNumber(value, 'n_cols'),
    created_at: requiredString(value, 'created_at'),
  };
}

export function parseDatasets(value: unknown): DatasetRecord[] {
  return objectArray(value).map(parseDatasetRecord);
}

export function parseContractRow(value: unknown): ContractRecord {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    id: requiredString(value, 'id'),
    project_id: requiredString(value, 'project_id'),
    version: requiredNumber(value, 'version'),
    created_at: requiredString(value, 'created_at'),
    contract: parseJsonObjectText(requiredString(value, 'contract_json')),
  };
}

export function parseContracts(value: unknown): ContractRecord[] {
  return objectArray(value).map(parseContractRow);
}

export function parseProfile(value: unknown): DatasetProfile {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    rows: requiredNumber(value, 'rows'),
    cols: requiredNumber(value, 'cols'),
    columns: stringArray(value.columns),
    dtypes: stringRecord(value.dtypes),
    null_counts: numberRecord(value.null_counts),
    n_unique: numberRecord(value.n_unique),
    roles: stringRecord(value.roles),
    role_evidence: stringRecord(value.role_evidence),
    all_null: stringArray(value.all_null),
  };
}

export function parseVocabulary(value: unknown): Vocabulary {
  if (!isJsonObject(value)) throw invalidResponse();
  const contractSchema = value.contract_schema;
  const registry = value.registry;
  if (!isJsonObject(contractSchema) || !isJsonObject(registry)) throw invalidResponse();
  const estimators: EstimatorInfo[] = [];
  const registered = isJsonObject(registry.estimators) ? registry.estimators : {};
  for (const [name, entry] of Object.entries(registered)) {
    if (!isJsonObject(entry) || typeof entry.task !== 'string') continue;
    estimators.push({ name, task: entry.task });
  }
  estimators.sort((left, right) => (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
  const inferential = registry.inferential_models;
  return {
    contractSchema,
    estimators,
    inferentialModels: isJsonObject(inferential) ? Object.keys(inferential).sort() : [],
  };
}

export function parseValidation(value: unknown): SetupValidation {
  if (!isJsonObject(value) || typeof value.valid !== 'boolean') throw invalidResponse();
  return { valid: value.valid, mismatches: objectArray(value.mismatches), warnings: stringArray(value.warnings) };
}

export function parseContractCheck(value: unknown): JsonObject[] {
  if (!isJsonObject(value) || !Array.isArray(value.errors)) throw invalidResponse();
  return value.errors.map((error: unknown) => {
    if (!isJsonObject(error) || typeof error.path !== 'string' || typeof error.message !== 'string') {
      throw invalidResponse();
    }
    return { field: error.path === '' ? 'contract' : error.path, message: error.message };
  });
}

export function parseProposal(value: unknown): ContractProposal {
  if (!isJsonObject(value) || !isJsonObject(value.contract)) throw invalidResponse();
  return { contract: value.contract, reasoning: requiredString(value, 'reasoning') };
}

export function parseRunAccepted(value: unknown): string {
  if (!isJsonObject(value)) throw invalidResponse();
  return requiredString(value, 'run_id');
}

export function publicCandidateId(value: string | null): string | null {
  return value === null ? null : value.slice(value.lastIndexOf(':') + 1);
}

function lenientJson(text: string): JsonValue | null {
  try {
    return JSON.parse(text) as JsonValue;
  } catch {
    return null;
  }
}

function lenientObject(text: string | null): JsonObject | null {
  if (text === null) return null;
  const parsed = lenientJson(text);
  return isJsonObject(parsed) ? parsed : null;
}

function parseExecution(row: JsonObject): ExecutionRecord {
  return {
    id: requiredString(row, 'id'),
    candidateId: publicCandidateId(nullableString(row, 'candidate_id')),
    node: requiredString(row, 'node_name'),
    seq: requiredNumber(row, 'seq'),
    attempt: requiredNumber(row, 'attempt'),
    status: requiredString(row, 'status'),
    summary: lenientObject(nullableString(row, 'summary')),
    error: nullableString(row, 'error'),
    startedAt: requiredString(row, 'started_at'),
    endedAt: nullableString(row, 'ended_at'),
  };
}

function parseToolCall(row: JsonObject): ToolCallRecord {
  return {
    id: requiredString(row, 'id'),
    executionId: requiredString(row, 'node_execution_id'),
    tool: requiredString(row, 'tool_name'),
    args: lenientJson(requiredString(row, 'args_json')),
    result: lenientJson(requiredString(row, 'result_json')),
    durationMs: requiredNumber(row, 'duration_ms'),
    ts: requiredString(row, 'ts'),
  };
}

function parseLlmCall(row: JsonObject): LlmCallRecord {
  return {
    id: requiredString(row, 'id'),
    executionId: requiredString(row, 'node_execution_id'),
    signature: requiredString(row, 'signature'),
    model: requiredString(row, 'model'),
    promptTokens: requiredNumber(row, 'prompt_tokens'),
    completionTokens: requiredNumber(row, 'completion_tokens'),
    cachedTokens: requiredNumber(row, 'cached_tokens'),
    costUsd: requiredNumber(row, 'cost_usd'),
    durationMs: requiredNumber(row, 'duration_ms'),
    ts: requiredString(row, 'ts'),
  };
}

export function parseExecutions(value: unknown): RunExecutions {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    executions: objectArray(value.executions).map(parseExecution),
    toolCalls: objectArray(value.tool_calls).map(parseToolCall),
    llmCalls: objectArray(value.llm_calls).map(parseLlmCall),
    costUsd: requiredNumber(value, 'cost_usd'),
  };
}

export function parseControlReply(value: unknown): ControlReply {
  if (!isJsonObject(value)) throw invalidResponse();
  const newRunId = value.new_run_id;
  if (newRunId !== undefined && newRunId !== null && typeof newRunId !== 'string') throw invalidResponse();
  return {
    text: requiredString(value, 'text'),
    category: requiredString(value, 'category'),
    newRunId: newRunId ?? null,
  };
}

export function parseAuthUser(value: unknown): AuthUser {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    id: requiredString(value, 'id'),
    email: requiredString(value, 'email'),
    name: requiredString(value, 'name'),
    is_admin: requiredBoolean(value, 'is_admin'),
  };
}

export function parseAuthConfig(value: unknown): AuthConfig {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    signup_open: requiredBoolean(value, 'signup_open'),
    has_users: requiredBoolean(value, 'has_users'),
  };
}

export function parseMeter(value: unknown): Meter {
  if (!isJsonObject(value)) throw invalidResponse();
  return { spent_usd: requiredNumber(value, 'spent_usd'), limit_usd: nullableNumber(value.limit_usd) };
}

export function parseRunBudget(value: unknown): RunBudget {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    run: parseMeter(value.run),
    project: parseMeter(value.project),
    person: parseMeter(value.person),
    started_by: nullableString(value, 'started_by'),
    blocked: nullableString(value, 'blocked'),
  };
}

function parseMember(row: JsonObject): Member {
  const role = row.role;
  if (role !== 'owner' && role !== 'member') throw invalidResponse();
  return {
    user_id: requiredString(row, 'user_id'),
    email: requiredString(row, 'email'),
    name: requiredString(row, 'name'),
    role,
    added_at: requiredString(row, 'added_at'),
  };
}

export function parseMembers(value: unknown): Member[] {
  return objectArray(value).map(parseMember);
}

export function parseAdminSettings(value: unknown): AdminSettings {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    default_monthly_limit_usd: nullableNumber(value.default_monthly_limit_usd),
    default_project_limit_usd: nullableNumber(value.default_project_limit_usd),
    default_run_limit_usd: nullableNumber(value.default_run_limit_usd),
    signup_open: requiredBoolean(value, 'signup_open'),
  };
}

function parseAdminUserRow(row: JsonObject): AdminUser {
  return {
    id: requiredString(row, 'id'),
    email: requiredString(row, 'email'),
    name: requiredString(row, 'name'),
    is_admin: requiredBoolean(row, 'is_admin'),
    disabled: requiredBoolean(row, 'disabled'),
    created_at: requiredString(row, 'created_at'),
    last_login_at: nullableString(row, 'last_login_at'),
    monthly_limit_usd: nullableNumber(row.monthly_limit_usd),
    month: parseMeter(row.month),
    total_spent_usd: requiredNumber(row, 'total_spent_usd'),
    projects: requiredNumber(row, 'projects'),
    runs: requiredNumber(row, 'runs'),
  };
}

export function parseAdminUser(value: unknown): AdminUser {
  if (!isJsonObject(value)) throw invalidResponse();
  return parseAdminUserRow(value);
}

export function parseAdminUsers(value: unknown): AdminUser[] {
  return objectArray(value).map(parseAdminUserRow);
}

function parseAdminProjectRow(row: JsonObject): AdminProject {
  return {
    id: requiredString(row, 'id'),
    name: requiredString(row, 'name'),
    slug: requiredString(row, 'slug'),
    created_at: requiredString(row, 'created_at'),
    owner_id: nullableString(row, 'owner_id'),
    owner_name: nullableString(row, 'owner_name'),
    owner_email: nullableString(row, 'owner_email'),
    members: requiredNumber(row, 'members'),
    runs: requiredNumber(row, 'runs'),
    budget: parseMeter(row.budget),
    limit_usd: nullableNumber(row.limit_usd),
    run_limit_usd: nullableNumber(row.run_limit_usd),
    effective_run_limit_usd: nullableNumber(row.effective_run_limit_usd),
  };
}

export function parseAdminProject(value: unknown): AdminProject {
  if (!isJsonObject(value)) throw invalidResponse();
  return parseAdminProjectRow(value);
}

export function parseAdminProjects(value: unknown): AdminProject[] {
  return objectArray(value).map(parseAdminProjectRow);
}

function parseAdminRunRow(row: JsonObject): AdminRun {
  const status = row.status;
  if (!isRunStatus(status)) throw invalidResponse();
  return {
    id: requiredString(row, 'id'),
    project_id: requiredString(row, 'project_id'),
    project_name: requiredString(row, 'project_name'),
    status,
    started_at: requiredString(row, 'started_at'),
    limit_usd: nullableNumber(row.limit_usd),
    budget: parseRunBudget(row.budget),
  };
}

export function parseAdminRun(value: unknown): AdminRun {
  if (!isJsonObject(value)) throw invalidResponse();
  return parseAdminRunRow(value);
}

export function parseAdminRuns(value: unknown): AdminRun[] {
  return objectArray(value).map(parseAdminRunRow);
}

export function parseAdminOverview(value: unknown): AdminOverview {
  if (!isJsonObject(value)) throw invalidResponse();
  return {
    month: requiredString(value, 'month'),
    month_spent_usd: requiredNumber(value, 'month_spent_usd'),
    total_spent_usd: requiredNumber(value, 'total_spent_usd'),
    users: requiredNumber(value, 'users'),
    admins: requiredNumber(value, 'admins'),
    disabled_users: requiredNumber(value, 'disabled_users'),
    projects: requiredNumber(value, 'projects'),
    runs: requiredNumber(value, 'runs'),
    open_runs: requiredNumber(value, 'open_runs'),
    blocked_runs: objectArray(value.blocked_runs).map(parseAdminRunRow),
    settings: parseAdminSettings(value.settings),
  };
}

function abortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof DOMException && error.name === 'AbortError');
}

const signedOutListeners = new Set<() => void>();

export function onSignedOut(listener: () => void): () => void {
  signedOutListeners.add(listener);
  return () => {
    signedOutListeners.delete(listener);
  };
}

type RequestOptions = {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  json?: JsonValue;
  form?: FormData;
  signal?: AbortSignal | undefined;
  text?: boolean;
  messages?: Partial<Record<number, string>>;
  detail?: boolean;
};

function sentence(text: string): string {
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}${text.endsWith('.') ? '' : '.'}`;
}

async function request(path: string, options: RequestOptions): Promise<unknown> {
  const { method, json, form, signal, text } = options;
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Statlab-Csrf': '1' };
  let body: string | FormData | null = null;
  if (json !== undefined) {
    body = JSON.stringify(json);
    headers['Content-Type'] = 'application/json';
  } else if (form !== undefined) {
    body = form;
  }
  let response: Response;
  try {
    response = await fetch(path, { method, headers, body, signal: signal ?? null });
  } catch (error) {
    if (abortError(error, signal)) throw error;
    throw new ApiError('Cannot reach the backend.');
  }
  if (!response.ok) {
    if (response.status === 401) for (const listener of signedOutListeners) listener();
    let reason: string | null = null;
    if (options.detail === true && (response.status === 409 || response.status === 422)) {
      try {
        const body: unknown = await response.json();
        if (isJsonObject(body) && typeof body.detail === 'string' && body.detail.trim() !== '') reason = sentence(body.detail);
      } catch {
        /* keep the generic message */
      }
    }
    const message = options.messages?.[response.status] ?? reason ?? (response.status === 403 ? 'Locked until the test score is recorded.' :
      response.status === 404 ? 'Resource not found.' :
      response.status === 409 ? 'Run transition rejected.' :
      response.status === 410 ? 'Artifact was pruned.' :
      response.status === 413 ? 'Upload is too large.' :
      response.status === 422 ? 'Invalid request.' :
      response.status === 401 ? 'Sign in to continue.' :
      response.status === 402 ? 'A budget limit is reached. Ask an admin to raise it.' :
      response.status === 429 ? 'Too many failed sign-ins. Try again in 15 minutes.' :
      response.status === 502 ? 'The agent could not produce a valid answer.' :
      response.status === 503 ? 'Backend is unavailable.' : 'Backend request failed.');
    throw new ApiError(message, response.status);
  }
  try {
    if (text === true) return await response.text();
    return await response.json() as unknown;
  } catch (error) {
    if (abortError(error, signal)) throw error;
    throw invalidResponse();
  }
}

export function datasetQuery(options: JsonObject): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (Array.isArray(value)) {
      for (const item of value) if (typeof item === 'string') params.append(key, item);
    } else if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text === '' ? '' : `?${text}`;
}

export async function listProjects(signal?: AbortSignal): Promise<ProjectSummary[]> {
  return parseProjects(await request('/api/projects', { method: 'GET', signal }));
}

export async function getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  if (runId.trim() === '') throw new ApiError('Run ID is required.');
  return parseRunSnapshot(await request(`/api/runs/${encodeURIComponent(runId)}`, { method: 'GET', signal }), runId);
}

function projectPath(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}`;
}

export async function createProject(slug: string, name: string): Promise<ProjectSummary> {
  return parseProjectRow(await request('/api/projects', { method: 'POST', json: { slug, name } }));
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectDetail> {
  return parseProjectDetail(await request(projectPath(projectId), { method: 'GET', signal }));
}

export async function renameProject(projectId: string, name: string): Promise<ProjectSummary> {
  return parseProjectRow(await request(projectPath(projectId), { method: 'PATCH', json: { name } }));
}

export async function deleteProject(projectId: string): Promise<void> {
  await request(projectPath(projectId), {
    method: 'DELETE',
    text: true,
    messages: { 403: 'Only the project owner can delete this project.' },
  });
}

export async function listDatasets(projectId: string, signal?: AbortSignal): Promise<DatasetRecord[]> {
  return parseDatasets(await request(`${projectPath(projectId)}/datasets`, { method: 'GET', signal }));
}

export async function listContracts(projectId: string, signal?: AbortSignal): Promise<ContractRecord[]> {
  return parseContracts(await request(`${projectPath(projectId)}/contracts`, { method: 'GET', signal }));
}

export async function uploadDataset(
  projectId: string,
  file: Blob,
  filename: string,
  options: JsonObject,
): Promise<DatasetRecord> {
  const form = new FormData();
  form.append('file', file, filename);
  return parseDatasetRecord(
    await request(`${projectPath(projectId)}/datasets${datasetQuery(options)}`, { method: 'POST', form }),
  );
}

export async function getDatasetProfile(
  projectId: string,
  datasetId: string,
  options: JsonObject,
  signal?: AbortSignal,
): Promise<DatasetProfile> {
  return parseProfile(
    await request(
      `${projectPath(projectId)}/datasets/${encodeURIComponent(datasetId)}/profile${datasetQuery(options)}`,
      { method: 'GET', signal },
    ),
  );
}

export async function uploadContract(projectId: string, contract: JsonObject): Promise<ContractRecord> {
  const form = new FormData();
  form.append('file', new Blob([JSON.stringify(contract)], { type: 'application/json' }), 'contract.json');
  return parseContractRow(await request(`${projectPath(projectId)}/contracts`, { method: 'POST', form }));
}

export async function validateContract(
  projectId: string,
  datasetId: string,
  contract: JsonObject,
): Promise<SetupValidation> {
  const issues = parseContractCheck(
    await request('/api/contracts/check', { method: 'POST', json: { contract } }),
  );
  if (issues.length > 0) return { valid: false, mismatches: issues, warnings: [] };
  return parseValidation(
    await request(`${projectPath(projectId)}/contracts/validate`, {
      method: 'POST',
      json: { dataset_id: datasetId, contract },
    }),
  );
}

export async function proposeContract(
  projectId: string,
  datasetId: string,
  contract: JsonObject,
): Promise<ContractProposal> {
  return parseProposal(
    await request(`${projectPath(projectId)}/contracts/propose`, {
      method: 'POST',
      json: { dataset_id: datasetId, contract },
    }),
  );
}

export async function getVocabulary(signal?: AbortSignal): Promise<Vocabulary> {
  return parseVocabulary(await request('/api/vocabulary', { method: 'GET', signal }));
}

export async function createRun(
  projectId: string,
  mode: RunMode,
  datasetId: string,
  contractId: string,
): Promise<string> {
  return parseRunAccepted(
    await request(`${projectPath(projectId)}/runs`, {
      method: 'POST',
      json: { mode, dataset_ids: [datasetId], contract_id: contractId },
      detail: true,
    }),
  );
}

export function parseSetupState(value: unknown): SetupState {
  if (!isJsonObject(value)) throw invalidResponse();
  const mode = value.mode ?? null;
  if (mode !== null && !isRunMode(mode)) throw invalidResponse();
  return {
    project_id: nullableString({ ...value, project_id: value.project_id ?? null }, 'project_id'),
    dataset_id: nullableString({ ...value, dataset_id: value.dataset_id ?? null }, 'dataset_id'),
    target: nullableString({ ...value, target: value.target ?? null }, 'target'),
    contract_id: nullableString({ ...value, contract_id: value.contract_id ?? null }, 'contract_id'),
    mode,
  };
}

export function parseAssistantReply(value: unknown): AssistantReply {
  if (!isJsonObject(value)) throw invalidResponse();
  if (typeof value.start !== 'boolean') throw invalidResponse();
  return {
    reply: requiredString(value, 'reply'),
    state: parseSetupState(value.state),
    start: value.start,
    issues: stringArray(value.issues),
  };
}

export async function assistantTurn(messages: ChatTurn[], state: SetupState): Promise<AssistantReply> {
  return parseAssistantReply(await request('/api/assistant/turn', { method: 'POST', json: { messages, state } }));
}

function runPath(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}`;
}

export async function getExecutions(runId: string, signal?: AbortSignal): Promise<RunExecutions> {
  return parseExecutions(await request(`${runPath(runId)}/executions`, { method: 'GET', signal }));
}

export async function answerGate(runId: string, gateId: string, submission: GateSubmission): Promise<ControlReply> {
  const json: JsonObject = { revision: submission.revision, action: submission.action };
  if (submission.edits !== undefined) json.edits = submission.edits;
  if (submission.instruction !== undefined) json.instruction = submission.instruction;
  if (submission.reason !== undefined) json.reason = submission.reason;
  if (submission.target !== undefined) json.target = submission.target;
  return parseControlReply(
    await request(`${runPath(runId)}/gates/${encodeURIComponent(gateId)}`, { method: 'POST', json }),
  );
}

export async function checkGateEdits(runId: string, gateId: string, edits: JsonObject): Promise<JsonObject[]> {
  return parseContractCheck(
    await request(`${runPath(runId)}/gates/${encodeURIComponent(gateId)}/check`, {
      method: 'POST',
      json: { edits },
    }),
  );
}

export async function controlRun(runId: string, action: 'pause' | 'resume' | 'cancel'): Promise<ControlReply> {
  return parseControlReply(await request(`${runPath(runId)}/control`, { method: 'POST', json: { action } }));
}

export async function rewindRun(runId: string, target: string, requestId: string): Promise<string> {
  const value = await request(`${runPath(runId)}/control`, {
    method: 'POST',
    json: { action: 'rewind', target, request_id: requestId },
  });
  if (!isJsonObject(value)) throw invalidResponse();
  return requiredString(value, 'new_run_id');
}

export async function sendMessage(runId: string, text: string): Promise<ControlReply> {
  return parseControlReply(await request(`${runPath(runId)}/messages`, { method: 'POST', json: { text } }));
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function parseArtifactEntries(value: unknown): ArtifactEntry[] {
  if (!Array.isArray(value)) throw invalidResponse();
  return value.map((item): ArtifactEntry => {
    if (!isJsonObject(item) || !isJsonObject(item.artifact) || typeof item.locked !== 'boolean') {
      throw invalidResponse();
    }
    const artifact = item.artifact;
    let label: string | null = null;
    let split: string | null = null;
    let rows: number | null = null;
    let cols: number | null = null;
    const meta = artifact.meta_json;
    if (typeof meta !== 'string') throw invalidResponse();
    try {
      const parsed: unknown = JSON.parse(meta);
      if (isJsonObject(parsed)) {
        label = typeof parsed.label === 'string' ? parsed.label : null;
        split = typeof parsed.split === 'string' ? parsed.split : null;
        rows = finiteOrNull(parsed.rows);
        cols = finiteOrNull(parsed.cols);
      }
    } catch {
      // unparseable meta leaves label, split, rows and cols empty
    }
    return {
      id: requiredString(artifact, 'id'),
      kind: requiredString(artifact, 'kind'),
      label,
      split,
      rows,
      cols,
      producedBy: requiredString(artifact, 'produced_by'),
      ts: requiredString(artifact, 'ts'),
      pruned: nullableString(artifact, 'pruned_at') !== null,
      locked: item.locked,
    };
  });
}

export function parseArtifactPreview(value: unknown): ArtifactPreview {
  if (!isJsonObject(value)) throw invalidResponse();
  const dtypes = stringRecord(value.dtypes);
  return {
    rows: requiredNumber(value, 'rows'),
    cols: requiredNumber(value, 'cols'),
    columns: Object.keys(dtypes),
    dtypes,
    head: objectArray(value.head),
  };
}

function nullableNumber(value: JsonValue | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'number' || !Number.isFinite(value)) throw invalidResponse();
  return value;
}

export function parseValidationPage(value: unknown): ValidationPage {
  if (!isJsonObject(value)) throw invalidResponse();
  if (value.task !== 'classification' && value.task !== 'regression') throw invalidResponse();
  if (!Array.isArray(value.rows)) throw invalidResponse();
  const rows = value.rows.map((item): ValidationRow => {
    if (!isJsonObject(item) || !isJsonObject(item.values)) throw invalidResponse();
    const confidence = nullableNumber(item.confidence);
    const error = nullableNumber(item.error);
    if (item.correct !== null && typeof item.correct !== 'boolean') throw invalidResponse();
    return {
      row: item.row ?? null,
      actual: item.actual ?? null,
      predicted: item.predicted ?? null,
      confidence,
      error,
      correct: item.correct ?? null,
      values: item.values,
    };
  });
  return {
    task: value.task,
    total: requiredNumber(value, 'total'),
    shownTotal: requiredNumber(value, 'shown_total'),
    offset: requiredNumber(value, 'offset'),
    limit: requiredNumber(value, 'limit'),
    correct: nullableNumber(value.correct),
    meanAbsError: nullableNumber(value.mean_abs_error),
    threshold: nullableNumber(value.threshold),
    columns: stringArray(value.columns),
    rows,
  };
}

export async function getValidation(
  runId: string,
  query: { candidate: string; worstFirst: boolean; offset: number; limit: number },
  signal?: AbortSignal,
): Promise<ValidationPage> {
  const params = new URLSearchParams({
    candidate: query.candidate,
    worst_first: String(query.worstFirst),
    offset: String(query.offset),
    limit: String(query.limit),
  });
  return parseValidationPage(
    await request(`${runPath(runId)}/validation?${params.toString()}`, { method: 'GET', signal }),
  );
}

export function parsePredictionPreview(value: unknown): PredictionPreview {
  if (!isJsonObject(value)) throw invalidResponse();
  if (value.task !== 'classification' && value.task !== 'regression') throw invalidResponse();
  return {
    task: value.task,
    rows: requiredNumber(value, 'rows'),
    columns: stringArray(value.columns),
    head: objectArray(value.head),
    file: requiredString(value, 'file'),
    missing: value.missing === undefined ? [] : stringArray(value.missing),
    ignored: value.ignored === undefined ? [] : stringArray(value.ignored),
  };
}

export async function predictUnlabelled(runId: string, signal?: AbortSignal): Promise<PredictionPreview> {
  return parsePredictionPreview(await request(`${runPath(runId)}/predictions`, { method: 'POST', signal, detail: true }));
}

export async function predictUpload(runId: string, file: Blob, filename: string, signal?: AbortSignal): Promise<PredictionPreview> {
  const form = new FormData();
  form.append('file', file, filename);
  return parsePredictionPreview(await request(`${runPath(runId)}/predictions/upload`, { method: 'POST', form, signal, detail: true }));
}

export function artifactUrl(id: string): string {
  return `/api/artifacts/${encodeURIComponent(id)}`;
}

export function runFileUrl(runId: string, name: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/files/${name}`;
}

export async function getRunFile(runId: string, name: string, signal?: AbortSignal): Promise<string> {
  const body = await request(runFileUrl(runId, name), { method: 'GET', signal, text: true });
  if (typeof body !== 'string') throw invalidResponse();
  return body;
}

export async function getRunChart(runId: string, ref: string, signal?: AbortSignal): Promise<unknown> {
  return request(runFileUrl(runId, `plots/${ref}.chart.json`), { method: 'GET', signal });
}

export async function listRunArtifacts(runId: string, signal?: AbortSignal): Promise<ArtifactEntry[]> {
  return parseArtifactEntries(await request(`${runPath(runId)}/artifacts`, { method: 'GET', signal }));
}

export async function previewArtifact(id: string, signal?: AbortSignal): Promise<ArtifactPreview> {
  return parseArtifactPreview(await request(`${artifactUrl(id)}/preview`, { method: 'GET', signal }));
}

export async function getAuthConfig(signal?: AbortSignal): Promise<AuthConfig> {
  return parseAuthConfig(await request('/api/auth/config', { method: 'GET', signal }));
}

export async function getMe(signal?: AbortSignal): Promise<AuthUser> {
  return parseAuthUser(await request('/api/auth/me', { method: 'GET', signal }));
}

export async function signIn(email: string, password: string): Promise<AuthUser> {
  return parseAuthUser(
    await request('/api/auth/login', {
      method: 'POST',
      json: { email, password },
      messages: {
        401: 'Email or password is incorrect.',
        403: 'This account is disabled. Ask an admin.',
      },
    }),
  );
}

export async function register(name: string, email: string, password: string): Promise<AuthUser> {
  return parseAuthUser(
    await request('/api/auth/register', {
      method: 'POST',
      json: { email, name, password },
      messages: {
        403: 'Sign-up is closed. Ask an admin for an account.',
        409: 'An account with this email already exists.',
        422: 'Check the email address and use a password of at least 10 characters.',
      },
    }),
  );
}

export async function signOut(): Promise<void> {
  await request('/api/auth/logout', { method: 'POST', text: true });
}

export async function getMyBudget(signal?: AbortSignal): Promise<Meter> {
  return parseMeter(await request('/api/auth/budget', { method: 'GET', signal }));
}

export async function listMembers(projectId: string, signal?: AbortSignal): Promise<Member[]> {
  return parseMembers(await request(`${projectPath(projectId)}/members`, { method: 'GET', signal }));
}

export async function addMember(projectId: string, email: string): Promise<Member> {
  const value = await request(`${projectPath(projectId)}/members`, {
    method: 'POST',
    json: { email },
    messages: {
      403: 'Only the project owner can add people.',
      404: 'No account uses this email.',
      409: 'This person is already a member.',
    },
  });
  if (!isJsonObject(value)) throw invalidResponse();
  return parseMember(value);
}

export async function removeMember(projectId: string, userId: string): Promise<void> {
  await request(`${projectPath(projectId)}/members/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
    text: true,
    messages: {
      403: 'Only the project owner can remove people.',
      409: 'The project owner cannot be removed.',
    },
  });
}

export async function getRunBudget(runId: string, signal?: AbortSignal): Promise<RunBudget> {
  return parseRunBudget(await request(`${runPath(runId)}/budget`, { method: 'GET', signal }));
}

const adminOnly: Partial<Record<number, string>> = { 403: 'Admins only.' };

export async function getAdminOverview(signal?: AbortSignal): Promise<AdminOverview> {
  return parseAdminOverview(
    await request('/api/admin/overview', { method: 'GET', signal, messages: adminOnly }),
  );
}

export async function listAdminUsers(signal?: AbortSignal): Promise<AdminUser[]> {
  return parseAdminUsers(await request('/api/admin/users', { method: 'GET', signal, messages: adminOnly }));
}

export async function updateAdminUser(userId: string, patch: UserPatch): Promise<AdminUser> {
  return parseAdminUser(
    await request(`/api/admin/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      json: patch,
      messages: {
        ...adminOnly,
        409: 'You cannot remove your own admin rights or disable your own account.',
      },
    }),
  );
}

export async function listAdminProjects(signal?: AbortSignal): Promise<AdminProject[]> {
  return parseAdminProjects(
    await request('/api/admin/projects', { method: 'GET', signal, messages: adminOnly }),
  );
}

export async function updateAdminProject(projectId: string, patch: ProjectPatch): Promise<AdminProject> {
  return parseAdminProject(
    await request(`/api/admin/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH',
      json: patch,
      messages: adminOnly,
    }),
  );
}

export async function changeProjectOwner(projectId: string, email: string): Promise<AdminProject> {
  return parseAdminProject(
    await request(`/api/admin/projects/${encodeURIComponent(projectId)}/owner`, {
      method: 'PUT',
      json: { email },
      messages: { ...adminOnly, 404: 'No account uses this email.' },
    }),
  );
}

export async function listAdminRuns(signal?: AbortSignal): Promise<AdminRun[]> {
  return parseAdminRuns(
    await request('/api/admin/runs?limit=200', { method: 'GET', signal, messages: adminOnly }),
  );
}

export async function updateRunLimit(runId: string, limitUsd: number | null): Promise<AdminRun> {
  return parseAdminRun(
    await request(`/api/admin/runs/${encodeURIComponent(runId)}`, {
      method: 'PATCH',
      json: { limit_usd: limitUsd },
      messages: adminOnly,
    }),
  );
}

export async function getAdminSettings(signal?: AbortSignal): Promise<AdminSettings> {
  return parseAdminSettings(
    await request('/api/admin/settings', { method: 'GET', signal, messages: adminOnly }),
  );
}

export async function saveAdminSettings(settings: AdminSettings): Promise<AdminSettings> {
  return parseAdminSettings(
    await request('/api/admin/settings', { method: 'PUT', json: settings, messages: adminOnly }),
  );
}
