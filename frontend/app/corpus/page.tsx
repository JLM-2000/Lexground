import { listDocuments } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function CorpusPage() {
  let documents;
  try {
    documents = await listDocuments();
  } catch (cause) {
    return (
      <div className="banner" data-kind="error">
        Could not reach the API: {cause instanceof Error ? cause.message : String(cause)}
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <p className="empty">
        Nothing indexed yet. Run <code>make seed</code> to ingest the fixture corpus.
      </p>
    );
  }

  return (
    <section className="card">
      <h2>Indexed acts</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Short title</th>
              <th>Title</th>
              <th>Lang</th>
              <th>Version</th>
              <th className="num">Chunks</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((document) => (
              <tr key={`${document.source}-${document.source_id}-${document.language}`}>
                <td className="tag">{document.source}</td>
                <td className="pin">{document.short_title}</td>
                <td>
                  <a href={document.source_url} target="_blank" rel="noreferrer">
                    {document.title}
                  </a>
                </td>
                <td>{document.language}</td>
                <td>{document.version}</td>
                <td className="num">{document.chunk_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
