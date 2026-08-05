# SSRF en fetches de salida: scrape con `WebBaseLoader` + gap de DNS en `callWebhook` (SEC-SSRF-03 / SEC-SSRF-01)

| | |
|---|---|
| **ClickUp** | https://app.clickup.com/t/9013463087/86ajfwqgb |
| **GitHub** | https://github.com/aspe-ai/main_service/issues/1698 |
| **Severidad** | SEC-SSRF-03 Alta · SEC-SSRF-01 Media (blind) |
| **Estado** | **Arreglado en PR, sin mergear** (2026-08-05). Sale en observe-only: hay que mirar los logs y recién después prender `SSRF_GUARD_ENFORCE` |
| **Repo afectado** | `main_service` |
| **Plan de arreglo** | [`PLAN-ssrf-1698.md`](PLAN-ssrf-1698.md) |

---

## El problema en una frase

Varios caminos de salida permiten que el usuario haga que **nuestro pod** haga la petición por él. El
de scraping no valida **nada** y además le devuelve el contenido al atacante a través de las
respuestas del bot (SSRF de **lectura completa**). El de `callWebhook` sí tiene un validador decente,
pero sólo bloquea IPs privadas **escritas literalmente**: un hostname que resuelve por DNS a una IP
privada pasa (SSRF ciego).

> **Este doc empezó describiendo 2 caminos. Son 4 sinks y 6 puntos de entrada** — ver
> [Inventario completo de sinks](#inventario-completo-de-sinks) más abajo. Los dos que el doc
> describía en detalle (scrape `/{bot_id}/urls` y `callWebhook`) siguen siendo válidos; los otros se
> descubrieron al armar el plan (2026-08-04) y al verificarlo (2026-08-05).

---

## Inventario completo de sinks

> **Actualizado 2026-08-05 tras un barrido exhaustivo del repo.** El issue y la primera versión de
> este doc hablaban de 4 sinks. Son **12 con URL variable**. Los otros ~150 fetches del repo tienen
> host fijo (Meta, Telegram, Hasura, Slack, S3) y no son vector.

Ordenados por gravedad real, que la da el **canal de lectura**, no el endpoint:

| # | Sink | Entrada | Auth | ¿El contenido vuelve? |
|---|---|---|---|---|
| **1** | `messaging_core/helpers/media_uploader.py:118/136` | `POST /messaging/send-message` (`media_url`) | usuario con acceso al bot | **El más fuerte:** el body se sube a **S3** y la URL pública queda en el mensaje → el atacante baja el objeto |
| **2** | `routers/sitemap.py` → `services/sitemap.py:7` | `POST /scan_sitemap` | `get_current_user` — **sólo logueado** | **Sí, directo:** devuelve los `<loc>` parseados. Y es **recursivo** sin revalidar |
| **A** | `WebBaseLoader` en `services/onboarding.py:118` | `POST /onboarding/discover` | `get_current_user` — **sólo logueado, no exige bot** | **Sí, directo:** devuelve `business_info` en la misma respuesta |
| **3** | `helpers/webhooks_manager.py:46` | `POST /webhook/add_bot_webhook` (API key) → DB | dueño del bot | **Sí:** `response.json()` se guarda en los logs de webhooks, que el tenant lee |
| **4** | `messaging_core/helpers/media_processor.py:321` | igual que 1, con `media_type=audio` | ídem | Sí: el body va a Whisper y la transcripción queda como texto del mensaje |
| **5** | `helpers/documents.py:190/240` | attachments de flows (DB) y `media_url` de mensajes | dueño del bot | Sí: el texto extraído se inyecta en el prompt del LLM |
| **B** | `WebBaseLoader` en `services/scrape.py:23` | `POST /{bot_id}/urls` | `has_access_to_bot` | Sí, indirecto: vía FAQs → preguntarle al bot |
| **6** | `whatsapp/services/wa_messaging_service.py:426` | `POST /wa/{bot_id}/audio_message` | ídem | Sí: el body va a S3 y queda en `metadata.audio.media_url` |
| **C** | MCP — **4 puntos de entrada**, ver abajo | 4 rutas | `_require_bot_access` | Parcial: nombres/schemas de tools y sus resultados |
| **D** | `callWebhook` (`helpers/scenario_webhook_*`) | escenarios | dueño del bot | **No** — ciego |

Casos aparte, de menor riesgo:

- **Paginación de Graph API** (`wa_message.py:1298/1344`, `messenger_api.py:231/290`): la URL sale del
  `paging.next` de la respuesta anterior de Meta, no del usuario. Ninguno valida el host contra
  `graph.facebook.com`. Riesgo bajo, no arreglado.
- **Shopify** (`shopify_client.py:210`): la URL viene de la Bulk Operation API. Los otros dos fetches
  de Shopify sí están anclados por `is_valid_shop_domain` (regex `*.myshopify.com`).

### El detalle que hace grave al #1

`is_cdn_url` (`media_uploader.py:247`) existe y valida bien (HTTPS + dominio exacto de
fbcdn/fbsbx/cdninstagram). Pero **`ensure_mime_extension` nunca lo consulta**: decide por la extensión
del *path*. Una URL sin extensión conocida —como `http://10.43.135.179/graphql`— cae directo al
`_download_media`. Por eso el guard va **abajo**, en `_download_media`, y no al lado de cada
`is_cdn_url`.

**A es el peor y no estaba en este doc:** es el que tiene la auth más débil (no exige propiedad de
bot) y el único con canal de lectura **de un solo paso** — no hace falta crear bot, upsertear FAQs y
después interrogar al bot como en B.

`Step0Data.validate_websites` (`models/models.py:32-43`) parece proteger a A pero no: es sólo
`re.match(r"^https?://[^\s]+$", url)`. Valida **esquema y nada más** — pasan
`http://10.43.135.179/`, `http://169.254.169.254/` y cualquier puerto.

### El sink C tiene 4 puntos de entrada, y `_build_connection` NO los cubre a todos

| Punto de entrada | De dónde sale la URL | Nota |
|---|---|---|
| `POST /mcp/servers/validate-and-create` (`routers/mcp.py:79`) | del payload (`schemas.py:24` → `url: str` pelado) | primer orden |
| `POST /mcp/servers/{id}/refresh` (`routers/mcp.py:191`) | **de la DB** (`server["url"]`) | **2º orden:** una fila ya envenenada se re-fetchea en cada refresh |
| `call_tool` (`discovery_service.py`) | **de la DB** | **2º orden:** corre cuando el bot ejecuta una tool |
| **`agent_runner/tools.py:167`** | **de la DB**, vía `MCPService.get_mcp_tools_for_bot` | **el más caliente: corre cuando el bot conversa.** Construye `MultiServerMCPClient` **directo**, sin pasar por `_build_connection` |

Los tres primeros sí pasan por `_build_connection`, que es su choke point. **El cuarto no**, así que
hacen falta dos guards: uno en `_build_connection` y otro en el loop de `service.py:80-88` que arma el
dict de conexiones. Ese segundo saltea el servidor rechazado en vez de fallar, para que el bot siga
conversando con el resto de sus tools.

Consecuencia práctica: además del guard hay que **revisar las filas que ya están** en `mcp_servers`
(al 2026-08-05: 97 filas, todas `https` a puerto default, ninguna interna — limpio hoy).

`_build_connection` valida **sólo `transport`**, nunca la URL. Y acepta `websocket`, así que en teoría
alcanza puertos que no son HTTP.

### Lo que NO es un sink (para no perder tiempo)

`routers/onboarding.py:55` llama `handle_scrape(bot_id, doc_sites, False)`. Con `scrape=False` la
función **no fetchea nada** (usa `url.get("docs")`). No es un sink.

---

## Hallazgo sistémico: el cliente HTTP compartido sigue redirecciones

`src/core/http_client.py:35` crea el cliente con **`follow_redirects=True`**. Cualquier camino que
valide la URL y después use `http_client` queda **evadible con un 302**: se valida `evil.com`, y el
redirect lleva al destino interno. No es de un endpoint — es el default compartido, y **se come el
pinning de IP** si no se arregla.

`scenario_webhook_http.py:77` sí pasa `follow_redirects=False`; es la excepción, no la regla.

---

# SEC-SSRF-03 — Scrape: sin validación, y con canal de lectura

## El código, hoy

`src/services/scrape.py:23-25` — el sink, sin un solo chequeo:

```python
loader = WebBaseLoader(url, header_template=header_template)
documents = loader.load()
```

No hay validación de esquema, ni de host, ni de IP, ni de puerto. Sigue redirecciones por default.
La entrada es `POST /{bot_id}/urls` (`src/routers/documents.py:60-72`): recibe `urls: List[str]` sin
validar y lo dispara como background task.

Está autenticado con `has_access_to_bot`, así que no es cualquiera de internet — **es cualquier dueño
de bot**. En un producto multi-tenant eso es efectivamente "cualquier cliente que se registre".

## Lo que lo hace grave: el contenido vuelve

Esto no es SSRF ciego. La cadena completa:

1. El atacante manda una URL interna en `urls`.
2. El pod la fetchea desde **dentro** del cluster.
3. El texto de la respuesta se resume a FAQs con el LLM (`generate_faqs_from_text`) y se hace upsert
   al store de flow-FAQ/RAG del bot con sus embeddings.
4. El atacante le pregunta al bot y **lee de vuelta** lo que el pod cosechó.

La lectura es mediada por el LLM (semi-verbatim, no byte a byte), pero es un canal de exfiltración
que funciona.

## Corrección importante al diagnóstico original: acá no hay IMDS de AWS

El issue y el diagnóstico giran alrededor de robar credenciales de nube vía `169.254.169.254`, y
dejan como pregunta abierta si IMDSv2 está forzado. **Esa pregunta es irrelevante en esta
infraestructura.** Verificado:

```
kubectl get nodes -o jsonpath='{...providerID}'
→ california-01, dallas-02, dallas-03, dallas-edge-01 — todos provider=k3s://<nombre>
```

Es **k3s sobre nodos propios**, no EKS ni GKE. No hay IMDS de AWS que blindar con IMDSv2, y la lista
de bloqueo actual (`metadata.google.internal`, `metadata.goog`) apunta a **GCP**, que tampoco
aplica. O sea: el trabajo de blocklist que ya está hecho cubre una nube que no usamos.

Y agregar `169.254.169.254` a la blocklist **no hace falta**: para IP literal ya está cubierto.
Verificado ejecutando la propia función:

```
169.254.169.254   bloqueada_si_literal=True   (is_link_local)
fd00:ec2::254     bloqueada_si_literal=True   (is_private)
10.43.135.179     bloqueada_si_literal=True   (is_private)
```

`_is_blocked_ip_address` ya chequea `is_link_local` e `is_private`. **El gap es exclusivamente por
DNS.** Dejar ese ítem como pendiente da falsa sensación de trabajo por hacer.

**El objetivo real acá no es la nube: son los servicios internos del cluster.**

## El objetivo real: todo el cluster, sin restricción de egress

No hay NetworkPolicy de egress en ninguna parte. La única del cluster entero es `postgresql-dev` en
`aspe-dev`, que no tiene nada que ver:

```
kubectl get networkpolicy -A   →  1 sola: aspe-dev/postgresql-dev
```

Entonces el pod alcanza cualquier servicio del namespace. Blancos concretos en `aspe`:

| Servicio | ClusterIP | Puertos | Por qué importa |
|---|---|---|---|
| `n8n-aspe` | 10.43.120.156 | 80, 5679 | automatización; suele guardar credenciales de terceros |
| `metabase-aspe` | 10.43.53.233 | 80 | BI sobre la base de producción |
| `hasura-prod` | 10.43.135.179 | 80 | GraphQL sobre la base (mutaciones necesitan el admin secret) |
| `hasura-prod-read` | 10.43.97.21 | 8080 | réplica de lectura |
| `wuzapi` | 10.43.211.218 | 8080 | API de WhatsApp |
| `presidio` | 10.43.162.204 | 3000 | servicio de PII |
| `livekit-agent-service` | 10.43.130.201 | 8080 | agente de voz |
| `pgpool` | 10.43.174.128 | 5432, 9898 | pooler de la base de prod |
| `redis` | 10.43.233.112 | 6379 | colas y estado |
| `main-service-prod` | 10.43.183.146 | 8000 | **nosotros mismos** — ver nota abajo |

**El scrape no tiene allowlist de puertos**, así que llega a los `8080`, `3000` y `5679` de esa tabla.
`callWebhook` sí la tiene (sólo 80/443), así que su alcance es más chico. Es una diferencia real entre
los dos caminos y conviene no perderla al unificar el guard.

> Nota de composición con [`hooks-hasura-sin-autenticacion.md`](hooks-hasura-sin-autenticacion.md):
> el scrape hace GET y los `/hooks/*` son POST, así que **no** se combinan directamente. Pero los dos
> hallazgos comparten la misma causa de fondo: se asumió que "la red interna es confiable" y no hay
> nada que lo haga cierto.

---

# SEC-SSRF-01 — `callWebhook`: el validador no resuelve DNS

## El código, hoy

`src/helpers/scenario_webhook_security.py:113-116`, con la limitación admitida en el propio comentario:

```python
# Block literal private/reserved IPs in URL (hostnames are not resolved here).
parsed_ip = _parse_ip_address(hostname)
if parsed_ip is not None and _is_blocked_ip_address(parsed_ip):
    return False, "blocked_ip"
```

`_parse_ip_address` devuelve `None` para un hostname, así que el chequeo de IP **se saltea entero**.
Un dominio cuyo registro A apunta a `10.43.x.x` o a `169.254.169.254` pasa: no es IP literal y no
está en `_BLOCKED_HOSTNAMES` (que son 3 entradas: `localhost`, `metadata.google.internal`,
`metadata.goog`).

Lo que **sí** está bien y hay que preservar: allowlist de esquema (`http`/`https`), rechazo de
credenciales en la URL, allowlist de puertos (80/443), validación de headers contra inyección, y
`follow_redirects=False` (`scenario_webhook_http.py:77`).

## Por qué es Media y no Alta

El cuerpo de la respuesta **nunca** se le muestra al atacante y no se siguen redirecciones. Es ciego
o semi-ciego. Sirve para mapear puertos internos y para **disparar endpoints internos que tienen
efecto** — no para leer datos. De ahí la diferencia con el camino de scrape.

---

## El arreglo

Un guard compartido `validate_and_resolve_url()` usado por los dos caminos:

1. Validaciones estáticas que ya existen en `validate_webhook_url` (esquema, userinfo, puertos, hosts
   bloqueados) — reutilizarlas, no reescribirlas.
2. **Resolver DNS y chequear cada registro A/AAAA** contra `_is_blocked_ip_address`. Esto es lo que
   cierra el gap.
3. **Pinnear la conexión a la IP validada** (transport/resolver propio de httpx). Sin esto queda
   abierto el DNS rebinding: se resuelve a una IP buena, se valida, y entre la validación y la
   petición el atacante cambia el registro.

> ~~4. Agregar `169.254.169.254` y `fd00:ec2::254` a la blocklist, defensivo.~~ **Redundante** — ya
> cubiertos por `is_link_local`/`is_private` cuando son IP literal (ver la verificación más arriba).
> El gap es exclusivamente por DNS, y lo cierra el punto 2.

### Qué dice la data de prod sobre la allowlist de puertos (medido 2026-08-05)

La pregunta abierta era si poner allowlist de puertos en el scrape rompe clientes. Medido sobre lo que
realmente se scrapeó (`bot_flows`, `metadata->>'source_type' = 'url/website'`):

| Métrica | Valor |
|---|---|
| FAQs generadas desde URL | 86 557 |
| URLs distintas históricas | 1 126 |
| **con puerto explícito** | **0** |
| `http://` plano | 286 (25 %) |
| `https://` | 836 |
| apuntando a IP privada / localhost | **0** |

Y en MCP (`mcp_servers`): 97 filas, **0** con puerto explícito, **0** con scheme `ws://`, **0** con
`http://` plano — todas https a puerto default.

**Conclusiones:**
1. La allowlist **80/443 no rompe nada medible**, ni en scrape ni en MCP. Deja de ser una decisión de
   criterio. La preocupación de que "MCP usa websocket y necesita otros puertos" es teórica: nadie lo
   usa así en prod.
2. **Hay que permitir `http` además de `https`**: un cuarto de las URLs legítimas es http plano.
3. Sin señales de explotación previa. **Evidencia parcial**: sólo mide lo que llegó a guardarse como
   FAQ; un fetch interno que falló o que no generó FAQs no dejaría rastro acá.

### Trampa de implementación: el scheme en MAYÚSCULAS

En prod hay 4 URLs legítimas así: `HTTP://WWW.FEPCONSULTING.COM.AR`,
`Http://myboatincancunrentadeyates.com`, `Https://Www.sflhouses.com`,
`Https://www.hectornucamendig.com`.

- `validate_webhook_url` las **tolera**: usa `urlparse`, que normaliza el scheme a minúsculas.
- `Step0Data.validate_websites` las **rechaza con 422** — su regex `^https?://` es case-sensitive.
  Es un **bug preexistente**, no del guard nuevo. Conviene arreglarlo de paso, y sobre todo **no
  replicar el patrón regex** dentro del guard.

### Tres cosas específicas al implementar

**El scrape necesita más que el guard.** Hay que **apagar el seguimiento de redirecciones** en
`WebBaseLoader` (o revalidar cada salto), porque hoy las sigue: si no, se valida `evil.com` y el
302 lleva al destino interno. Y decidir qué hacer con el allowlist de puertos: hoy el scrape no
tiene ninguno.

**Es un background task, así que el rechazo no se ve.** `documents.py:72` dispara `handle_scrape` con
`background_tasks.add_task`, o sea la respuesta HTTP ya salió cuando el guard rechace. Hay que
validar las URLs **de forma síncrona en el handler**, antes de encolar, para poder devolver un error
al usuario; y dejar el guard también adentro como red de seguridad.

**No romper a los clientes legítimos.** Este guard puede empezar a rechazar URLs que hoy funcionan.
Aplica el mismo molde que sirvió en el #1691: loguear los rechazos primero sin bloquear, ver qué
aparece, y después endurecer.

### Defense-in-depth, en otro carril

NetworkPolicy de egress que niegue el tráfico del pod hacia `169.254.0.0/16` y hacia la CIDR de
servicios del cluster (`10.43.0.0/16`). Es trabajo de infra, no de este repo, y **no** reemplaza el
guard de aplicación — pero es lo único que protege a los demás pods del mismo problema. Ojo que
`main_service` necesita alcanzar `pgpool`, `redis` y `hasura` legítimamente, así que la policy tiene
que ser una allowlist de esos destinos, no un deny plano.

---

## Preguntas abiertas del issue, ya respondidas

| Pregunta | Respuesta (verificada 2026-08-03) |
|---|---|
| ¿Está IMDSv2 forzado (`HttpTokens=required`, hop limit 1)? | **No aplica.** Los nodos son k3s propios (`provider=k3s://`), no EKS. No hay IMDS de AWS. La rama de "robo de credenciales de nube" del diagnóstico no corresponde a esta infra. |
| ¿Hay proxy de egress o NetworkPolicy limitando la salida de los pods? | **No.** La única NetworkPolicy del cluster es `aspe-dev/postgresql-dev`, no relacionada. Egress libre a todo el cluster. |

---

## Orden sugerido de trabajo

1. Guard compartido con resolución de DNS + pinning de IP; tests con hostname→IP privada y con
   rebinding.
2. `follow_redirects` del cliente compartido (ver hallazgo sistémico) — decidir con el `grep` en la
   mano quién depende de seguir redirecciones.
3. Aplicarlo al camino de `callWebhook` (el más contenido: ya tiene validador y allowlist de puertos).
4. Aplicarlo al scrape (sinks **A** y **B**), que es el que más cambia: validación síncrona antes de
   encolar, redirecciones apagadas, allowlist 80/443.
5. Aplicarlo a MCP (sink **C**) en `_build_connection`, que cubre los 3 puntos de entrada de una.
6. Rollout **observe-only → enforce**, no endurecer de golpe.
7. NetworkPolicy de egress como allowlist, coordinado con infra (fuera de este repo).

El plan ejecutable con fases, decisiones y comandos de verificación está en
[`PLAN-ssrf-1698.md`](PLAN-ssrf-1698.md).

## Referencia cruzada

- [`README.md`](README.md) — índice de la auditoría.
- [`hooks-hasura-sin-autenticacion.md`](hooks-hasura-sin-autenticacion.md) — misma causa de fondo
  ("la red interna es confiable"), y la ausencia de NetworkPolicies es evidencia compartida.
- Issue [#1691](https://github.com/aspe-ai/main_service/issues/1691) — molde de rollout
  observe-only → enforce para no cortar tráfico legítimo.
- El módulo de seguridad de webhooks nació en `d39f7da2` ("Security and validations improvements"):
  ese trabajo estuvo bien hecho y hay que **extenderlo**, no reemplazarlo. `scrape.py` nunca entró en
  ese esfuerzo.
