import ReactMarkdown, { defaultUrlTransform, type Options } from 'react-markdown'
import { useInRouterContext, useLocation } from 'react-router-dom'

type MarkdownProps = Omit<Options, 'urlTransform'>

function documentUrlTransform(url: string, key: string): string {
  const safeUrl = defaultUrlTransform(url)
  // The runtime <base> serves assets from the deployment root. Bare fragments
  // must instead target the current document, preserving its path and query.
  return key === 'href' && safeUrl.startsWith('#') && typeof window !== 'undefined'
    ? new URL(safeUrl, window.location.href).href
    : safeUrl
}

function RoutedMarkdown(props: MarkdownProps) {
  // Subscribe below memoized content so navigation also refreshes unchanged
  // Markdown. window.location includes the public basename exactly once.
  useLocation()
  return <ReactMarkdown {...props} urlTransform={documentUrlTransform} />
}

/** Shared Markdown renderer; retain native links and the default URL safety filter. */
export default function Markdown(props: MarkdownProps) {
  return useInRouterContext()
    ? <RoutedMarkdown {...props} />
    : <ReactMarkdown {...props} urlTransform={documentUrlTransform} />
}
