import { useState, useRef, useEffect, useCallback } from 'react'
import ChatMessage from './ChatMessage'
import { streamChat } from '../services/api'

const SUGGESTED_QUERIES = [
  {
    category: 'Required Queries',
    queries: [
      'Which products have the most billing documents?',
      'Trace billing document 91150216',
      'Are there any broken or incomplete O2C flows?',
    ],
  },
  {
    category: 'Customer Analysis',
    queries: [
      'Which customer has the most sales orders?',
      'How many customers are active?',
      'How many customers are blocked?',
      'Tell me products purchased by Nelson, Fitzpatrick and Jordan',
    ],
  },
  {
    category: 'Operations',
    queries: [
      'Which plant handles the most deliveries?',
      'Which deliveries have not been shipped yet?',
    ],
  },
]

export default function ChatPanel({ onHighlight }) {
  const [messages,  setMessages]  = useState([])
  const [input,     setInput]     = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef   = useRef(null)
  const inputRef    = useRef(null)
  const msgCounter  = useRef(0)

  // Always-current snapshot of completed messages for history — avoids
  // stale closure issues inside the send callback without adding messages
  // to its dependency array (which would re-create it on every keystroke).
  const historyRef  = useRef([])
  useEffect(() => {
    historyRef.current = messages
      .filter(m => !m.streaming && m.content)
      .map(({ role, content }) => ({ role, content }))
  }, [messages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Auto-grow textarea: reset to auto each time input changes so shrinking
  // also works, then clamp to scrollHeight (max 120px before scrolling)
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [input])

  const send = useCallback(async (text) => {
    const trimmed = text.trim()
    if (!trimmed || streaming) return

    setInput('')
    onHighlight?.([])   // clear previous highlights before the new query runs

    const userId      = ++msgCounter.current
    const assistantId = ++msgCounter.current

    setMessages(prev => [
      ...prev,
      { id: userId,      role: 'user',      content: trimmed, streaming: false },
      { id: assistantId, role: 'assistant', content: '',      streaming: true  },
    ])
    setStreaming(true)

    await streamChat(trimmed, historyRef.current, {
      onToken(token) {
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, content: m.content + token } : m)
        )
      },
      onHighlight(nodeIds) {
        console.log('onHighlight called with:', nodeIds)
        onHighlight?.(nodeIds)
      },
      onError(err) {
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId
              ? { ...m, content: `Something went wrong: ${err}`, streaming: false }
              : m
          )
        )
      },
      onDone() {
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, streaming: false } : m)
        )
        setStreaming(false)
        inputRef.current?.focus()
      },
    })
  }, [streaming, onHighlight])

  const handleSubmit = useCallback((e) => {
    e.preventDefault()
    send(input)
  }, [input, send])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    }
  }, [input, send])

  return (
    <div className="flex flex-col h-full">

      <div className="px-5 py-4 border-b border-gray-100 shrink-0">
        <h2 className="text-sm font-semibold text-gray-900">AI Analysis</h2>
        <p className="text-xs text-gray-400 mt-0.5">Ask questions about your O2C data</p>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5 min-h-0">
        {messages.length === 0 ? (
          <div className="space-y-4 mt-2">
            {SUGGESTED_QUERIES.map(({ category, queries }) => (
              <div key={category}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">
                  {category}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {queries.map(q => (
                    <button
                      key={q}
                      onClick={() => setInput(q)}
                      className="text-left text-xs text-gray-600 bg-gray-100 hover:bg-gray-200 px-2.5 py-1.5 rounded-lg transition-colors leading-snug"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          messages.map(msg => (
            <ChatMessage
              key={msg.id}
              role={msg.role}
              content={msg.content}
              streaming={msg.streaming}
            />
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div className="px-4 py-4 border-t border-gray-100 shrink-0">
        <form onSubmit={handleSubmit}>
          <div className="flex items-end gap-2 bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 focus-within:border-blue-400 focus-within:bg-white transition-colors">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Analyze anything…"
              disabled={streaming}
              className="flex-1 bg-transparent text-sm text-gray-800 placeholder-gray-400 outline-none resize-none leading-5 disabled:opacity-50"
              style={{ height: '20px', overflowY: 'auto' }}
            />
            <button
              type="submit"
              disabled={streaming || !input.trim()}
              className="shrink-0 w-8 h-8 flex items-center justify-center rounded-xl bg-gray-900 text-white disabled:opacity-30 hover:bg-gray-700 transition-colors"
              aria-label="Send"
            >
              {streaming ? (
                <span className="w-3 h-3 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              ) : (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M7 12V2M7 2L2.5 6.5M7 2L11.5 6.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          </div>
          <p className="text-[10px] text-gray-400 mt-1.5 pl-1">
            Enter to send · Shift+Enter for new line
          </p>
        </form>
      </div>
    </div>
  )
}
