const LABELS = {
  ready: "Full text ✓",
  downloading: "Downloading PDF…",
  processing: "Processing PDF…",
  no_pdf: "No PDF",
  failed: "PDF unreadable",
};

export default function PdfStatus({ paper }) {
  if (!paper.status) return null;
  return (
    <span className={`tag status-${paper.status}`} title={paper.status_detail || undefined}>
      {LABELS[paper.status] || paper.status}
    </span>
  );
}
