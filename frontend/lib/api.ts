export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Citation {
  marker: number;
  citation: string;
  supporting_quote: string;
}

export interface RetrievedChunk {
  chunk_id: string;
  citation: string;
  document_title: string;
  source_url: string;
  text: string;
  lexical_rank: number | null;
  dense_rank: number | null;
  fused_score: number;
}

export interface QueryResponse {
  trace_id: string;
  question: string;
  answerable: boolean;
  answer: string;
  refusal_reason: string;
  citations: Citation[];
  retrieved: RetrievedChunk[];
  latency_ms: number;
  cost_usd: number;
  model: string;
}

export interface DocumentSummary {
  celex_id: string;
  short_title: string;
  title: string;
  language: string;
  version: string;
  source_url: string;
  chunk_count: number;
}

export interface EvalRun {
  id: string;
  git_sha: string | null;
  index_version: string;
  case_count: number;
  metrics: Record<string, number>;
  passed: boolean;
  created_at: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export function ask(question: string, language: string | null): Promise<QueryResponse> {
  return request<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify({ question, language }),
  });
}

export function listDocuments(): Promise<DocumentSummary[]> {
  return request<DocumentSummary[]>("/api/documents");
}

export function listEvalRuns(): Promise<EvalRun[]> {
  return request<EvalRun[]>("/api/evaluation/runs");
}
