# Authentication delivery boundaries

This plan narrows the optional single-owner SSO proposal in [upstream PR #197](https://github.com/TNT-Likely/PanWatch/pull/197#issuecomment-6143226177). It does not enable SSO or change existing server authentication.

## Independent prerequisite: local password session lifecycle

The first slice is useful with today's password authentication: discard expired local credentials before sending requests, and prevent a delayed unauthorized response from deleting a newer login in another tab. Login/setup failures keep their own error messages and do not clear an existing session. Explicit logout still clears the local token; server-side JWT revocation is not introduced.

A request snapshots both token and expiry. Only a protected request sent with that exact stored bearer credential may clear that credential after a 401. Changed credentials, caller-supplied authorization, authentication-endpoint failures and aborted requests do not end the current session. Rejected operations are never replayed. Tokens without expiry retain legacy compatibility and are checked by the server; malformed nonempty expiry values are discarded.

This slice has no dependency on subpath, public-URL, static-file containment or OIDC changes. It changes no backend route, database schema, cookie policy or identity-provider configuration. Its reproducible Vitest regressions use synthetic credentials and mocked responses, not real browser/IdP acceptance. No UI layout changes are involved.

## Next complete slice: one optional OIDC owner-login path

The next feature slice must work end to end on its own once any independently accepted prerequisites are applied:

1. Explicitly opt in to a single configured OIDC issuer and client. Preserve local-password behavior when disabled. Password fallback, if offered, must be separately explicit.
2. Provide authorization-code login using PKCE, state and nonce; verify signed ID tokens, issuer, audience, expiry and the configured exact subject allowlist before opening a local session.
3. Display and document that **every allowed identity receives the same owner permissions and sees and changes the same shared assets**. This is not a multi-user tenancy or role system.
4. Complete browser login, protected API access, session expiration and local logout. Clearly distinguish local logout from IdP logout. Protect cookie-authenticated mutations and avoid stale responses replacing a newer session.
5. Keep initial setup, local login, personal-access-token access and existing deployments compatible within the stated supported mode.

The public-URL/static groundwork and complete subpath deployment are separate work under upstream PR #198. OIDC's first supported deployment should be explicitly root-path unless the completed subpath slice has its own acceptance evidence. Do not claim prefixed deployment coverage from skipped tests. The session prerequisite does not require that URL slice; a future OIDC slice must name its exact prerequisite commits instead of copying overlapping helper changes.

Before opening the complete OIDC slice for upstream review, recount its production files and added/deleted lines against the 20-file/800-line budget. If the smallest secure complete feature still exceeds it, request a current-head maintainer exception rather than splitting an unusable login flow into artificial stages.

## Acceptance gate for the complete OIDC slice

Bind the report to the actual feature head. Record provider/version, non-secret deployment settings, commands, and sanitized evidence for:

- Real IdP authorization-code round trip in a browser, including consent/cancellation or failure and retry.
- Browser login, refresh, logout, expired session, concurrent/stale responses and access denial after logout.
- Existing local login with SSO disabled; explicitly supported password fallback with SSO enabled.
- Shared-owner permission/asset warning in the login flow and both English and Chinese documentation.
- Trust boundary, cookie/CSRF behavior and the exact supported root/subpath deployment.

Current local unit tests cannot establish this gate. Browser execution is blocked by the previously verified Chromium socket restriction in the available environment, and no authorized real IdP test setup is available. These remain unexecuted, not passing. Identity-provider accounts, credentials, persistent access and security configuration require separate authorization where applicable.

Gateway/header authentication, multi-owner asset isolation, permissions/RBAC, and additional IdP-specific deployment paths are deferred. No current upstream PR or full-feature fork branch is replaced by this prerequisite.
