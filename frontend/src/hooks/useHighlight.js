import { useState, useCallback } from 'react'

export function useHighlight() {
  const [highlightedIds, setHighlightedIds] = useState(new Set())

  const setHighlights = useCallback((ids) => {
    setHighlightedIds(new Set(ids))
  }, [])

  const clearHighlights = useCallback(() => {
    setHighlightedIds(new Set())
  }, [])

  return { highlightedIds, setHighlights, clearHighlights }
}
