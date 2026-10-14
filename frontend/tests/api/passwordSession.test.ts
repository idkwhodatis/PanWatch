import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI, getToken, isAuthenticated, logout } from '../../packages/api/src/client'

import { setAuthStatus } from '../../packages/api/src/auth-session'

const expiry = '2099-01-01T00:00:00.000Z'
function login(token = 'synthetic-old', expires = expiry) {
  localStorage.setItem('token', token)
  localStorage.setItem('token_expires', expires)
}
function unauthorized() {
  return { status: 401, json: async () => ({ code: 401, error_code: 'invalid_credentials' }) }
}

describe('local password session lifecycle', () => {
  beforeEach(() => {
    localStorage.clear()
    setAuthStatus({ initialized: true, oidc_enabled: false, password_enabled: true, authenticated: false, auth_method: null, csrf_token: null, oidc_login_url: null })
    localStorage.setItem('panwatch-locale', 'en-US')
    vi.stubGlobal('window', { location: { href: '/portfolio' }, setTimeout, clearTimeout })
  })
  afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('keeps a valid local login and allows explicit logout', async () => {
    login()
    setAuthStatus({ initialized: true, oidc_enabled: false, password_enabled: true, authenticated: true, auth_method: 'password', csrf_token: null, oidc_login_url: null })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ code: 0 }) }))
    expect(isAuthenticated()).toBe(true)
    await logout()
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('token_expires')).toBeNull()
    expect(window.location.href).toBe('/login')
  })

  it.each(['2000-01-01T00:00:00Z', 'not-a-date'])('discards expired/malformed expiry %s before requests', async expires => {
    login('synthetic-old', expires)
    const fetch = vi.fn().mockResolvedValue({ status: 200, json: async () => ({ code: 0, data: 'ok' }) })
    vi.stubGlobal('fetch', fetch)
    expect(await fetchAPI('/stocks')).toBe('ok')
    expect(new Headers(fetch.mock.calls[0][1].headers).has('Authorization')).toBe(false)
    expect(isAuthenticated()).toBe(false)
    expect(localStorage.getItem('token_expires')).toBeNull()
  })

  it('treats exact expiry as expired', () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(expiry)); login()
    expect(getToken()).toBeNull()
  })

  it('preserves legacy tokens without an expiry until the server rejects them', async () => {
    localStorage.setItem('token', 'legacy-synthetic')
    expect(getToken()).toBe('legacy-synthetic')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(unauthorized()))
    await expect(fetchAPI('/stocks')).rejects.toThrow('Your session has expired.')
    expect(getToken()).toBeNull()
    expect(window.location.href).toBe('/login')
  })

  it.each(['replacement-token', 'replacement-expiry', 'already-logged-out'])('preserves %s after a delayed 401 without replay', async scenario => {
    login()
    let resolve!: (value: ReturnType<typeof unauthorized>) => void
    const fetch = vi.fn(() => new Promise<ReturnType<typeof unauthorized>>(r => { resolve = r }))
    vi.stubGlobal('fetch', fetch)
    const request = fetchAPI('/stocks', { method: 'POST', body: '{}' })
    if (scenario === 'replacement-token') login('synthetic-new')
    if (scenario === 'replacement-expiry') login('synthetic-old', '2099-01-01T00:00:00.001Z')
    if (scenario === 'already-logged-out') { localStorage.removeItem('token'); localStorage.removeItem('token_expires') }
    const before = [localStorage.getItem('token'), localStorage.getItem('token_expires')]
    resolve(unauthorized())
    await expect(request).rejects.toThrow('Your session changed or could not be verified.')
    expect([localStorage.getItem('token'), localStorage.getItem('token_expires')]).toEqual(before)
    expect(window.location.href).toBe('/portfolio')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(['/auth/login', '/auth/setup', '/auth/status?fresh=1'])('does not clear an existing login for %s errors', async path => {
    login(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(unauthorized()))
    await expect(fetchAPI(path, { method: 'POST' })).rejects.toThrow('The username or password is incorrect.')
    expect(getToken()).toBe('synthetic-old')
    expect(window.location.href).toBe('/portfolio')
  })

  it.each([
    { authorization: 'Bearer synthetic-custom' },
    new Headers({ Authorization: 'Bearer synthetic-custom' }),
    [['Authorization', 'Bearer synthetic-custom']] as [string, string][],
  ])('preserves login when caller supplies a different credential (%s)', async headers => {
    login(); const fetch = vi.fn().mockResolvedValue(unauthorized()); vi.stubGlobal('fetch', fetch)
    await expect(fetchAPI('/stocks', { headers })).rejects.toThrow('The username or password is incorrect.')
    expect(new Headers(fetch.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer synthetic-custom')
    expect(getToken()).toBe('synthetic-old')
  })

  it('does not end the session after an aborted request receives a late 401', async () => {
    login(); const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn(async () => { controller.abort(); return unauthorized() }))
    await expect(fetchAPI('/stocks', { signal: controller.signal })).rejects.toThrow()
    expect(getToken()).toBe('synthetic-old')
    expect(window.location.href).toBe('/portfolio')
  })
})
