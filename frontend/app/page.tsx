"use client";

import { useState } from "react";
import { ask, type QueryResponse } from "@/lib/api";
import { RetrievalInspector } from "@/components/RetrievalInspector";

const EXAMPLES = [
  "How long does a deployer have to complete a human review?",
  "How long must records of automated decisions be kept?",
  "¿En qué plazo debe completarse la revisión humana?",
  "What is the standard rate of VAT in Germany?",
];

export default function AskPage() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [language, setLanguage] = useState("");
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    try {
      setResult(await ask(question, language || null));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <form className="ask" onSubmit={submit}>
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about an indexed act…"
          required
          minLength={3}
        />
        <select value={language} onChange={(event) => setLanguage(event.target.value)}>
          <option value="">All languages</option>
          <option value="en">English</option>
          <option value="es">Español</option>
        </select>
        <button type="submit" disabled={pending}>
          {pending ? "Retrieving…" : "Ask"}
        </button>
      </form>

      <p className="empty" style={{ marginTop: "0.75rem" }}>
        Try:{" "}
        {EXAMPLES.map((example, index) => (
          <span key={example}>
            {index > 0 && " · "}
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                setQuestion(example);
              }}
            >
              {example.length > 44 ? `${example.slice(0, 44)}…` : example}
            </a>
          </span>
        ))}
      </p>

      {error && (
        <div className="banner" data-kind="error">
          {error}
        </div>
      )}

      {result && !result.answerable && (
        <div className="banner" data-kind="refusal">
          <strong>Not answerable from the indexed corpus.</strong>
          <div>{result.refusal_reason}</div>
        </div>
      )}

      {result?.answerable && (
        <section className="card">
          <h2>Answer</h2>
          <div className="answer">{result.answer}</div>
        </section>
      )}

      {result && result.citations.length > 0 && (
        <section className="card">
          <h2>Citations</h2>
          <ol className="citations">
            {result.citations.map((citation) => (
              <li key={citation.marker}>
                <span className="pin">
                  [{citation.marker}] {citation.citation}
                </span>
                <blockquote>“{citation.supporting_quote}”</blockquote>
              </li>
            ))}
          </ol>
        </section>
      )}

      {result && <RetrievalInspector result={result} />}
    </>
  );
}
