import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '../../packages/api/src/auth'
import { clearAuthSession, getAuthStatus, setAuthStatus, type AuthStatus } from '../../packages/api/src/auth-session'
import { authHeaders, fetchAPI, getToken, isAuthenticated, logout } from '../../packages/api/src/client'
import { readSSE, subscribeSSE } from '../../packages/api/src/sse'
import { tradingAgentsApi } from '../../packages/api/src/tradingagents'

const passwordStatus: AuthStatus = {
  initialized: true, oidc_enabled: false, password_enabled: true, authenticated: false,
  auth_method: null, csrf_token: null, oidc_login_url: null,
}
const oidcStatus: AuthStatus = {
  ...passwordStatus, oidc_enabled: true, password_enabled: false, authenticated: true,
  auth_method: 'oidc', csrf_token: 'csrf-memory-only', oidc_login_url: 'https://panwatch.example/api/auth/oidc/login',
}
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data }), {
  status, headers: { 'Content-Type': 'application/json' },
})

beforeEach(() => {
  localStorage.clear()
  setAuthStatus(passwordStatus)
  window.history.replaceState(null, '', '/login')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  clearAuthSession()
})

describe('verified OIDC status', () => {
  it('does not authenticate with a local marker or an unverified password token', () => {
    localStorage.setItem('oidc_authenticated', 'true')
    localStorage.setItem('token', 'stale-password')
    expect(isAuthenticated()).toBe(false)
  })

  it('deduplicates overlapping status checks and keeps cookie credentials in memory only', async () => {
    let resolve!: (value: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetch)
    const first = authApi.status()
    const second = authApi.status()
    expect(fetch).toHaveBeenCalledTimes(1)
    resolve(response(oidcStatus))
    expect(await first).toEqual(oidcStatus)
    expect(await second).toEqual(oidcStatus)
    expect(isAuthenticated()).toBe(true)
    expect(localStorage.length).toBe(0)
    expect(fetch.mock.calls[0]).toEqual(['/api/auth/status', expect.objectContaining({ credentials: 'same-origin' })])
  })

  it.each([{ initialized: false }, { ...oidcStatus, csrf_token: null }, { ...oidcStatus, oidc_login_url: 'javascript:alert(1)' }])(
    'rejects an incomplete or unsafe status without enabling setup', async value => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(value)))
      await expect(authApi.status()).rejects.toThrow()
      expect(isAuthenticated()).toBe(false)
    },
  )

  it('discards old password tokens under SSO-only policy', async () => {
    localStorage.setItem('token', 'old-token')
    localStorage.setItem('token_expires', '2099-01-01')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null })))
    await authApi.status()
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it('does not restore a cookie session from a status request predating logout', async () => {
    setAuthStatus(oidcStatus)
    let finishStatus!: (value: Response) => void
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finishStatus = resolve }))
      .mockResolvedValueOnce(response({ ok: true }))
    vi.stubGlobal('fetch', fetch)
    const pending = authApi.status()
    await logout()
    finishStatus(response(oidcStatus))
    expect((await pending).authenticated).toBe(false)
    expect(isAuthenticated()).toBe(false)
  })
})

describe('cookie request authentication', () => {
  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('sends CSRF for %s without a stale bearer', async method => {
    setAuthStatus({ ...oidcStatus, password_enabled: true })
    localStorage.setItem('token', 'stale-password')
    const fetch = vi.fn().mockResolvedValue(response({ ok: true }))
    vi.stubGlobal('fetch', fetch)
    await fetchAPI('/settings/example', { method, body: '{}' })
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', headers: { 'X-CSRF-Token': 'csrf-memory-only' } })
    expect(fetch.mock.calls[0][1].headers.Authorization).toBeUndefined()
  })

  it('preserves verified password bearer authentication without CSRF', async () => {
    localStorage.setItem('token', 'password-token')
    setAuthStatus({ ...passwordStatus, authenticated: true, auth_method: 'password' })
    expect(isAuthenticated()).toBe(true)
    expect(authHeaders('POST')).toEqual({ Authorization: 'Bearer password-token' })
  })

  it('omits CSRF for safe cookie reads', () => {
    setAuthStatus(oidcStatus)
    expect(authHeaders('GET')).toEqual({})
  })

  it('expires the cookie session after a protected 401', async () => {
    setAuthStatus(oidcStatus)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(null, 401)))
    await expect(fetchAPI('/settings/test')).rejects.toThrow()
    expect(isAuthenticated()).toBe(false)
    expect(getAuthStatus()?.csrf_token).toBeNull()
  })

  it.each(['/auth/status', '/auth/login', '/auth/setup'])('does not run logout/redirect for a 401 on %s', async path => {
    localStorage.setItem('token', 'old-token')
    const fetch = vi.fn().mockResolvedValue(response(null, 401))
    vi.stubGlobal('fetch', fetch)
    await expect(fetchAPI(path)).rejects.toThrow()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('token')).toBe('old-token')
  })

  it('sends CSRF on POST SSE requests', async () => {
    setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValue(new Response('id: 1\nevent: done\ndata: {}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' },
    }))
    vi.stubGlobal('fetch', fetch)
    await readSSE('/assistant/stream', { method: 'POST', body: {}, onEvent: vi.fn() })
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', headers: { 'X-CSRF-Token': 'csrf-memory-only' } })
  })

  it('does not keep reconnecting an expired SSE session', async () => {
    setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValue(response(null, 401))
    vi.stubGlobal('fetch', fetch)
    const onFailed = vi.fn()
    const onRetry = vi.fn()
    const stop = subscribeSSE('/notifications/stream', { onEvent: vi.fn(), onFailed, onRetry })
    await vi.waitFor(() => expect(onFailed).toHaveBeenCalledTimes(1))
    expect(onRetry).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(1)
    stop()
  })

  it('uses cookie credentials and expires auth for a PDF 401', async () => {
    setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValue(response(null, 401))
    vi.stubGlobal('fetch', fetch)
    await expect(tradingAgentsApi.downloadAnalysisPdf('TEST', '2026-10-11')).rejects.toThrow()
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', headers: {} })
    expect(isAuthenticated()).toBe(false)
  })
})

describe('server-confirmed logout', () => {
  it('deduplicates clicks and retains state until the CSRF-protected POST succeeds', async () => {
    setAuthStatus(oidcStatus)
    let finish!: (value: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(resolve => { finish = resolve }))
    vi.stubGlobal('fetch', fetch)
    const first = logout()
    const second = logout()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(isAuthenticated()).toBe(true)
    expect(fetch.mock.calls[0]).toEqual(['/api/auth/logout', expect.objectContaining({
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': 'csrf-memory-only' },
    })])
    finish(response({ ok: true }))
    await Promise.all([first, second])
    expect(isAuthenticated()).toBe(false)
    expect(getAuthStatus()?.csrf_token).toBeNull()
  })

  it('keeps the session after a failed logout and allows a retry', async () => {
    setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(response({ ok: true }))
    vi.stubGlobal('fetch', fetch)
    await expect(logout()).rejects.toThrow('offline')
    expect(isAuthenticated()).toBe(true)
    expect(getAuthStatus()?.csrf_token).toBe('csrf-memory-only')
    await logout()
    expect(isAuthenticated()).toBe(false)
  })

  it.each([403, 500])('does not discard the session on a %s logout failure', async status => {
    setAuthStatus(oidcStatus)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(null, status)))
    await expect(logout()).rejects.toThrow()
    expect(isAuthenticated()).toBe(true)
  })

  it('accepts a server-confirmed already expired session', async () => {
    setAuthStatus(oidcStatus)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(null, 401)))
    await logout()
    expect(isAuthenticated()).toBe(false)
  })
})
