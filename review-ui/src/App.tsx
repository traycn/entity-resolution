import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Decision, type ItemDetail, type Queue, type QueueItem, getItem, getQueue, postDecision } from "./api";
import { ItemView } from "./ItemView";

// What Approve / Reject mean depends on the item: a pair proposes "same person", a
// split proposes "these records are not one entity".
const ACTIONS = {
  pair: { approve: "Same person", reject: "Different people" },
  split: { approve: "Split is correct", reject: "Should be one entity" },
} as const;

const SHORTCUTS: [string[], string][] = [
  [["A"], "Approve"],
  [["R"], "Reject"],
  [["N", "J"], "Next item"],
  [["P", "K"], "Previous item"],
  [["U"], "Next undecided item"],
  [["?"], "Show or hide shortcuts"],
];

function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

function DecisionMark({ decision }: { decision: Decision | null }) {
  if (!decision) return <span className="w-4" aria-hidden="true" />;
  return decision === "approve" ? (
    <span className="w-4 font-bold text-emerald-700 dark:text-emerald-400" title="Approved">
      <span aria-hidden="true">✓</span>
      <span className="sr-only">approved</span>
    </span>
  ) : (
    <span className="w-4 font-bold text-rose-700 dark:text-rose-400" title="Rejected">
      <span aria-hidden="true">✕</span>
      <span className="sr-only">rejected</span>
    </span>
  );
}

export function App() {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const currentRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    getQueue()
      .then((q) => {
        setQueue(q);
        const first = q.groups[0]?.group ?? null;
        setGroup(first);
        setCurrentId(q.items.find((i) => i.group === first && !i.decision)?.id ?? q.items[0]?.id ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const items = useMemo(() => (queue && group ? queue.items.filter((i) => i.group === group) : []), [queue, group]);
  const index = items.findIndex((i) => i.id === currentId);
  const current: QueueItem | undefined = items[index];

  useEffect(() => {
    if (!currentId) return;
    let cancelled = false;
    setDetail(null);
    getItem(currentId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e: Error) => !cancelled && setError(e.message));
    currentRef.current?.scrollIntoView({ block: "nearest" });
    return () => {
      cancelled = true;
    };
  }, [currentId]);

  const goTo = useCallback(
    (i: number) => {
      const item = items[i];
      if (!item) return;
      setCurrentId(item.id);
      setStatus(`Item ${i + 1} of ${items.length}: ${item.title}`);
    },
    [items],
  );

  const nextUndecided = useCallback(
    (from: number, list: QueueItem[] = items) => {
      for (let k = 1; k <= list.length; k++) {
        const i = (from + k) % list.length;
        if (!list[i].decision) return i;
      }
      return -1;
    },
    [items],
  );

  const decide = useCallback(
    async (decision: Decision) => {
      if (!current || busy) return;
      setBusy(true);
      try {
        await postDecision(current.id, decision);
        const updated = items.map((i) => (i.id === current.id ? { ...i, decision } : i));
        setQueue((q) =>
          q && {
            groups: q.groups.map((g) =>
              g.group === current.group && !current.decision ? { ...g, decided: g.decided + 1 } : g,
            ),
            items: q.items.map((i) => (i.id === current.id ? { ...i, decision } : i)),
          },
        );
        const label = ACTIONS[current.kind][decision];
        const next = nextUndecided(index, updated);
        if (next >= 0 && next !== index) {
          setCurrentId(updated[next].id);
          setStatus(`Saved: ${label}. Next: ${updated[next].title}`);
        } else {
          setStatus(`Saved: ${label}. Every item in this group is decided.`);
        }
      } catch (e) {
        setStatus(`Not saved: ${(e as Error).message}`);
      } finally {
        setBusy(false);
      }
    },
    [busy, current, index, items, nextUndecided],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      const key = e.key.toLowerCase();
      const actions: Record<string, () => void> = {
        a: () => void decide("approve"),
        r: () => void decide("reject"),
        n: () => goTo(Math.min(index + 1, items.length - 1)),
        j: () => goTo(Math.min(index + 1, items.length - 1)),
        p: () => goTo(Math.max(index - 1, 0)),
        k: () => goTo(Math.max(index - 1, 0)),
        u: () => {
          const i = nextUndecided(index);
          if (i >= 0) goTo(i);
          else setStatus("Every item in this group is decided.");
        },
        "?": () => setShowHelp((s) => !s),
        escape: () => setShowHelp(false),
      };
      const action = actions[key];
      if (action) {
        e.preventDefault();
        action();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, goTo, index, items.length, nextUndecided]);

  const selectGroup = (g: string) => {
    if (!queue) return;
    setGroup(g);
    const list = queue.items.filter((i) => i.group === g);
    const target = list.find((i) => !i.decision) ?? list[0];
    setCurrentId(target?.id ?? null);
    const title = queue.groups.find((x) => x.group === g)?.title ?? g;
    setStatus(`${title}: ${list.length} items`);
  };

  const totals = queue
    ? queue.groups.reduce((acc, g) => ({ total: acc.total + g.total, decided: acc.decided + g.decided }), {
        total: 0,
        decided: 0,
      })
    : null;

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#item"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:shadow dark:focus:bg-stone-900"
      >
        Skip to the current item
      </a>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-200 bg-white px-4 py-3 dark:border-stone-800 dark:bg-stone-900 sm:px-6">
        <div>
          <h1 className="text-lg font-semibold">Review queue</h1>
          {totals && (
            <p className="text-sm text-stone-600 dark:text-stone-400">
              {totals.decided.toLocaleString()} of {totals.total.toLocaleString()} decided
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => setShowHelp((s) => !s)}
          aria-expanded={showHelp}
          aria-controls="shortcuts"
          className="rounded-md border border-stone-300 px-3 py-1.5 text-sm hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          Keyboard shortcuts <kbd>?</kbd>
        </button>
      </header>

      {showHelp && (
        <section
          id="shortcuts"
          aria-label="Keyboard shortcuts"
          className="border-b border-stone-200 bg-stone-100 px-4 py-3 dark:border-stone-800 dark:bg-stone-900 sm:px-6"
        >
          <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {SHORTCUTS.map(([keys, label]) => (
              <li key={label} className="flex items-center gap-1.5">
                {keys.map((k) => (
                  <kbd key={k}>{k}</kbd>
                ))}
                <span>{label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>

      {error && (
        <div role="alert" className="m-4 rounded-md border border-rose-300 bg-rose-50 p-4 text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-100">
          {error}
        </div>
      )}

      {queue && (
        <div className="grid flex-1 grid-cols-1 lg:grid-cols-[22rem_1fr]">
          <nav
            aria-label="Review groups"
            className="border-b border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900 lg:h-[calc(100vh-4.25rem)] lg:overflow-y-auto lg:border-b-0 lg:border-r"
          >
            <ul className="space-y-1 p-3">
              {queue.groups.map((g) => (
                <li key={g.group}>
                  <button
                    type="button"
                    onClick={() => selectGroup(g.group)}
                    aria-current={g.group === group ? "true" : undefined}
                    className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                      g.group === group
                        ? "bg-blue-50 font-medium text-blue-900 ring-1 ring-blue-200 dark:bg-blue-950 dark:text-blue-100 dark:ring-blue-800"
                        : "hover:bg-stone-100 dark:hover:bg-stone-800"
                    }`}
                  >
                    <span className="block">{g.title}</span>
                    <span className="mt-0.5 block text-xs text-stone-500 dark:text-stone-400">
                      <span className="font-mono">{g.group}</span> · {g.decided}/{g.total} decided
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <h2 className="border-t border-stone-200 px-4 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-stone-500 dark:border-stone-800 dark:text-stone-400">
              Items in this group
            </h2>
            <ol aria-label="Items in this group" className="max-h-80 space-y-px overflow-y-auto px-3 pb-3 lg:max-h-none">
              {items.map((item, i) => (
                <li key={item.id}>
                  <button
                    type="button"
                    ref={item.id === currentId ? currentRef : undefined}
                    onClick={() => goTo(i)}
                    aria-current={item.id === currentId ? "true" : undefined}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs ${
                      item.id === currentId
                        ? "bg-stone-200 font-medium dark:bg-stone-700"
                        : "hover:bg-stone-100 dark:hover:bg-stone-800"
                    }`}
                  >
                    <DecisionMark decision={item.decision} />
                    <span className="font-mono">{item.record_ids.join(" · ")}</span>
                    {item.score !== null && (
                      <span className="ml-auto font-mono tabular-nums text-stone-500 dark:text-stone-400">
                        {item.score.toFixed(2)}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ol>
          </nav>

          <main id="item" tabIndex={-1} className="min-w-0 px-4 py-5 sm:px-6">
            {current ? (
              <>
                <div className="mb-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-stone-400">
                    {current.kind === "split" ? "Conflict split" : "Pair"} · item {index + 1} of {items.length}
                  </p>
                  <h2 className="mt-1 text-xl font-semibold leading-snug">{current.title}</h2>
                  {current.decision && (
                    <p className="mt-1 text-sm text-stone-600 dark:text-stone-300">
                      Decided: <strong>{ACTIONS[current.kind][current.decision]}</strong> ({current.decision}).
                      Choosing again replaces it.
                    </p>
                  )}
                </div>

                <div
                  role="group"
                  aria-label="Decision"
                  className="sticky top-0 z-10 -mx-4 mb-4 flex flex-wrap items-center gap-2 border-b border-stone-200 bg-stone-50/95 px-4 py-3 backdrop-blur dark:border-stone-800 dark:bg-stone-950/95 sm:-mx-6 sm:px-6"
                >
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void decide("approve")}
                    aria-keyshortcuts="A"
                    className={`inline-flex items-center gap-2 rounded-md bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-60 ${current.decision === "approve" ? "ring-2 ring-emerald-700 ring-offset-2 ring-offset-stone-50 dark:ring-offset-stone-950" : ""}`}
                    aria-pressed={current.decision === "approve"}
                  >
                    Approve: {ACTIONS[current.kind].approve} <kbd className="!border-emerald-500 !bg-emerald-800 !text-white">A</kbd>
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void decide("reject")}
                    aria-keyshortcuts="R"
                    className={`inline-flex items-center gap-2 rounded-md bg-rose-700 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-800 disabled:opacity-60 ${current.decision === "reject" ? "ring-2 ring-rose-700 ring-offset-2 ring-offset-stone-50 dark:ring-offset-stone-950" : ""}`}
                    aria-pressed={current.decision === "reject"}
                  >
                    Reject: {ACTIONS[current.kind].reject} <kbd className="!border-rose-500 !bg-rose-800 !text-white">R</kbd>
                  </button>
                  <span className="mx-1 hidden h-6 w-px bg-stone-300 dark:bg-stone-700 sm:block" aria-hidden="true" />
                  <button
                    type="button"
                    onClick={() => goTo(Math.max(index - 1, 0))}
                    disabled={index <= 0}
                    aria-keyshortcuts="P K"
                    className="rounded-md border border-stone-300 px-3 py-2 text-sm hover:bg-stone-100 disabled:opacity-50 dark:border-stone-700 dark:hover:bg-stone-800"
                  >
                    Previous <kbd>P</kbd>
                  </button>
                  <button
                    type="button"
                    onClick={() => goTo(Math.min(index + 1, items.length - 1))}
                    disabled={index >= items.length - 1}
                    aria-keyshortcuts="N J"
                    className="rounded-md border border-stone-300 px-3 py-2 text-sm hover:bg-stone-100 disabled:opacity-50 dark:border-stone-700 dark:hover:bg-stone-800"
                  >
                    Next <kbd>N</kbd>
                  </button>
                </div>

                {detail && detail.id === current.id ? (
                  <ItemView detail={detail} />
                ) : (
                  <p className="text-sm text-stone-500" aria-busy="true">
                    Loading…
                  </p>
                )}
              </>
            ) : (
              <p className="text-stone-600 dark:text-stone-300">The review queue is empty.</p>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
