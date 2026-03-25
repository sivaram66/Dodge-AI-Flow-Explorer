import { useState, useCallback, useEffect } from 'react'
import GraphCanvas from './components/GraphCanvas'
import NodeInspector from './components/NodeInspector'
import ChatPanel from './components/ChatPanel'
import { useHighlight } from './hooks/useHighlight'

const BASE = import.meta.env.VITE_API_URL || '/api'

function WakingUp({ elapsed }) {
  const progress = Math.min((elapsed / 60) * 100, 95)
  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6"
         style={{ background: '#f0f2f5' }}>
      <div className="flex flex-col items-center gap-4 max-w-sm text-center">
        <div className="w-12 h-12 rounded-full border-4 border-gray-200 border-t-blue-500 animate-spin" />
        <p className="text-gray-700 font-medium text-base">Waking up the server…</p>
        <p className="text-gray-400 text-sm">This takes about 30 seconds on first load.</p>
        <div className="w-64 h-1.5 rounded-full bg-gray-200 overflow-hidden">
          <div
            className="h-full rounded-full bg-blue-500 transition-all duration-1000"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="text-gray-400 text-xs">{elapsed}s elapsed</p>
      </div>
    </div>
  )
}

export default function App() {
  const { highlightedIds, setHighlights } = useHighlight()
  const [selectedNode, setSelectedNode]   = useState(null)
  const [focusNodeId,  setFocusNodeId]    = useState(null)
  const [serverReady,  setServerReady]    = useState(false)
  const [elapsed,      setElapsed]        = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer = null

    async function ping() {
      try {
        const BACKEND = import.meta.env.VITE_API_URL?.replace('/api', '') || ''
        const res = await fetch(`${BACKEND}/health`)
        if (res.ok) {
          if (!cancelled) setServerReady(true)
          return
        }
      } catch {
        // server still sleepingg
      }
      if (!cancelled) timer = setTimeout(ping, 3000)
    }

    ping()

    const counter = setInterval(() => {
      if (!cancelled) setElapsed(s => s + 1)
    }, 1000)

    return () => {
      cancelled = true
      clearTimeout(timer)
      clearInterval(counter)
    }
  }, [])

  const handleNodeClick = useCallback((node) => {
    setSelectedNode(node)
  }, [])

  const handleConnectedNodeClick = useCallback((neighbor) => {
    setSelectedNode(neighbor)
    setHighlights([neighbor.id])
    setFocusNodeId(neighbor.id)
  }, [setHighlights])

  const handleCloseInspector = useCallback(() => {
    setSelectedNode(null)
  }, [])

  if (!serverReady) return <WakingUp elapsed={elapsed} />

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: '#f0f2f5' }}>

      <div className="relative flex flex-col flex-1 min-w-0 bg-white">

        <header className="flex items-center gap-2 px-6 py-3 border-b border-gray-100 shrink-0 select-none">
          <span className="text-sm text-gray-400 font-medium">Mapping</span>
          <span className="text-gray-300 text-sm">/</span>
          <span className="text-sm text-gray-700 font-semibold">Order to Cash</span>
        </header>

        <div className="relative flex-1 min-h-0">
          <GraphCanvas
            highlightedIds={highlightedIds}
            focusNodeId={focusNodeId}
            onNodeClick={handleNodeClick}
          />

          {selectedNode && (
            <NodeInspector
              node={selectedNode}
              onClose={handleCloseInspector}
              onNodeClick={handleConnectedNodeClick}
            />
          )}
        </div>
      </div>

      <div className="w-[380px] shrink-0 border-l border-gray-200 bg-white flex flex-col">
        <ChatPanel onHighlight={(ids) => { console.log('setHighlights called with:', ids); setHighlights(ids) }} />
      </div>

    </div>
  )
}
