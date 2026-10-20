import type { ReactNode } from "react";
import type { Agreement, Explanation, FieldRow, ItemDetail, SplitEvidence } from "./api";

const FIELD_LABELS: Record<string, string> = {
  first_name: "First name",
  last_name: "Last name",
  email: "Email",
  email_verified: "Email verified",
  phone: "Phone",
  address: "Street",
  city: "City",
  state: "State",
  zip: "ZIP",
  dob: "Date of birth",
  source: "Source",
  updated_at: "Updated",
};

const SCORE_LABELS: Record<string, string> = {
  email: "Email",
  phone: "Phone",
  name: "Name",
  address: "Address",
};

const AGREEMENT: Record<Agreement, { text: string; icon: string; className: string }> = {
  same: {
    text: "Same",
    icon: "=",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-800",
  },
  different: {
    text: "Differs",
    icon: "≠",
    className: "bg-amber-50 text-amber-900 ring-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-700",
  },
  missing: {
    text: "Not comparable",
    icon: "–",
    className: "bg-stone-100 text-stone-600 ring-stone-200 dark:bg-stone-800 dark:text-stone-300 dark:ring-stone-700",
  },
};

function AgreementBadge({ agreement }: { agreement: Agreement }) {
  const a = AGREEMENT[agreement];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${a.className}`}>
      <span aria-hidden="true" className="font-mono">{a.icon}</span>
      {a.text}
    </span>
  );
}

function Value({ value }: { value: string | null }) {
  if (!value) {
    return (
      <span className="text-stone-400 dark:text-stone-500">
        <span aria-hidden="true">—</span>
        <span className="sr-only">missing</span>
      </span>
    );
  }
  return <span className="break-all">{value}</span>;
}

function RecordsTable({ detail }: { detail: ItemDetail }) {
  const conflicts = new Set(
    detail.kind === "split"
      ? (detail.evidence as SplitEvidence).conflicts.flat()
      : [],
  );
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Records side by side, one column per record</caption>
        <thead className="border-b border-stone-200 bg-stone-50 text-xs uppercase tracking-wide text-stone-500 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-400">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Field</th>
            {detail.records.map((r) => (
              <th key={r.record_id} scope="col" className="px-3 py-2 font-medium normal-case tracking-normal">
                <span className="font-mono text-stone-800 dark:text-stone-100">{r.record_id}</span>
                {conflicts.has(r.record_id ?? "") && (
                  <span className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-800 dark:bg-rose-950 dark:text-rose-200">
                    in a conflict
                  </span>
                )}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 font-medium">Agreement</th>
          </tr>
        </thead>
        <tbody>
          {detail.fields.map((row: FieldRow) => (
            <tr
              key={row.field}
              className={`border-b border-stone-100 last:border-0 dark:border-stone-800 ${
                row.agreement === "different" ? "bg-amber-50/60 dark:bg-amber-950/30" : ""
              }`}
            >
              <th scope="row" className="whitespace-nowrap px-3 py-2 font-medium text-stone-600 dark:text-stone-300">
                {FIELD_LABELS[row.field] ?? row.field}
              </th>
              {row.values.map((v, i) => (
                <td key={i} className="px-3 py-2 font-mono text-[13px]">
                  <Value value={v} />
                </td>
              ))}
              <td className="px-3 py-2">
                <AgreementBadge agreement={row.agreement} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScoreBar({ label, value, note }: { label: string; value: number; note?: string }) {
  const pct = Math.round(value * 100);
  return (
    <div className="grid grid-cols-[7rem_1fr_3.5rem] items-center gap-3 text-sm">
      <span className="text-stone-600 dark:text-stone-300">
        {label}
        {note && <span className="block text-xs text-stone-400 dark:text-stone-500">{note}</span>}
      </span>
      <span aria-hidden="true" className="h-2 rounded-full bg-stone-200 dark:bg-stone-800">
        <span
          className={`block h-2 rounded-full ${value === 1 ? "bg-emerald-600" : "bg-blue-600 dark:bg-blue-500"}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-right font-mono tabular-nums">{value.toFixed(2)}</span>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900"
    >
      <h3 className="mb-3 text-sm font-semibold text-stone-700 dark:text-stone-200">{title}</h3>
      {children}
    </section>
  );
}

function PairEvidence({ e }: { e: Explanation }) {
  const fields = Object.keys(SCORE_LABELS).filter((k) => k in e.field_scores);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Field scores">
        <div className="space-y-2">
          {fields.map((k) => (
            <ScoreBar
              key={k}
              label={SCORE_LABELS[k]}
              value={e.field_scores[k]}
              note={e.weights[k] !== undefined ? `weight ${e.weights[k].toFixed(2)}` : undefined}
            />
          ))}
          {fields.length === 0 && <p className="text-sm text-stone-500">No field present on both records.</p>}
        </div>
        <p className="mt-3 border-t border-stone-100 pt-3 text-sm dark:border-stone-800">
          Weighted score <span className="font-mono tabular-nums">{e.score.toFixed(2)}</span>
          <span className="text-stone-500 dark:text-stone-400"> (missing fields excluded)</span>
        </p>
      </Panel>
      <Panel title="Rule that fired">
        <p className="font-mono text-sm">{e.rule}</p>
        <dl className="mt-3 space-y-2 text-sm">
          {e.checks.first_name !== undefined && (
            <div className="flex justify-between gap-4">
              <dt className="text-stone-600 dark:text-stone-300">First-name similarity (Jaro-Winkler)</dt>
              <dd className="font-mono tabular-nums">{e.checks.first_name.toFixed(2)}</dd>
            </div>
          )}
          {e.checks.dob !== undefined && (
            <div className="flex justify-between gap-4">
              <dt className="text-stone-600 dark:text-stone-300">Dates of birth</dt>
              <dd>{e.checks.dob === 1 ? "same" : "different"}</dd>
            </div>
          )}
        </dl>
      </Panel>
    </div>
  );
}

const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

function SplitEvidenceView({ e }: { e: SplitEvidence }) {
  const rejected = new Set(e.rejected_edges.map(([a, b]) => pairKey(a, b)));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Why they were linked (MATCH edges)">
        <ul className="space-y-2 text-sm">
          {e.edges.map((edge) => {
            const cut = rejected.has(pairKey(edge.left_id, edge.right_id));
            return (
              <li key={`${edge.left_id}-${edge.right_id}`} className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono">
                  {edge.left_id} ↔ {edge.right_id}
                </span>
                <span>{edge.description}</span>
                {cut ? (
                  <span className="rounded bg-rose-100 px-1.5 text-xs font-semibold text-rose-800 dark:bg-rose-950 dark:text-rose-200">
                    cut by the split
                  </span>
                ) : (
                  <span className="rounded bg-stone-100 px-1.5 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                    kept
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </Panel>
      <Panel title="Why they were split">
        <ul className="space-y-1 text-sm">
          {e.conflicts.map(([a, b]) => (
            <li key={`${a}-${b}`}>
              <span className="font-mono">{a}</span> and <span className="font-mono">{b}</span> differ on both
              email and phone
            </li>
          ))}
        </ul>
        <h4 className="mt-4 text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
          Resulting clusters
        </h4>
        <ul className="mt-1 space-y-1 text-sm">
          {e.clusters.map((c) => (
            <li key={c.join()} className="font-mono">
              {c.join(" · ")}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

export function ItemView({ detail }: { detail: ItemDetail }) {
  return (
    <div className="space-y-4">
      <RecordsTable detail={detail} />
      {detail.kind === "pair" ? (
        <PairEvidence e={detail.evidence as Explanation} />
      ) : (
        <SplitEvidenceView e={detail.evidence as SplitEvidence} />
      )}
    </div>
  );
}
