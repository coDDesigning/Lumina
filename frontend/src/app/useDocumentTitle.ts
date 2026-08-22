import { useEffect } from 'react';

const SUFFIX = 'Lumina';

/**
 * Sets the tab title per route. The current build never sets one, so every tab
 * reads "Lumina Workspace" regardless of where the user is.
 */
export function useDocumentTitle(title: string | undefined) {
  useEffect(() => {
    if (!title) {
      document.title = SUFFIX;
      return;
    }
    document.title = `${title} · ${SUFFIX}`;
  }, [title]);
}
