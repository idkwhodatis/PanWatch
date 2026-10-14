import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MarketExtensionsSection from '../../src/components/MarketExtensionsSection'

const fetchAPI = vi.hoisted(() => vi.fn())
vi.mock('@panwatch/api', () => ({ fetchAPI }))
const status = { id: 'canada', enabled: false, available: false, exchanges: ['TSX', 'TSXV'], capabilities: { quotes: false, analysis: false } }
beforeEach(() => fetchAPI.mockReset())
afterEach(cleanup)

describe('optional market configuration', () => {
  it('shows default off and unavailable capabilities; toggles only after saved response', async () => {
    fetchAPI.mockResolvedValueOnce([status])
    render(<MarketExtensionsSection />)
    const toggle = await screen.findByRole('switch', { name: '加拿大' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(screen.getByText(/尚未安装/)).toBeTruthy()
    let save!: (value: unknown) => void
    fetchAPI.mockImplementationOnce(() => new Promise(resolve => { save = resolve }))
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(toggle.hasAttribute('disabled')).toBe(true)
    await act(async () => save({ ...status, enabled: true }))
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    expect(fetchAPI).toHaveBeenLastCalledWith('/market-extensions/canada', { method: 'PATCH', body: '{"enabled":true}' })
  })

  it('keeps prior state after ambiguous save failure and provides verification refresh', async () => {
    fetchAPI.mockResolvedValueOnce([status]).mockRejectedValueOnce(new Error('timeout'))
    render(<MarketExtensionsSection />)
    const toggle = await screen.findByRole('switch', { name: '加拿大' })
    fireEvent.click(toggle)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    fetchAPI.mockResolvedValueOnce([{ ...status, enabled: true }])
    fireEvent.click(screen.getByRole('button', { name: '刷新设置' }))
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
  })

  it('does not update a remounted component from an old request', async () => {
    let finish!: (value: unknown) => void
    fetchAPI.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const first = render(<MarketExtensionsSection />)
    first.unmount()
    fetchAPI.mockResolvedValueOnce([status])
    render(<MarketExtensionsSection />)
    const toggle = await screen.findByRole('switch', { name: '加拿大' })
    await act(async () => finish([{ ...status, enabled: true }]))
    expect(toggle.getAttribute('aria-checked')).toBe('false')
  })
})
