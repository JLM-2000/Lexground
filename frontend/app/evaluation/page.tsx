import { listEvalRuns } from "@/lib/api";

export const dynamic = "force-dynamic";

const METRIC_ORDER = [
  "recall_at_5",
  "ndcg_at_10",
  "mrr",
  "citation_precision",
  "quote_fidelity",
  "refusal_accuracy",
  "groundedness",
  "latency_p50_ms",
  "latency_p95_ms",
];

function formatMetric(name: string, value: number): string {
  return name.endsWith("_ms") ? `${value.toFixed(0)} ms` : value.toFixed(4);
}

export default async function EvaluationPage() {
  let runs;
  try {
    runs = await listEvalRuns();
  } catch (cause) {
    return (
      <div className="banner" data-kind="error">
        Could not reach the API: {cause instanceof Error ? cause.message : String(cause)}
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <p className="empty">
        No evaluation runs recorded. Run <code>make eval</code> to score the golden set.
      </p>
    );
  }

  const metrics = METRIC_ORDER.filter((name) => runs.some((run) => name in run.metrics));

  return (
    <section className="card">
      <h2>Evaluation history</h2>
      <p className="empty">
        Each row is one pass over the golden set. Results are only comparable within an
        index version — the corpus and embedding model are hashed into it.
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Gate</th>
              <th>Index</th>
              <th className="num">Cases</th>
              {metrics.map((name) => (
                <th key={name} className="num">
                  {name.replace(/_/g, " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{new Date(run.created_at).toLocaleString()}</td>
                <td>
                  <span className="tag" data-pass={run.passed}>
                    {run.passed ? "pass" : "fail"}
                  </span>
                </td>
                <td className="pin">{run.index_version.slice(0, 8)}</td>
                <td className="num">{run.case_count}</td>
                {metrics.map((name) => (
                  <td key={name} className="num">
                    {name in run.metrics ? formatMetric(name, run.metrics[name]) : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
