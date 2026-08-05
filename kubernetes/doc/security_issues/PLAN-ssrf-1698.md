# PLAN — Arreglo de SSRF en fetches de salida (#1698 / SEC-SSRF-01 + SEC-SSRF-03)

| | |
|---|---|
| **Estado** | **Código completo, PR abierto sin mergear** (2026-08-05). Falta el rollout observe-only → enforce. |
| **Rama** | `fix/ssrf-outbound-guard-1698` → PR base `development`, 7 commits |

> **El alcance creció durante la ejecución.** El plan original cubría 4 sinks; un barrido exhaustivo
> encontró **12**, varios peores que los documentados (ver el inventario en el doc del hallazgo). Se
> decidió cerrarlos todos en este PR. Lo que sigue conserva el plan original por trazabilidad; el
> estado real de cada pieza está en la tabla de abajo.

## Estado final por sink

| Sink | Dónde quedó el guard | Semántica |
|---|---|---|
| scrape (onboarding + `/{bot_id}/urls`) | `helpers/web_fetch.py` — los 2 unificados | saltear la URL |
| `POST /scan_sitemap` | `services/sitemap.py` + limpieza de la URL en el router | devolver vacío |
| `webhooks_manager` | antes del POST, con IP pinneada | no dispara, se loguea el rechazo |
| `callWebhook` | reemplaza `validate_webhook_url`, + pinning | error al usuario |
| MCP | `_build_connection` **y** `service.py` (4 entradas) | admin: error; bot: saltear ese server |
| `media_uploader` | `_download_media` (embudo de 7 callers) | saltear, mensaje sin análisis |
| `media_processor` | `_download_media_file` | saltear, `[Could not download audio]` |
| `documents.py` | `download_file_async` + fetch propio en `load_pdf`/`load_docx` | dict de error |
| WhatsApp audio | cliente que revalida cada hop de redirect | abortar (400/500) |
| `POST /messaging/send-message` | guard en el router, corta los 3 caminos de media | 400 |
| **ClickUp** | https://app.clickup.com/t/9013463087/86ajfwqgb |
| **GitHub** | https://github.com/aspe-ai/main_service/issues/1698 |
| **Repo a tocar** | `main_service` (sólo backend) |
| **Doc del hallazgo** | [`ssrf-scrape-y-callwebhook.md`](ssrf-scrape-y-callwebhook.md) |

> **Cómo usar este archivo:** es auto-suficiente. Para ejecutarlo no hace falta la conversación
> donde se armó. Empezar por "Decisiones a confirmar", después seguir las fases en orden.

---

## Qué está pasando (contexto)

La app hace peticiones de salida con **URLs que elige el usuario** (para leer el sitio web del
cliente durante el onboarding, para webhooks de escenarios, para descubrir servidores MCP). El pod
corre **dentro** del cluster, donde los servicios internos no piden credenciales porque asumen que
"la red interna es confiable".

Resultado: un usuario autenticado puede hacer que **nuestro pod** hable con servicios internos, y en
varios caminos **el contenido le vuelve**. Como cualquiera se registra en el producto, en la práctica
la barrera de entrada es crear una cuenta.

Verificado el 2026-08-04 por lectura de código e infra (no se explotó nada):

- **1 sola NetworkPolicy en todo el cluster** (`aspe-dev/postgresql-dev`, no relacionada) → egress
  totalmente libre.
- Nodos **k3s propios** (`k3s://california-01`, `dallas-02`, `dallas-03`, `dallas-edge-01`) → **no
  hay IMDS de AWS**; la rama de "robo de credenciales de nube" del issue original no aplica.
- Blancos internos reales en el namespace `aspe` (ClusterIP verificados):

  | Servicio | ClusterIP | Puertos |
  |---|---|---|
  | `hasura-prod` | 10.43.135.179 | 80 |
  | `hasura-prod-read` | 10.43.97.21 | 8080 |
  | `metabase-aspe` | 10.43.53.233 | 80 |
  | `n8n-aspe` | 10.43.120.156 | 80, 5679 |
  | `wuzapi` | 10.43.211.218 | 8080 |
  | `presidio` | 10.43.162.204 | 3000 |
  | `livekit-agent-service` | 10.43.130.201 | 8080 |
  | `pgpool` | 10.43.174.128 | 5432, 9898 |
  | `redis` | 10.43.233.112 | 6379 |
  | `main-service-prod` | 10.43.183.146 | 8000 |

---

## Los 4 sinks (el doc del hallazgo sólo tiene 2)

### A. `POST /onboarding/discover` — **el peor, y no está en el doc**

- **Sink:** `src/services/onboarding.py:118` → `WebBaseLoader(url, header_template=...)` + `.load()`
- **Router:** `src/routers/onboarding.py:33` → `Depends(get_current_user)`
- **Auth más débil que el resto:** sólo pide usuario logueado, **no** propiedad de bot.
- **Síncrono y con canal de lectura directo:** el handler devuelve `business_info`, que es el
  resumen del LLM sobre el contenido escrapeado. **Una sola petición y el contenido vuelve.** No hace
  falta crear bot → upsert FAQ → preguntarle al bot como en el sink B.
- **Tiene un validador que el doc no registra**, y no alcanza: `Step0Data.validate_websites`
  (`src/models/models.py:32-43`) es sólo `re.match(r"^https?://[^\s]+$", url)`. Valida **esquema y
  nada más**: pasan `http://10.43.135.179/`, `http://169.254.169.254/` y cualquier puerto.

### B. `POST /{bot_id}/urls` — el que sí describe el doc

- **Sink:** `src/services/scrape.py:23-25` → `WebBaseLoader(url)` + `.load()`, cero validación.
- **Router:** `src/routers/documents.py:60-72` → `Depends(has_access_to_bot)`, recibe
  `urls: List[str]` sin validar y lo encola con `background_tasks.add_task(handle_scrape, ...)`.
- **Canal de lectura indirecto:** el contenido va a `generate_faqs_from_text` → embeddings →
  `upsert_flows_faq`, y después se lee preguntándole al bot.
- **Segunda entrada al mismo servicio:** `src/routers/onboarding.py:55` llama
  `handle_scrape(bot_id, doc_sites, False)`. Con `scrape=False` **no fetchea** (usa
  `url.get("docs")`), así que esa entrada no es un sink — **no perder tiempo ahí**.

### C. MCP discovery — **no está en el doc, y tiene 3 puntos de entrada, no 1**

- **Choke point único:** `MCPDiscoveryService._build_connection`
  (`src/modules/mcp/discovery_service.py:40-47`) valida **sólo `transport`**, nunca la URL. Lo
  atraviesan `list_tools` **y** `call_tool`, así que **el guard va acá** — cubre los 3 de una. Parchear
  sólo `schemas.py` no alcanza.

| Punto de entrada | De dónde sale la URL | Orden |
|---|---|---|
| `POST /mcp/servers/validate-and-create` (`routers/mcp.py:79`) | del payload (`schemas.py:24` → `url: str` pelado) | 1º |
| `POST /mcp/servers/{id}/refresh` (`routers/mcp.py:191`) | **de la DB** (`server["url"]`) | **2º** — una fila envenenada se re-fetchea en cada refresh |
| `call_tool` (`discovery_service.py`) | **de la DB** | **2º** — corre cuando el bot ejecuta una tool |

- **Auth:** `Depends(_require_bot_access)`.
- `transport` acepta `websocket`, así que en teoría alcanza puertos no-HTTP. **En prod nadie lo usa:**
  97 filas, 0 con `ws://`, 0 con puerto explícito.
- **Pendiente aparte del guard:** revisar las filas ya guardadas en `mcp_servers` por los sinks de 2º
  orden. Al 2026-08-05 están limpias (ninguna interna).

### D. `callWebhook` — ya tiene guard, con un agujero puntual

- **Validador:** `src/helpers/scenario_webhook_security.py:84-124` → `validate_webhook_url`.
- **Lo que ya está bien y hay que preservar:** allowlist de esquema (`http`/`https`), rechazo de
  userinfo, `_BLOCKED_HOSTNAMES` (`localhost`, `metadata.google.internal`, `metadata.goog`, `*.local`,
  `*.localhost`), allowlist de puertos (**80/443**), validación de headers contra inyección, y
  `follow_redirects=False` (`src/helpers/scenario_webhook_http.py:77`).
- **El agujero:** líneas 113-116. `_parse_ip_address(hostname)` devuelve `None` cuando el hostname no
  es una IP literal, así que el chequeo de IP **se saltea entero**. Un dominio con registro A
  apuntando a `10.43.x.x` pasa.
- **Severidad menor (Media):** el body **nunca** vuelve al atacante y no sigue redirecciones. Sirve
  para disparar endpoints internos con efecto y para mapear puertos, no para leer.

### Problema sistémico aparte: el cliente HTTP compartido sigue redirecciones

`src/core/http_client.py:34` crea el cliente con **`follow_redirects=True`**. Cualquier camino que
valide la URL y después use `http_client` queda **evadible con un 302**. Esto no es de un endpoint —
es del default compartido, y se come el pinning de IP si no se arregla.

---

## Correcciones al doc del hallazgo (aplicar en Fase 0)

1. **El punto 4 del arreglo es redundante.** El doc pide agregar `169.254.169.254` y `fd00:ec2::254`
   a la blocklist. **Para IP literal ya están cubiertos** — verificado ejecutando la propia función:

   ```
   169.254.169.254   bloqueada_si_literal=True   (is_link_local)
   fd00:ec2::254     bloqueada_si_literal=True   (is_private)
   10.43.135.179     bloqueada_si_literal=True   (is_private)
   ```

   `_is_blocked_ip_address` ya chequea `is_link_local` e `is_private`. **El gap es exclusivamente por
   DNS.** Dejar ese punto da falsa sensación de trabajo pendiente.
2. Agregar los sinks **A** y **C** — el doc dice "dos caminos de salida" y son cuatro.
3. Agregar `follow_redirects=True` del cliente compartido como hallazgo sistémico.
4. Agregar `livekit-agent-service` (10.43.130.201:8080) a la tabla de blancos internos.
5. Aclarar que `onboarding.py:55` **no** es un sink (`scrape=False` no fetchea), para que nadie
   pierda tiempo ahí.

---

## Decisiones tomadas (2026-08-05)

1. **Base de las ramas: `development`** (PR base `development`), el flujo normal del repo. Se evaluó
   `main` directo y **se descartó** el 2026-08-05: este agujero requiere autenticación, así que no
   tiene la urgencia del fix de `presigned_url`, y el camino por dev permite **observar los rechazos
   del guard antes de que toquen a un cliente**. Rama: `fix/ssrf-outbound-guard-1698`.
2. **Allowlist de puertos: 80/443 en todos los sinks.** Ya no es criterio, es dato: de 1 126 URLs
   scrapeadas históricamente en prod, **0 usan puerto explícito**; de 97 servidores MCP, **0**. Riesgo
   medido de romper clientes: nulo. **Ojo: permitir `http` además de `https`** — 286 de las 1 126 URLs
   legítimas (25 %) son `http://` plano.
3. **MCP entra en este PR.** Los datos lo abarataron: ningún servidor usa `ws://` ni puertos raros, así
   que la allowlist 80/443 sirve tal cual y no hay que pensar una aparte.

### Regla de trabajo

**Llegar hasta crear el PR y parar.** No mergear nada — ni este PR ni los sync PRs que este doc lista
como pendientes. El merge es del usuario.

---

## Fases

### Fase 0 — Actualizar el doc del hallazgo ✅ HECHA (2026-08-05)

Las 5 correcciones aplicadas a `ssrf-scrape-y-callwebhook.md`, más el inventario de sinks, la data de
prod y la trampa del scheme en mayúsculas. **Sin commitear** en el repo `kubernetes`.

### Fase 1 — El guard compartido (el corazón) ✅ HECHA (2026-08-05)

Implementado en `src/helpers/outbound_url_guard.py`. 27 tests nuevos, todos pasan; los 24 del módulo
de webhooks siguen verdes; ruff limpio. **Ningún sink enchufado todavía.**

Lo que quedó, con nombres reales:

| Pieza | Qué hace |
|---|---|
| `validate_and_resolve_url(url, *, allowed_ports)` → `UrlGuardVerdict` | validaciones estáticas + resuelve DNS y rechaza si **alguna** IP es interna |
| `guard_outbound_url(url, *, context)` | aplica el modo de rollout; **observe-only por default** |
| `PinnedIPTransport(pinned_ip)` | transport de httpx pinneado, preserva `Host` y SNI |
| `settings.SSRF_GUARD_ENFORCE` (default `False`) | off = loguea `[ssrf_guard] would_reject` y deja pasar |

Reutiliza las primitivas de `scenario_webhook_security` vía alias públicos
(`parse_ip_address`, `is_blocked_ip_address`, `hostname_is_blocked`, `ALLOWED_PORTS`) — 7 líneas
agregadas ahí, sin tocar la lógica ya probada.

> **Corrección al plan original: el pinning de IP no es aplicable en todos los sinks.**
> `PinnedIPTransport` es de httpx, y sólo `callWebhook` usa httpx. **`WebBaseLoader` usa `requests`**
> (verificado por introspección: 0 ocurrencias de httpx) y MCP usa `langchain_mcp_adapters`. En esos
> dos el pinning **no es inyectable** sin reemplazar el fetch — ver Fase 4.

Diseño de referencia original (superado por lo de arriba, se deja por contexto):

**Reutilizar, no reescribir:** las validaciones estáticas ya existen y están bien hechas en
`validate_webhook_url` (`scenario_webhook_security.py:84`). El módulo de seguridad de webhooks nació
en el commit `d39f7da2` ("Security and validations improvements"); ese trabajo hay que **extenderlo**.

```
async def validate_and_resolve_url(url, *, allowed_ports=frozenset({80, 443}))
    -> tuple[bool, str | None, list[str]]
```

1. Correr las validaciones estáticas existentes (esquema, userinfo, hosts bloqueados, puertos).
2. **Resolver DNS** y chequear **cada** registro A/AAAA con `_is_blocked_ip_address`. Si **alguno**
   está bloqueado → rechazar (no "si todos"; uno solo alcanza para el ataque).
3. Devolver las IPs validadas para poder pinnearlas.

**Pinning de IP (imprescindible, no opcional).** Sin esto queda abierto el DNS rebinding: se resuelve
a una IP buena, se valida, y entre la validación y la petición el atacante cambia el registro. Se
implementa con un transport/resolver propio de httpx que fuerce la conexión a la IP ya validada,
preservando el header `Host`.

**Tests que tienen que existir:**
- hostname que resuelve a IP privada → rechazado (el gap central)
- IP literal privada → rechazado (regresión, ya funciona)
- puerto fuera de la allowlist → rechazado
- esquema no http/https → rechazado
- userinfo en la URL → rechazado
- **rebinding**: resolver devuelve IP buena en la 1ª llamada y privada en la 2ª → la conexión tiene
  que ir a la IP validada, no a la nueva
- hostname público normal → **aceptado** (que no quede un guard que bloquea todo)

### Fase 2 — `follow_redirects` por default

`src/core/http_client.py:34` → cambiar el default a `follow_redirects=False` y que lo active
explícitamente quien lo necesite.

**Cuidado: esto es un cambio de comportamiento global.** Antes de tocarlo hay que listar quién usa
`http_client.client()` y ver quién depende de seguir redirecciones. Si son muchos, la alternativa
menos invasiva es dejar el default y pasar `follow_redirects=False` **explícitamente** en los sinks
de este plan. Decidir con el `grep` en la mano, no antes.

### Fase 3 — Aplicar a `callWebhook` (el más contenido)

Ya tiene validador y allowlist de puertos: es sólo cambiar la llamada por el guard nuevo y pinnear.
Es el mejor lugar para estrenar el guard porque el radio de daño si algo sale mal es el más chico.

### Fase 4 — Aplicar al scrape (el que más cambia)

**Primero unificar los dos sinks en una función, después meter el guard adentro.** Decidido el
2026-08-05. `scrape.py:23` y `onboarding.py:118` son el mismo bug escrito dos veces: el
`header_template` es **idéntico byte a byte** (mismo User-Agent de Chrome 114) y el patrón es el mismo
par de líneas. Lo único que difiere es el uso del resultado:

- `onboarding` usa sólo `documents[0]` → acumula `page_content` / `title` / `description` para el LLM
- `scrape` usa todos los `documents` → los splitea → FAQs + embeddings

O sea que el pedazo común es exactamente "dame los documents de esta URL":

```python
async def fetch_url_documents(url: str, *, context: str) -> list[Document]
```

Adentro: guard → `WebBaseLoader` con el `header_template` (deja de estar duplicado) → redirects
apagados → `.load()`.

**Por qué unificar y no poner el guard dos veces:** hoy hay que acordarse de ponerlo en dos lados, y
el que se olvide deja el agujero abierto igual. Con el embudo, cualquier scrape futuro pasa por el
guard sin que su autor sepa que existe. Mismo argumento que `_build_connection` en MCP.

**Dos diferencias de comportamiento a resolver al unificar:**

1. **Manejo de errores.** `onboarding` hace `except Exception: continue` (se come el fallo y sigue con
   la próxima URL); `scrape` no tiene try por URL, tiene uno global que aborta el lote entero.
   **Decidido: devolver lista vacía y loguear** — preserva la semántica de onboarding, y en scrape
   saltear las vacías es además una mejora sobre perder todo el lote por una URL mala.
2. `onboarding` accede a `documents[0]` sin chequear que la lista tenga algo; hoy queda tapado por el
   `except Exception`. Al unificar, chequearlo explícito.

**Y además:**

3. **Validar de forma síncrona en el handler, antes de encolar.** `documents.py:72` usa
   `background_tasks.add_task`, así que la respuesta HTTP ya salió cuando el guard rechace — el
   usuario no vería el error. Validar en el handler **y** dejar el guard adentro como red de
   seguridad.
4. **Apagar redirecciones.** `WebBaseLoader` acepta `requests_kwargs`, que se pasa a `session.get` →
   `allow_redirects=False`. Sin esto se valida `evil.com` y el 302 lleva al destino interno.
5. **`Step0Data.validate_websites`**: mover la validación al guard o endurecerla. Ojo que es un
   `field_validator` → rechazar ahí devuelve `422`, hay que ver que el front lo muestre entendible. Y
   **arreglar el bug de case-sensitivity** (rechaza 4 URLs legítimas de prod con scheme en mayúsculas).

**Sobre el pinning acá:** `WebBaseLoader` usa `requests`, así que `PinnedIPTransport` no aplica. Tres
caminos, en orden de costo:

| Opción | Costo | ¿Cierra rebinding? |
|---|---|---|
| **1.** Guard (DNS) + `allow_redirects=False` | bajo | no, riesgo residual |
| **2.** `HTTPAdapter` pinneado montado en la `session` de WebBaseLoader | medio | sí |
| **3.** Reemplazar el fetch por httpx propio y pasar el HTML al splitter | alto, cambia el scraping | sí |

**Recomendación: opción 1 ahora, pinning del scrape como ticket aparte.** El gap de DNS es el riesgo
real y con canal de lectura; el rebinding exige además controlar un dominio y ganar una carrera de
milisegundos, mientras que hoy alcanza con un registro A.

### Fase 5 — Aplicar a MCP discovery

**El guard va en `MCPDiscoveryService._build_connection`** (`discovery_service.py:40`), no en el
router ni en el schema: es el embudo por donde pasan `list_tools` **y** `call_tool`, así que cubre los
3 puntos de entrada de una (create, refresh, y ejecutar tool). Dos de esos leen la URL **de la DB**,
así que validar sólo el payload del create no alcanza.

Allowlist 80/443 tal cual — la data de prod dice que ningún servidor MCP usa `ws://` ni puertos raros.

Pendiente aparte del guard: **revisar las filas ya guardadas** en `mcp_servers` (al 2026-08-05: 97,
todas limpias).

### Fase 6 — Rollout observe-only → enforce

**No endurecer de golpe.** Este guard puede empezar a rechazar URLs que hoy funcionan, y no sabemos
qué escrapean los clientes. Aplicar el molde que sirvió en el #1691:

1. Desplegar **logueando los rechazos sin bloquear**, con un log distinguible
   (p. ej. `[ssrf_guard] would_reject url=... reason=...`).
2. Dejarlo correr y **mirar los logs de prod** para ver qué aparece.
3. Recién entonces cambiar a enforce.

Con base `development`, el orden es: **dev primero** (ahí cae el merge del PR), y recién después de
promover a `main`, observar en prod — que es donde están los 1 126 URLs reales, así que el observe-only
de prod es el que de verdad informa la decisión de enforce.

```bash
# dev
KUBECONFIG=./config-k3s kubectl get pods -n aspe-dev | grep main-service
KUBECONFIG=./config-k3s kubectl logs -n aspe-dev <pod> --since=24h | grep "ssrf_guard"

# prod (después de promover)
KUBECONFIG=./config-k3s kubectl get pods -n aspe | grep main-service
KUBECONFIG=./config-k3s kubectl logs -n aspe <pod> --since=24h | grep "ssrf_guard"
```

**Ojo con la retención de logs:** el pod de prod retiene poco (el 2026-08-04 eran ~1.5 h de ventana).
Para una observación real hay que mirar varias veces a lo largo del día, o mandar la señal a algún
lado que persista.

**El flag es `SSRF_GUARD_ENFORCE`** (default `False` = observe-only). Se prende por env var; el
usuario las maneja desde `k3s-dashboard.aspe.ai`.

---

## Verificación

**Local:**
```bash
cd ~/academiadeia/main_service
.venv/bin/python -m pytest src/tests/helpers/test_outbound_url_guard.py -v
.venv/bin/python -m ruff check src/ && .venv/bin/python -m ruff format --check src/
```

> Notas del entorno (verificadas el 2026-08-04): **no existe `python` en el PATH**, usar
> `.venv/bin/python`. Y hay **6 errores de colección preexistentes** en `src/tests/routers/` por
> `langchain_anthropic` ausente del venv local (no está en `requirements.txt`) — **no son de este
> cambio**, ignorarlos. La convención de tests vigente es `src/tests/` (310 archivos), no `tests/`
> (24).

**En dev, prueba empírica del sink más directo** (con un token de dev):
```bash
# Debe quedar registrado como would_reject en observe-only, y dar error en enforce
curl -s -X POST "$DEV/onboarding/discover" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"organization_id":"<org>","websites":["http://10.43.135.179/"]}'
```

**Regresión que no se puede romper:** un scrape de un sitio público normal tiene que seguir
funcionando, y un `callWebhook` a un endpoint público también. Si el guard bloquea todo, el fix es
inútil y nadie lo va a notar hasta que un cliente se queje.

---

## Fuera de alcance (tickets aparte)

1. **NetworkPolicy de egress** que niegue el tráfico del pod hacia `169.254.0.0/16` y hacia la CIDR
   de servicios (`10.43.0.0/16`). Es infra, no este repo, y **no reemplaza** el guard de aplicación —
   pero es lo único que protege a **los demás pods** del mismo problema.
   **Tiene que ser una allowlist, no un deny plano:** `main_service` necesita alcanzar `pgpool`
   (10.43.174.128:5432), `redis` (10.43.233.112:6379) y `hasura-prod` (10.43.135.179:80)
   legítimamente. Un deny de `10.43.0.0/16` tira la app.
2. **SSRF de segundo orden en Shopify**: `shopify_client.py:210` hace
   `client.stream("GET", url)` con una URL que viene de la respuesta de la Bulk Operation API. El
   `shop_domain` lo pone el comerciante, así que una tienda maliciosa podría devolver una URL interna.
   Riesgo bajo, pero es el mismo patrón.

---

## Estado pendiente del trabajo del 2026-08-04 (issue #1697, `presigned_url`)

Esto **no** es parte de este plan, pero quedó abierto y conviene no perderlo:

| Pendiente | Acción |
|---|---|
| Sync PR [main_service#2104](https://github.com/aspe-ai/main_service/pull/2104) (`main`→`development`) | mergear — **merge commit, no squash**. Estaba CLEAN con los 3 checks en verde. |
| Sync PR [next#871](https://github.com/aspe-ai/next/pull/871) (`main`→`development`) | mergear — **merge commit, no squash** |
| `aspe-mobile`: commit `90890c3` en rama local `fix/presigned-url-auth-1697` | **sin pushear** — la cuenta del entorno no tiene permiso de escritura en `aspe-ai/aspe-mobile` (`ERROR: Write access to repository not granted`). Hay que dar acceso o pushear a mano. |
| `presigned-url-sin-auth.md` actualizado en local | **sin commitear** en el repo `kubernetes` |

El fix en sí **ya está en prod y verificado**: sin token da `401` (antes `200`), y una subida real de
usuario dio `200` en 694 ms sin el warning `no file_size declared`, lo que confirma que el front nuevo
manda `file_size`.

**Dos cosas ajenas encontradas de paso, por si se quieren tickets:**
- `POST /wa/{bot_id}/media_message` tarda **7.5–16.3 s** en subir una imagen desde el chat. Es la
  causa real del "se queda colgado" que se reportó; es preexistente y de otro flujo.
- `GET /integrations/org/...` devuelve **401 cada 2-5 min** para las mismas 5 organizaciones, de
  forma constante. Algo poleando sin token válido.

---

## Orden de ejecución (resumen)

```
0. Confirmar las 3 decisiones                                            ✅ hecho 2026-08-05
1. Fase 0  — corregir el doc del hallazgo                                ✅ hecho (sin commitear)
2. Fase 1  — guard compartido + tests                                    ✅ hecho (27 tests, sin push)
3. Fase 2  — decidir follow_redirects con el grep en la mano             ← siguiente
4. Fase 3  — callWebhook (radio de daño chico, estrena el guard)
5. Fase 4  — scrape: unificar los 2 sinks en fetch_url_documents,
             validación síncrona en el handler, redirecciones off
6. Fase 5  — MCP en _build_connection (cubre los 3 puntos de entrada)
7. crear el PR (base development) y PARAR — el merge es del usuario
8. Fase 6  — observe-only en dev, mirar logs; promover a main; enforce
9. NetworkPolicy de egress → ticket de infra aparte
```

> **Regla:** el trabajo termina al crear el PR. No mergear nada, incluidos los sync PRs que este doc
> lista como pendientes más arriba.
