import { useState, useCallback } from 'react'
import GraphCanvas from './components/GraphCanvas'
import NodeInspector from './components/NodeInspector'
import ChatPanel from './components/ChatPanel'
import { useHighlight } from './hooks/useHighlight'

export default function App() {
  const { highlightedIds, setHighlights } = useHighlight()
  const [selectedNode, setSelectedNode]   = useState(null)
  const [focusNodeId,  setFocusNodeId]    = useState(null)

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
