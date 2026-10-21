# ADR-0005: Passwordless Email OTP & Client-Bound JWT Authentication

- **Status:** accepted
- **Date:** 2026-10-21
- **Related specs:** AUTH-001, AUTH-002, AUTH-003, AUTH-004

## Context
Standard password-based authentication suffers from password reuse, database credential stuffing, and requires complex password reset flows. Concurrently, standard JWT implementations are vulnerable to token theft: if an access token is leaked (e.g. through client-side XSS or proxy logs), an attacker can use it from anywhere until expiration.

## Decision
1. **Passwordless Login**: Users authenticate using a cryptographically secure 6-digit One-Time Password (OTP) sent to their registered email address, valid for 5 minutes with a maximum 3-attempt brute-force lockout.
2. **Client-Bound Tokens**:
   - At issuance, a client fingerprint hash (`SHA-256(ip + user_agent)`) is generated and embedded in the JWT payload as claim `client_hash`.
   - `JWTClientBindingMiddleware` verifies this claim on incoming HTTP requests. If the request IP or User-Agent does not match the token's `client_hash`, the request is immediately rejected with HTTP 403 `Client mismatch`.
   - Tokens are signed with `HS512` using the server's secret key.

## Alternatives considered
| Option | Pros | Cons |
| --- | --- | --- |
| Passwords + Salted Hashing | Familiar user flow | Password fatigue, credential stuffing attacks, complex reset flows. |
| Unbound standard JWTs | Simple validation | Stolen tokens can be exploited from any IP or device globally until TTL expires. |
| Mutual TLS (mTLS) client certificates | Hardware-grade binding | Impractical for standard web browser and consumer mobile clients. |

## Consequences
**Positive**
- Eliminates password storage and credential exposure.
- Dramatically reduces the blast radius of intercepted access tokens.

**Negative / trade-offs**
- Users whose IP addresses rotate mid-session (e.g. switching between Wi-Fi and mobile data) will experience a signature mismatch and must refresh their token.

**Rules this creates**
- All custom authentication issuance and verification must route through `utils/jwt_util.py`.
