# `/hooks/*` de Hasura sin autenticación de caller (SEC-WEB-04 / SEC-PAY-04)

| | |
|---|---|
| **ClickUp** | https://app.clickup.com/t/9013463087/86ajfwqex |
| **GitHub** | https://github.com/aspe-ai/main_service/issues/1692 |
| **Severidad** | Alta — confirmada adversarialmente |
| **Estado** | **Abierto, sin arreglar** (verificado 2026-08-03) |
| **Repo afectado** | `main_service` |

---

## El problema en una frase

Los 5 endpoints `/hooks/*` que Hasura usa para avisarnos de eventos de base de datos están montados
en la **misma app FastAPI pública** que los webhooks de Stripe/WhatsApp/Instagram, y **no verifican
quién los llama**: ni HMAC, ni secreto compartido, ni dependency de auth. Cualquiera en internet que
adivine un `bot_id` puede fabricar eventos de CRM, sacar PII de contactos y usar nuestro servidor
como proxy de salida.

## Por qué existe

El modelo de confianza era *"a estas rutas sólo las llama Hasura desde la red interna"*. Nunca se
implementó nada que lo hiciera cierto. No es una regresión: estas rutas nacieron así en noviembre de
2024 y el único cambio posterior al router (abril 2026) fue funcional, no de seguridad.

## La suposición no se sostiene

Esto es lo que convierte el hallazgo en urgente en lugar de defense-in-depth. Verificado contra el
cluster de producción:

```
kubectl get networkpolicy -n aspe   →  No resources found
kubectl get ingress main-service-prod -n aspe -o jsonpath='{...paths[*].path}'   →  /
```

- **No hay NetworkPolicy** en el namespace `aspe`. Ninguna.
- El ingress `main-service-prod` (`main-prod.aspe.ai`) publica **`path: /`** — todo el árbol de rutas,
  sin allowlist de IP ni middleware de Traefik que filtre por prefijo.

Traducción: `/hooks/on_new_lead` es alcanzable con un `curl` desde cualquier parte, hoy, sin
credenciales. Y como el router se registra con `include_in_schema=False`, tampoco aparece en el
OpenAPI — lo cual da cero protección real (las rutas son adivinables) pero sí evita que sea obvio.

---

## Superficie expuesta

`src/modules/hooks/routers/hasura_hooks.py`, registrado en `src/main.py:273` sin `dependencies=[]`
ni middleware:

| Ruta | Qué hace | Chequeo actual |
|---|---|---|
| `POST /hooks/on_new_contact` | despacha evento a los suscriptores del bot | **ninguno** |
| `POST /hooks/on_new_bot_contact` | `SELECT` de PII del contacto → despacha | **ninguno** |
| `POST /hooks/on_new_lead` | despacha evento de lead nuevo | **ninguno** |
| `POST /hooks/on_new_message` | despacha mensaje entrante | `verify_webhook_subscription` |
| `POST /hooks/on_contact_lead_status_update` | despacha cambio de status | `verify_webhook_subscription` |

`verify_webhook_subscription` **no es autenticación**. Pregunta *"¿este bot tiene un webhook
configurado para este evento?"* — una consulta de configuración. Un atacante que apunta a un bot que
sí tiene webhooks configurados la pasa sin fricción; de hecho sólo filtra los blancos inútiles.

## Los tres impactos

**1. Falsificación de eventos.** Un `POST` a `/hooks/on_new_lead` con un `bot_id` adivinado y campos
inventados se despacha a las integraciones del cliente como si Hasura lo hubiera emitido. El
`bot_id` viene **del body**, que es exactamente lo que el atacante controla. Se le está dando a un
identificador el peso de una identidad.

**2. Exfiltración de PII.** `notify_new_bot_contact`
(`src/modules/hooks/services/hasura_hooks_service.py:60-100`) hace un `SELECT` que trae `name`,
`last_name`, `email`, `phone`, `address`, `city`, `state`, `postal_code`, `country`, `notes`, `tags`,
`metadata`, `lead_source`, `organization_id` y lo manda a los suscriptores. Basta un `contact_id`
para que el servidor lo busque y lo entregue.

**3. Pivote SSRF / proxy de salida.** `src/helpers/webhooks_manager.py` hace
`await client.post(endpoint_url, json=payload)` contra la URL que el cliente configuró, con el
payload que llegó por HTTP. Es un POST arbitrario originado desde dentro de nuestra red, con
reintentos incluidos.

---

## El arreglo

Header de secreto compartido, comparado en tiempo constante, fail-closed. Es la Opción A del
diagnóstico y sigue siendo la correcta:

1. Nueva env var `HASURA_WEBHOOK_SECRET` (hoy no existe: `grep` en `src/` da 0 resultados para
   `HASURA_WEBHOOK_SECRET`, `verify_hasura_caller` y `X-Hasura-Webhook`).
2. Dependency que lee el header (`X-Hasura-Webhook-Secret`) y hace `hmac.compare_digest` contra el
   valor esperado. Si falta o no cuadra → **403**.
3. Aplicarla a las 5 rutas de una vez: `include_router(..., dependencies=[Depends(verify_hasura_caller)])`.
4. Dejar de tratar el `bot_id` del body como identidad. Sólo significa algo *después* de probar que
   quien llama es Hasura.
5. Defense-in-depth aparte: NetworkPolicy que limite `/hooks/*` al segmento de Hasura.

### Dos cosas que hay que tener presentes al implementarlo

**El guard va en el router, no en el service.** `src/helpers/session_analyzer.py:283,320` llama
`notify_new_contact` y `notify_new_lead` **directo sobre el service**, sin pasar por HTTP. Si el
chequeo se mete en los métodos del service, se rompe el flujo interno. En el router no se toca.

**Despliegue en lockstep.** El header hay que agregarlo en los event triggers de Hasura *antes o al
mismo tiempo* que el 403 entra en vigor, o los eventos legítimos empiezan a rebotar. Si se quiere
margen, conviene el mismo patrón que funcionó en el issue #1691: **observe-only primero** (loguear
mismatches sin rechazar), revisar que el volumen legítimo verifique limpio, y recién entonces
flipear a enforce.

### Sobre firmar el payload (Opción B del diagnóstico): no aplica

Los event triggers de Hasura soportan **custom headers** (valor estático o desde env var), pero **no
firman el body**. No hay HMAC del payload que recomputar, así que la Opción B no es viable
nativamente y la discusión de "¿lo soporta nuestra versión?" es irrelevante. Como dato: los
deployments `hasura-prod` y `hasura-prod-read` corren `hasura/graphql-engine:latest`, sin pin de
versión.

---

## Preguntas abiertas del issue, ya respondidas

| Pregunta | Respuesta (verificada 2026-08-03) |
|---|---|
| ¿Hay ingress o NetworkPolicy que restrinja `/hooks/*` a Hasura? | **No.** Cero NetworkPolicies en ns `aspe`; ingress con `path: /`. Internet-reachable hoy. |
| ¿Qué instancias de Hasura disparan los triggers? | `hasura-prod` y `hasura-prod-read` en ns `aspe` (`gql-prod.aspe.ai`, `gql-prod-ro.aspe.ai`). |
| ¿Qué versión de Hasura? | `hasura/graphql-engine:latest` — sin pin. Irrelevante para el fix, ver arriba. |

## Referencia cruzada

El issue [#1691](https://github.com/aspe-ai/main_service/issues/1691) (firmas de webhooks de Meta en
WhatsApp / Instagram / deauthorize) es el hermano de este y **sí está resuelto**: cerrado entre el
25 y el 29 de junio de 2026, con el helper compartido `src/helpers/meta_signature.py` y el patrón
observe-only → enforce. Vale como plantilla de cómo hacer este.
