// Types and calls for the review API (src/resolver/review_api.py).

export type Decision = "approve" | "reject";
export type Agreement = "same" | "different" | "missing";

export interface Group {
  group: string;
  title: string;
  total: number;
  decided: number;
}

export interface QueueItem {
  id: string;
  kind: "pair" | "split";
  group: string;
  title: string;
  score: number | null;
  record_ids: string[];
  decision: Decision | null;
}

export interface Queue {
  groups: Group[];
  items: QueueItem[];
}

export interface Explanation {
  left_id: string;
  right_id: string;
  decision: string;
  rule: string;
  score: number;
  field_scores: Record<string, number>;
  weights: Record<string, number>;
  checks: Record<string, number>;
  description?: string;
}

export interface SplitEvidence {
  component: string[];
  conflicts: [string, string][];
  rejected_edges: [string, string][];
  clusters: string[][];
  edges: Explanation[];
}

export interface FieldRow {
  field: string;
  values: (string | null)[];
  agreement: Agreement;
}

export interface ItemDetail extends QueueItem {
  records: Record<string, string | null>[];
  fields: FieldRow[];
  evidence: Explanation | SplitEvidence;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // not JSON: keep the status line
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export const getQueue = () => request<Queue>("/api/queue");

export const getItem = (id: string) =>
  request<ItemDetail>(`/api/items/${encodeURIComponent(id)}`);

export const postDecision = (item_id: string, decision: Decision) =>
  request<Record<string, string>>("/api/decisions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id, decision }),
  });
