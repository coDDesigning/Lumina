import { useEffect } from 'react';

const SUFFIX = 'Lumina';

export function useDocumentTitle(title: string | undefined) {
  useEffect(() => {
    if (!title) {
      document.title = SUFFIX;
      return;
    }
    document.title = `${title} · ${SUFFIX}`;
  }, [title]);
}
