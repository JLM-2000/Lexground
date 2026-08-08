import type { QueryResponse } from "@/lib/api";

export function RetrievalInspector({ result }: { result: QueryResponse }) {
  const cited = new Set(result.citations.map((citation) => citation.citation));

  return (
    <section className="card">
      <h2>Retrieval</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Provision</th>
              <th className="num">Lexical</th>
              <th className="num">Dense</th>
              <th className="num">Fused</th>
              <th>Used</th>
            </tr>
          </thead>
          <tbody>
            {result.retrieved.map((chunk) => (
              <tr key={chunk.chunk_id}>
                <td>
                  <details className="chunk">
                    <summary>{chunk.citation}</summary>
                    <p>{chunk.text}</p>
                  </details>
                </td>
                <td className="num">{chunk.lexical_rank ?? "—"}</td>
                <td className="num">{chunk.dense_rank ?? "—"}</td>
                <td className="num">{chunk.fused_score.toFixed(5)}</td>
                <td>
                  {cited.has(chunk.citation) ? <span className="tag" data-pass="true">cited</span> : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="meta">
        <span>{result.latency_ms} ms end to end</span>
        <span>{result.retrieved.length} chunks in context</span>
        <span>${result.cost_usd.toFixed(6)}</span>
        <span>{result.model}</span>
        <span className="pin">trace {result.trace_id.slice(0, 8)}</span>
      </div>
    </section>
  );
}
