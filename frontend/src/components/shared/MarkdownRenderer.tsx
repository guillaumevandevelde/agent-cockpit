import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'

interface MarkdownRendererProps {
  content: string
  className?: string
}

// Same-origin paths (e.g. `/kanban?card=<id>`) resolve to this app's own
// origin and should be routed by react-router; everything else (http(s)://,
// mailto:, an unparseable href) is external and gets the default anchor
// behavior (full navigation, no SPA routing).
function isInternalHref(href: string): boolean {
  try {
    return new URL(href, window.location.origin).origin === window.location.origin
  } catch {
    return false
  }
}

export function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  const navigate = useNavigate()
  return (
    <div className={`prose prose-sm max-w-none dark:prose-invert ${className}`}>
      <ReactMarkdown
        components={{
          a({ href, children, ...props }) {
            if (!href) {
              return <a {...props}>{children}</a>
            }
            if (isInternalHref(href)) {
              return (
                <a
                  {...props}
                  href={href}
                  onClick={(e) => {
                    e.preventDefault()
                    const url = new URL(href, window.location.origin)
                    navigate(`${url.pathname}${url.search}${url.hash}`)
                  }}
                >
                  {children}
                </a>
              )
            }
            return (
              <a {...props} href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            )
          },
          code({ className: codeClassName, children, ...props }) {
            const match = /language-(\w+)/.exec(codeClassName || '')
            const isInline = !match
            return !isInline && match ? (
              <SyntaxHighlighter
                style={oneDark as { [key: string]: CSSProperties }}
                language={match[1]}
                PreTag="div"
              >
                {String(children).replace(/\n$/, '')}
              </SyntaxHighlighter>
            ) : (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
