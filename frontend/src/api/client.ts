import type { GateSummary, JsonObject, JsonValue, ProjectSummary, RunMode, RunSnapshot, RunStatus } from './types.ts';

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

export function parseProjects(value: unknown): ProjectSummary[] {
  if (!Array.isArray(value) || !value.every(isJsonObject)) throw invalidResponse();
  return value.map((project) => ({
    id: requiredString(project, 'id'),
    slug: requiredString(project, 'slug'),
    name: requiredString(project, 'name'),
    created_at: requiredString(project, 'created_at'),
  }));
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
  const mode = run.mode;
  const status = run.status;
  if (!isRunMode(mode) || !isRunStatus(status)) throw invalidResponse();
  const parsedGates: GateSummary[] = gates.map((gate) => {
    if (!isJsonObject(gate)) throw invalidResponse();
    const gateRunId = requiredString(gate, 'run_id');
    if (gateRunId !== runId) throw invalidResponse();
    return {
      id: requiredString(gate, 'id'),
      run_id: gateRunId,
      node_execution_id: requiredString(gate, 'node_execution_id'),
      status: requiredString(gate, 'status'),
    };
  });
  return {
    run: {
      id,
      project_id: requiredString(run, 'project_id'),
      started_at: requiredString(run, 'started_at'),
      mode,
      status,
      current_node: nullableString(run, 'current_node'),
      forked_from_run_id: nullableString(run, 'forked_from_run_id'),
      ended_at: nullableString(run, 'ended_at'),
    },
    state,
    gates: parsedGates,
    execution_failed: value.execution_failed,
  };
}

function abortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof DOMException && error.name === 'AbortError');
}

async function requestJson(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, { method: 'GET', headers: { Accept: 'application/json' }, signal: signal ?? null });
  } catch (error) {
    if (abortError(error, signal)) throw error;
    throw new ApiError('Cannot reach the backend.');
  }
  if (!response.ok) {
    const message = response.status === 404 ? 'Resource not found.' :
      response.status === 409 ? 'Run transition rejected.' :
      response.status === 422 ? 'Invalid request.' :
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

export async function listProjects(signal?: AbortSignal): Promise<ProjectSummary[]> {
  return parseProjects(await requestJson('/api/projects', signal));
}

export async function getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  if (runId.trim() === '') throw new ApiError('Run ID is required.');
  return parseRunSnapshot(await requestJson(`/api/runs/${encodeURIComponent(runId)}`, signal), runId);
}
