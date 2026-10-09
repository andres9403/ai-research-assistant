import { useState } from "react";

const SOURCE_LABELS = { s2: "Semantic Scholar", arxiv: "arXiv", upload: "Upload" };
const MAX_AUTHORS = 6;
const ABSTRACT_PREVIEW = 320;

function formatAuthors(authors) {
  if (!authors?.length) return "Unknown authors";
  if (authors.length <= MAX_AUTHORS) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS).join(", ")}, et al.`;
}

export default function PaperCard({ paper, actions }) {
  const [expanded, setExpanded] = useState(false);
  const abstract = paper.abstract || "";
  const long = abstract.length > ABSTRACT_PREVIEW;

  return (
    <article className="card">
      <div className="card-head">
        <h3 className="card-title">
          {paper.url ? (
            <a href={paper.url} target="_blank" rel="noreferrer">
              {paper.title}
            </a>
          ) : (
            paper.title
          )}
        </h3>
        {actions && <div className="card-actions">{actions}</div>}
      </div>
      <p className="card-meta">
        {formatAuthors(paper.authors)}
        {paper.year && <> · {paper.year}</>}
        <span className="tag">{SOURCE_LABELS[paper.source] || paper.source}</span>
        {paper.pdf_url && (
          <a className="tag tag-link" href={paper.pdf_url} target="_blank" rel="noreferrer">
            PDF
          </a>
        )}
      </p>
      {abstract ? (
        <p className="card-abstract">
          {long && !expanded ? `${abstract.slice(0, ABSTRACT_PREVIEW).trimEnd()}…` : abstract}{" "}
          {long && (
            <button className="link-button" onClick={() => setExpanded(!expanded)}>
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </p>
      ) : (
        <p className="card-abstract muted">No abstract available.</p>
      )}
    </article>
  );
}
