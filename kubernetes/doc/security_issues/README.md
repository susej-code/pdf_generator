# Issues de seguridad — auditoría interna de junio 2026

Un doc por issue. **Cada doc es autosuficiente**: tiene el problema explicado, la evidencia verificada
contra el código y el cluster, el plan de arreglo con sus trampas, y las preguntas abiertas ya
respondidas. La idea es trabajar desde acá y **no** tener que volver a abrir el ticket de GitHub ni el
de ClickUp para entender qué hay que hacer.

Los hallazgos `SEC-*` vienen de la auditoría de seguridad interna de junio 2026.

## Pendientes

| Doc | Hallazgos | Qué es | ClickUp | GitHub |
|---|---|---|---|---|
| [`hooks-hasura-sin-autenticacion.md`](hooks-hasura-sin-autenticacion.md) | SEC-WEB-04, SEC-PAY-04 | Los 5 endpoints `/hooks/*` de Hasura no verifican quién llama; alcanzables desde internet hoy (sin NetworkPolicy, ingress en `/`) | [86ajfwqex](https://app.clickup.com/t/9013463087/86ajfwqex) | [#1692](https://github.com/aspe-ai/main_service/issues/1692) |
| [`stripe-webhook-secreto-e-idempotencia.md`](stripe-webhook-secreto-e-idempotencia.md) | SEC-PAY-01, SEC-PAY-02 | El body sin verificar elige el secreto de firma (y ese secreto de test **está compartido con dev**); sin idempotencia por `event.id` → créditos duplicados | [86ajfwqfc](https://app.clickup.com/t/9013463087/86ajfwqfc) | [#1694](https://github.com/aspe-ai/main_service/issues/1694) |
| [`presigned-url-sin-auth.md`](presigned-url-sin-auth.md) | SEC-WEB-05, SEC-SSRF-02 | `/generate_presigned_url` sin auth: cualquiera mintea URLs de PUT a S3 con el prefijo y el content-type que elija, sin tope de tamaño | *(tarea "Authenticate /generate_presigned_url" en SCOPING)* | [#1697](https://github.com/aspe-ai/main_service/issues/1697) |
| [`ssrf-scrape-y-callwebhook.md`](ssrf-scrape-y-callwebhook.md) | SEC-SSRF-03, SEC-SSRF-01 | El scrape fetchea cualquier URL sin validar y devuelve el contenido vía Q&A del bot (SSRF de lectura); `callWebhook` no resuelve DNS | [86ajfwqgb](https://app.clickup.com/t/9013463087/86ajfwqgb) | [#1698](https://github.com/aspe-ai/main_service/issues/1698) |

## Resueltos

| Hallazgos | Qué era | Cerrado | GitHub |
|---|---|---|---|
| SEC-WEB-01/02/03 | Firmas `X-Hub-Signature-256` en webhooks de Meta: WhatsApp, Instagram y `/fb/deauthorize` | 25-29 jun 2026 — helper `src/helpers/meta_signature.py`, rollout observe-only → enforce | [#1691](https://github.com/aspe-ai/main_service/issues/1691) |
| SEC-AUTH-01/02 | Auth en endpoints de extensión de trial de Stripe + IDOR en upsert de `/flows` | jun 2026 | [#1693](https://github.com/aspe-ai/main_service/issues/1693) |
| SEC-SECRET-02 | Se logueaba el admin secret de Hasura a nivel CRITICAL en `gql_utils.py` | jun 2026 | [#1696](https://github.com/aspe-ai/main_service/issues/1696) |

El #1691 sirve de molde para los pendientes: helper compartido + ventana observe-only antes de pasar
a rechazar, para no cortarle el tráfico a nadie por un secreto mal configurado.

## Cancelados

| Hallazgos | Qué era | Por qué |
|---|---|---|
| SEC-SECRET-01 | API key del voice service hardcodeada (`service_auth.py`), comparación con `!=`, y el campo de `Settings` comentado | **La funcionalidad de voz no se usa** → se decidió no arreglarlo (2026-08-03). Ver la nota de abajo antes de darlo por cerrado. Issue [#1695](https://github.com/aspe-ai/main_service/issues/1695) |

> **Si la voz se revive, ojo con dos cosas** (verificadas 2026-08-03): los endpoints `/voice/*`
> **siguen registrados y sirviéndose** en prod (`src/main.py:298`) y el deployment `livekit-agent`
> sigue `Running` en el namespace `aspe`, así que la superficie está viva aunque el producto no se
> use. Y aparte —**independiente de la decisión de cancelar**— `voice_poc/livekit-agent/.env.local`
> está commiteado y **no** gitignoreado, con claves reales de OpenAI, ElevenLabs, Deepgram, Cartesia
> y LiveKit; esas claves cuestan dinero y están en la historia de git. Eso amerita su propio ticket.
> El trabajo del fix no se perdió: sobreviven las ramas `fix/sec-secret-01-voice-service-key`
> (main_service, PR #1777) y `fix/sec-secret-01-remove-voice-key` (next, PR #763) — los dos PRs los
> cerró un bot de dedup por error, no por rechazo técnico.

## Panorama del audit

El tracker maestro es [#1700](https://github.com/aspe-ai/main_service/issues/1700): **60 hallazgos —
0 Critical / 15 High / 26 Medium / 19 Low**. Los High están todos cubiertos por las tablas de arriba.
Los 26 Medium + 19 Low viven agrupados en [#1699](https://github.com/aspe-ai/main_service/issues/1699)
(cerrado) y no tienen doc propio todavía.

Advertencia del propio audit que vale al trabajar estos: es **análisis estático**, no se ejecutó
código. Donde la configuración de runtime decide (ACLs del bucket, NetworkPolicies, IMDSv2, RBAC), hay
que verificarla — y de hecho al hacerlo aparecieron cosas que el audit asumió mal (no hay IMDS de AWS
porque el cluster es k3s propio; y el secreto de test de Stripe sí está compartido con dev).

## Convenciones al trabajar estos

- **Un branch y un PR por doc**, base `development` (no `main`).
- Si el arreglo necesita SQL, ojo: `main_service` **no** corre migraciones automáticas — el DDL se
  aplica a mano en la base **antes** de desplegar el código que lo usa, y en prod se pide OK antes.
- Cuando cierres uno, movelo a la tabla de *Resueltos* con la fecha y el commit/PR, y actualizá el
  encabezado de estado del doc.
