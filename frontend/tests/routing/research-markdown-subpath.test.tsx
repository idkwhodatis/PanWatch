import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import type { DeepAnalysisResult } from '@panwatch/api'
import { AnalysisReader } from '@panwatch/biz-ui/components/analysis-reader'
import { ResearchReport } from '@/components/ResearchReport'
import { ResearchEvidenceBoard } from '@/components/ResearchEvidenceBoard'
import { ResearchProcessGraph } from '@/components/ResearchProcessGraph'

const content = '[Details](#details)\n\n[Unsafe](javascript:alert%281%29)'
let base: HTMLBaseElement
beforeEach(async () => {
  await i18n.changeLanguage('en-US')
  base = document.createElement('base')
  document.head.append(base)
})
afterEach(() => {
  cleanup()
  base.remove()
  window.history.replaceState(null, '', '/')
})

function expectCurrentDocument(root: HTMLElement = document.body) {
  const current = `${window.location.origin}${window.location.pathname}${window.location.search}`
  expect(within(root).getByRole('link', { name: 'Details' }).getAttribute('href')).toBe(`${current}#details`)
  expect(within(root).getByText('Unsafe', { selector: 'a' }).getAttribute('href')).toBe('')
}

describe.each(['', '/panwatch', '/apps/panwatch'])('research Markdown at "%s"', prefix => {
  beforeEach(() => {
    base.setAttribute('href', `${prefix}/`)
    window.history.replaceState(null, '', `${prefix}/history?run=one#previous`)
  })
  it('keeps legacy report fragments on the current document', () => {
    render(<ResearchReport content={content} />)
    expectCurrentDocument()
  })
  it('keeps evidence summaries and expanded content on the current document', () => {
    render(<ResearchEvidenceBoard items={[{ title: 'Evidence', summary: content, content }]} />)
    expectCurrentDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand all details' }))
    expectCurrentDocument()
  })
  it('keeps unstructured process entries and role dialogs on the current document', () => {
    const { rerender } = render(<ResearchProcessGraph items={[{ title: 'Legacy process', content }]} />)
    expectCurrentDocument()
    rerender(<ResearchProcessGraph items={[{ title: 'Technical process', role: 'technical', content }]} />)
    fireEvent.click(within(screen.getByTestId('research-graph')).getByRole('button'))
    expectCurrentDocument(screen.getByRole('dialog'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('keeps analysis reader dialog GFM links and footnotes scoped', () => {
    const result = { content: '', raw_data: { analyst_reports: { news: `${content}\n\nClaim.[^source]\n\n[^source]: Evidence.` } } } as DeepAnalysisResult
    render(<AnalysisReader result={result} documentView={() => null} />)
    // The graph is the second tab; its translated label is owned by upstream.
    fireEvent.mouseDown(screen.getAllByRole('tab')[1], { button: 0, ctrlKey: false })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('analysisReader.news', { ns: 'bizUi' }) }))
    const dialog = screen.getByRole('dialog')
    expectCurrentDocument(dialog)
    const ref = dialog.querySelector<HTMLAnchorElement>('a[data-footnote-ref]')!
    expect(ref).toBeTruthy()
    expect(ref.href).toContain(`${window.location.pathname}${window.location.search}#user-content-`)
    expect(document.getElementById(decodeURIComponent(new URL(ref.href).hash.slice(1)))).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

})
