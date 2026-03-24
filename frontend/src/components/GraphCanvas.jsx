import { useEffect, useRef, useState, useCallback } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { fetchGraphFull } from '../services/api'

export default function GraphCanvas({ highlightedIds, focusNodeId, onNodeClick }) {
  console.log('GraphCanvas highlightedIds:', highlightedIds?.size)
  const containerRef = useRef(null)
  const graphRef     = useRef(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const [graphData,  setGraphData]  = useState({ nodes: [], links: [] })
  const [error,      setError]      = useState(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const measure = () =>
      setDimensions({ width: el.offsetWidth, height: el.offsetHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    fetchGraphFull()
      .then(({ nodes, edges }) => setGraphData({ nodes, links: edges }))
      .catch(err => setError(err.message))
  }, [])

  // ForceGraph2D mutates node objects in place with x/y positions, so
  // graphData.nodes contains current positions even after the simulation settles.
  useEffect(() => {
    if (!focusNodeId || !graphRef.current) return
    const node = graphData.nodes.find(n => n.id === focusNodeId)
    if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return
    graphRef.current.centerAt(node.x, node.y, 600)
    graphRef.current.zoom(4, 600)
  }, [focusNodeId, graphData.nodes])

  const nodeCanvasObject = useCallback(
    (node, ctx) => {
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return

      const isHighlighted = highlightedIds?.has(node.id)
      const radius = isHighlighted ? 8 : 4

      if (isHighlighted) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, radius + 4, 0, 2 * Math.PI)
        ctx.fillStyle = 'rgba(245, 166, 35, 0.2)'
        ctx.fill()
      }

      ctx.beginPath()
      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
      ctx.fillStyle = isHighlighted
        ? '#F5A623'
        : node.type === 'summary'
          ? '#4A90D9'
          : node.type === 'plant'
            ? '#4CAF50'
            : '#E8534A'
      ctx.fill()
    },
    [highlightedIds]
  )

  return (
    <div ref={containerRef} className="absolute inset-0">
      {error && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <p className="text-red-500 text-sm bg-white px-4 py-2 rounded shadow border border-red-100">
            {error}
          </p>
        </div>
      )}

      {dimensions.width > 0 && (
        <ForceGraph2D
          ref={graphRef}
          width={dimensions.width}
          height={dimensions.height}
          graphData={graphData}
          backgroundColor="#f8f8f8"
          nodeCanvasObject={nodeCanvasObject}
          nodeCanvasObjectMode={() => 'replace'}
          nodeLabel={node => node.label}
          linkColor={() => 'rgba(74, 144, 217, 0.3)'}
          linkWidth={1}
          linkDirectionalArrowLength={3}
          linkDirectionalArrowRelPos={1}
          onNodeClick={onNodeClick}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          enableNodeDrag
        />
      )}
    </div>
  )
}
