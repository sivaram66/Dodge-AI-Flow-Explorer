import ReactMarkdown from 'react-markdown'

// Strip any leaked Llama-style function-call tags before rendering.
function stripFunctionTags(text) {
  return text
    .replace(/<function=[^>]*>[\s\S]*?<\/function>/g, '')
    .replace(/<function=[^>]*>[\s\S]*/g, '')
    .trim()
}

// Normalise inline content that the LLM emits without newlines.
// Handles numbered list items run together and transition phrases mid-paragraph.
function preprocessMarkdown(text) {
  return text
    // Add newline before numbered list items (1–20 only).
    // Require a letter or closing paren immediately before the number so we
    // don't split inside long IDs like "740512" followed by list item "2.".
    // Using explicit 1-20 range instead of \d+ prevents "7405122." from being
    // misread as list item "7" when a document ID abuts the next item number.
    .replace(/([a-zA-Z\)])(1[0-9]|20|[1-9])\.\s+(\*\*|[A-Z])/g, '$1\n\n$2. $3')
    .replace(/([a-z])(\s*)(Here is|The following|The results|Below)/g, '$1\n\n$3')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

const MD = {
  p: ({ children }) => (
    <p className="text-sm text-gray-800 leading-relaxed mb-2 last:mb-0">
      {children}
    </p>
  ),
  ol: ({ children }) => (
    <ol className="my-2 pl-5 space-y-1 list-decimal marker:text-gray-400 text-sm text-gray-800">
      {children}
    </ol>
  ),
  ul: ({ children }) => (
    <ul className="my-2 pl-5 space-y-1 list-disc marker:text-blue-400 text-sm text-gray-800">
      {children}
    </ul>
  ),
  li: ({ children }) => (
    <li className="leading-relaxed">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-gray-900">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="italic text-gray-600">{children}</em>
  ),
  h1: ({ children }) => (
    <h1 className="text-base font-semibold text-gray-900 mt-3 mb-1">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-sm font-semibold text-gray-700 mt-3 mb-1">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mt-2 mb-0.5">
      {children}
    </h3>
  ),
  code: ({ children }) => (
    <code className="text-xs bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded font-mono">
      {children}
    </code>
  ),
  hr: () => <hr className="my-3 border-gray-100" />,
}

export default function ChatMessage({ role, content, streaming = false }) {
  const isUser = role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-sm bg-gray-900 text-white text-sm leading-relaxed whitespace-pre-wrap break-words">
          {content}
        </div>
      </div>
    )
  }

  const cleaned   = content ? stripFunctionTags(content) : ''
  const processed = cleaned  ? preprocessMarkdown(cleaned)  : ''

  return (
    <div className="flex justify-start mb-5">
      <div className="max-w-[92%]">

        {processed && (
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown components={MD}>
              {processed}
            </ReactMarkdown>
          </div>
        )}

        {streaming && (
          <span
            className="inline-block w-[2px] h-[14px] bg-gray-400 ml-0.5 align-middle rounded-sm"
            style={{ animation: 'pulse 1s cubic-bezier(0.4, 0, 0.6, 1) infinite' }}
          />
        )}

        {!processed && !streaming && (
          <span className="text-xs text-gray-400 italic">No response</span>
        )}
      </div>
    </div>
  )
}
