import type {
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
  if (!isJsonObject(run) || !isJsonObject(state) || !Array.isArray(gates) || typeof value.execution_failed !== 'boolean') {
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

function abortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof DOMException && error.name === 'AbortError');
}

type RequestOptions = { method: 'GET' | 'POST'; json?: JsonValue; form?: FormData; signal?: AbortSignal | undefined };

async function request(path: string, options: RequestOptions): Promise<unknown> {
  const { method, json, form, signal } = options;
  const headers: Record<string, string> = { Accept: 'application/json' };
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
    const message = response.status === 403 ? 'Locked until the test score is recorded.' :
      response.status === 404 ? 'Resource not found.' :
      response.status === 409 ? 'Run transition rejected.' :
      response.status === 410 ? 'Artifact was pruned.' :
      response.status === 413 ? 'Upload is too large.' :
      response.status === 422 ? 'Invalid request.' :
      response.status === 502 ? 'The agent could not produce a valid answer.' :
      response.status === 503 ? 'Backend is unavailable.' : 'Backend request failed.';
    throw new ApiError(message, response.status);
  }
  try {
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
    }),
  );
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
