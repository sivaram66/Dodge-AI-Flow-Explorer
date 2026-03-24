const BASE = import.meta.env.VITE_API_URL || '/api'

export async function fetchGraphSummary() {
  const res = await fetch(`${BASE}/graph/summary`)
  if (!res.ok) throw new Error(`Graph summary failed: ${res.status}`)
  return res.json()
}

export async function fetchGraphFull() {
  const res = await fetch(`${BASE}/graph/full`)
  if (!res.ok) throw new Error(`Graph full failed: ${res.status}`)
  return res.json()
}

export async function fetchNode(nodeId) {
  const res = await fetch(`${BASE}/graph/node/${encodeURIComponent(nodeId)}`)
  if (!res.ok) throw new Error(`Node fetch failed: ${res.status}`)
  return res.json()
}

export async function expandType(entityType) {
  const res = await fetch(`${BASE}/graph/expand/${entityType}`)
  if (!res.ok) throw new Error(`Expand type failed: ${res.status}`)
  return res.json()
}

// POST /api/chat — two possible responses:
//   • Out-of-scope: plain JSON  { answer: string, in_scope: false }
//   • In-scope:     SSE stream  event: token | highlight | error
//
// callbacks:
//   onToken(text)        — called for each streamed text chunk
//   onHighlight(ids[])   — called once with node IDs to light up
//   onError(message)     — called on error event or fetch failure
//   onDone()             — called when stream is finished
export async function streamChat(message, history = [], { onToken, onHighlight, onError, onDone } = {}) {
  let res
  try {
    res = await fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history }),
    })
  } catch (err) {
    onError?.(`Network error: ${err.message}`)
    onDone?.()
    return
  }

  if (!res.ok) {
    onError?.(`Request failed: ${res.status}`)
    onDone?.()
    return
  }

  const contentType = res.headers.get('content-type') ?? ''

  if (contentType.includes('application/json')) {
    const data = await res.json()
    onToken?.(data.answer)
    onDone?.()
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

      const parts = buffer.split('\n\n')
      buffer = parts.pop() // keep the incomplete trailing fragment

      for (const part of parts) {
        if (!part.trim()) continue

        let eventType = 'message'
        let data = ''

        for (const line of part.split('\n')) {
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            // Do NOT use .trim(): it eats meaningful whitespace in token text
            // (e.g. " world" becomes "world", merging words across chunks).
            const raw = line.slice(5)
            data = raw.startsWith(' ') ? raw.slice(1) : raw
          }
        }

        console.log('SSE event received:', eventType, data)

        if (eventType === 'token') {
          onToken?.(data)
        } else if (eventType === 'highlight') {
          try {
            const parsed = JSON.parse(data)
            onHighlight?.(parsed.node_ids ?? [])
          } catch {
            // ignore malformed highlight payload
          }
        } else if (eventType === 'error') {
          onError?.(data)
        }
      }
    }
  } finally {
    reader.releaseLock()
    onDone?.()
  }
}
