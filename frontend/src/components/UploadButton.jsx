import { useRef } from "react";

// A button that opens a file picker for one PDF and hands the file to onFile.
export default function UploadButton({ onFile, busy, children, className }) {
  const input = useRef(null);

  function pick(event) {
    const file = event.target.files?.[0];
    event.target.value = ""; // so picking the same file again still fires
    if (file) onFile(file);
  }

  return (
    <>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        onChange={pick}
        hidden
      />
      <button className={className} onClick={() => input.current.click()} disabled={busy}>
        {children}
      </button>
    </>
  );
}
