import { useEffect, useState } from "react";

/**
 * Reveals `text` one character at a time at `speedMs` per character.
 * Returns the progressively-typed substring; settles on the full string.
 */
export function useTypewriter(text: string, speedMs = 95): string {
  const [n, setN] = useState(0);
  // Reset the count when the text changes — the React-blessed "adjust state on a
  // prop change" pattern (set during render, NOT in an effect), so the interval
  // always starts from a clean zero.
  const [prevText, setPrevText] = useState(text);
  if (text !== prevText) {
    setPrevText(text);
    setN(0);
  }

  useEffect(() => {
    if (!text) return;
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setN(i);
      if (i >= text.length) clearInterval(id);
    }, speedMs);
    return () => clearInterval(id);
  }, [text, speedMs]);

  return text.slice(0, n);
}
