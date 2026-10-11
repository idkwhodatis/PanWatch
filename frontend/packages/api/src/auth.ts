import { fetchAPI } from './client'
import { getAuthRevision, getAuthStatus, setAuthStatus, validateAuthStatus, type AuthStatus } from './auth-session'
export type { AuthStatus } from './auth-session'

let statusRequest: Promise<AuthStatus> | null = null

function loadStatus(): Promise<AuthStatus> {
  if (statusRequest) return statusRequest
  const revision = getAuthRevision()
  statusRequest = fetchAPI<AuthStatus>('/auth/status', { cache: 'no-store' }).then(validateAuthStatus).then(status => {
    // Ignore a response that predates a completed logout or another auth change.
    if (revision !== getAuthRevision()) {
      const current = getAuthStatus()
      if (!current) throw new Error('Authentication changed')
      return current
    }
    setAuthStatus(status)
    return status
  }).finally(() => { statusRequest = null })
  return statusRequest
}

export function rememberPasswordLogin(data: AuthTokenPayload): void {
  localStorage.setItem('token', data.token)
  localStorage.setItem('token_expires', data.expires_at)
  const status = getAuthStatus()
  if (status?.password_enabled) setAuthStatus({ ...status, authenticated: true, auth_method: 'password', csrf_token: null })
}

export interface AuthTokenPayload {
  token: string
  expires_at: string
}

export interface LoginPayload {
  username: string
  password: string
}

export const authApi = {
  status: loadStatus,
  startOidcLogin: (url: string) => window.location.assign(url),
  login: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  setup: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
