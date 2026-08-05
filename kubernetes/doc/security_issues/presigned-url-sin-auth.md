# `/generate_presigned_url` sin autenticación y sin límites de key/content-type/tamaño (SEC-WEB-05 / SEC-SSRF-02)

| | |
|---|---|
| **ClickUp** | https://app.clickup.com/t/9013463087/86ajfwqex → *(tarea "Authenticate /generate_presigned_url" en la lista SCOPING)* |
| **GitHub** | https://github.com/aspe-ai/main_service/issues/1697 |
| **Severidad** | Alta — confirmada adversarialmente |
| **Estado** | **Fix en review** (2026-08-04) — [main_service#2103](https://github.com/aspe-ai/main_service/pull/2103), [next#870](https://github.com/aspe-ai/next/pull/870), móvil en rama local |
| **Repo afectado** | `main_service` (+ `next` y `aspe-mobile` como clientes) |

---

## El problema en una frase

`POST /generate_presigned_url` lo puede llamar **cualquiera desde internet, sin credencial**, y
devuelve una URL de PUT a S3 válida por una hora donde el que llama elige el **prefijo del key**
(`bot_id`) y el **`Content-Type`**, sin tope de tamaño. Eso da escrituras cruzadas entre tenants,
contenido del atacante servido desde el dominio de confianza `media.aspe.ai`, y abuso de
almacenamiento sin límite.

---

## El código, hoy

`src/routers/presigned_upload_files.py` completo — **cero `Depends(...)`**:

```python
@router.post("/generate_presigned_url", ...)
async def generate_presigned_url_endpoint(file_info: FileInfo):   # FileInfo = {bot_id, file_type}
    presigned_url, public_url = await generate_presigned_url(file_info.bot_id, file_info.file_type)
    return {"presigned_url": presigned_url, "public_url": public_url}
```

Registrado en `src/main.py:261` sin `dependencies=[]`, y no hay middleware global de auth. El `bot_id`
sale del **body**, o sea de quien ataca.

En `src/helpers/upload_files.py:36-61`:

- `file_path = f"{bot_id}/{file_name}"` (`:45-49`) — el prefijo lo pone el cliente. Sin validar que
  sea UUID, así que tampoco hay chequeo de forma.
- `"ContentType": file_type` (`:56`) — pasa crudo a S3.
- **No hay `ContentLengthRange`** en ninguna parte: `grep -rn ContentLengthRange src/` → 0
  resultados. Nada acota el tamaño del PUT.
- `public_url = f"https://media.aspe.ai/{file_path}"` (`:60`) — dominio de confianza.
- `MIME_TO_EXTENSION` (`:19-33`) incluye `image/svg+xml`, `application/json` y `text/plain`, y hace
  `.get(file_type, "")`: **cualquier MIME arbitrario pasa**, sólo se queda sin extensión.

Un mitigante real que conviene no perder de vista: el nombre del archivo es un `uuid4()` generado en
el servidor, así que **no se puede sobrescribir un objeto existente** de forma dirigida. El ataque es
escribir *nuevos* objetos bajo el prefijo de otro, no pisar los de otro.

## El patrón correcto ya existe en el repo

`src/modules/aws/routers/file_conversion.py` tiene un endpoint de presigned que **sí** usa
`Depends(has_access_to_bot)` y allowlist de tipos. O sea no hay que diseñar nada nuevo: el endpoint
viejo nunca se aseguró ni se deprecó cuando llegó el módulo nuevo. Es una brecha de día uno, no una
regresión.

## Cuidado: el helper también se usa desde adentro

`generate_presigned_url()` se llama server-side desde al menos tres lugares:

- `src/helpers/wa_session_manager.py:1886`
- `src/modules/whatsapp/helpers/wa_media_storage.py:46`
- `src/modules/messaging_core/helpers/media_uploader.py:186`

Esos flujos ya vienen con el `bot_id` resuelto internamente y **no** pasan por HTTP. Igual que en el
caso de `/hooks/*`: **el guard va en el router, no en el helper**, o se rompe el manejo de media de
WhatsApp.

---

## La postura del bucket: parcialmente respondida

La pregunta abierta que dejó bloqueado el ticket (`agent:needs-feedback`) era cómo se sirve
`media.aspe.ai` y si los objetos son de lectura pública, porque de eso depende cuán grave es la parte
de stored-XSS. Lo que pude verificar:

```
dig media.aspe.ai  →  d3pffjeuv6mnqo.cloudfront.net  (13.33.82.x)
curl -I https://media.aspe.ai/  →  HTTP/2 403, server: AmazonS3, x-amz-bucket-region: us-east-1
```

**Confirmado:** es **CloudFront delante de S3** (`aspe-files-bucket`, `us-east-1`), no un website
bucket directo.

**Sin resolver:** si los objetos son legibles públicamente. Sondear un key inexistente devuelve
`403 AccessDenied`, que **no discrimina** — S3 responde `AccessDenied` en lugar de `NoSuchKey` cuando
el principal no tiene `s3:ListBucket`, y eso pasa igual en las dos posturas.

Para cerrarlo hace falta **una URL de un objeto real** (sacar cualquier `media.aspe.ai/...` de la
base o de un chat) y un `curl -I` contra ella: si devuelve 200 con el `content-type` que se subió, la
lectura es pública y la escalada a stored-XSS aplica tal como está descrita.

Dicho eso, **el flujo del producto implica fuertemente lectura pública**: el `public_url` se le
entrega al cliente para usarlo como media en conversaciones, y WhatsApp/Meta necesitan poder
descargar ese archivo desde afuera. Trabajaría asumiendo que es público hasta comprobar lo contrario.

**Lo que no depende de esa respuesta:** la escritura cruzada entre tenants, el abuso de
almacenamiento/costo, y la falta de auth. El PUT se firma con las credenciales IAM de la app, así que
funciona sin importar la ACL de lectura. Sólo la parte de XSS almacenado está condicionada.

---

## El arreglo

1. **Auth + propiedad del bot.** Dos correcciones a lo que decía este doc antes:
   - `Depends(has_access_to_bot)` **no sirve tal cual**: recibe `bot_id` como parámetro de FastAPI y
     acá viene en el **body**, así que FastAPI lo buscaría como query param. Se usa
     `Depends(get_current_user)` + `await UserRepository.verify_user_bot_access(user_id, bot_id)`.
   - **No se puede "derivar el `bot_id` del principal"**: un usuario puede tener varios bots. Se
     **valida** el del body contra los accesos del principal.

   `verify_user_bot_access` ya cubre el rol `SUPERADMIN` **de cliente** (es miembro de la org). El que
   quedaría afuera es el **staff interno de Aspe** — ver nota abajo.
2. **Allowlist de `Content-Type`.** Reutilizar `SUPPORTED_MEDIA_TYPES` de `src/constants/wa_values.py`,
   que ya trae `mime_type` + `max_size` por extensión con los límites reales de WhatsApp, en vez de
   inventar una lista nueva. Rechazar `text/html`, `image/svg+xml`, `application/xhtml+xml`,
   `application/xml`, `text/xml`, `application/json`.
   Sobre el SVG: **verificado que no hay ninguno en uso** (`bot_media_library` → cero `.svg` y cero
   `.json`), así que bloquearlo no rompe nada.
3. **Tamaño: `ContentLengthRange` NO existe para presigned PUT.** Es una condición de *POST policy*
   (`generate_presigned_post`). Con una URL de PUT las opciones reales son (a) validar el tamaño
   **declarado** por el cliente, o (b) firmar un `ContentLength` **exacto** — que es enforcement de
   verdad pero rompe la subida si el cliente manda un largo distinto, con riesgo real en React Native.
   Se eligió (a) por ahora.
4. **Servir contenido de usuario con `Content-Disposition: attachment`**, `X-Content-Type-Options:
   nosniff` y una CSP restrictiva; lo ideal a futuro es un origen aparte, fuera del dominio de
   confianza de la app. **Ninguno de los tres está hoy** (verificado en la respuesta de un objeto real).

### La trampa del despliegue: resuelta

Los dos clientes legítimos (front y móvil) **ya mandan `Bearer`**, así que meter auth no rompe a
nadie por falta de token. Lo que sí hay que cuidar son tres detalles de compatibilidad que aparecieron
al implementar:

1. **`file_size` tiene que ser opcional.** Hay binarios del móvil ya publicados que no lo mandan; si
   se requiere, Pydantic devuelve `422`.
2. **La allowlist tiene que ser una unión, no un endurecimiento.** El front permite 5 tipos que
   `SUPPORTED_MEDIA_TYPES` no tiene (`image/gif`, `image/webp` como imagen y no sticker,
   `video/quicktime`, `video/x-msvideo`, `audio/wav`).
3. **`getMimeType()` del móvil devuelve `application/octet-stream`** para cualquier extensión que no
   conoce — y su `switch` **no incluye `.mp4`**. También produce `application/zip` y
   `application/vnd.rar`. Sin esos tres en la allowlist se rompen las subidas de documentos del móvil.
   Permitirlos no reabre el stored-XSS: ninguno se ejecuta en el navegador, se descargan. Pero sí
   significa que la allowlist de tipos es *blanda* — quien quiera evadirla declara `octet-stream`. El
   control duro es la auth; el complemento es `nosniff` (punto 4 del arreglo).

Como el endpoint está registrado con `include_in_schema=False`, no aparece en el OpenAPI — la lista de
clientes salió de `grep` en los repos de front/móvil, no de la documentación ni de los logs.

### Bug de cliente que apareció de paso

`aspe-mobile` hacía `const uploadBotId = bot_id || session_id` en las rutas de media de IG/Messenger
(`stores/chatStore.tsx`). Con el enforce, ese fallback da `403` porque un `session_id` nunca es un
`bot_id`. En la práctica las 3 pantallas que llaman pasan `selectedBot.id`, así que el fallback casi
nunca se usaba — pero había que sacarlo. Ahora cae en `get().currentChat?.bot_id`.

---

## Preguntas abiertas del issue

**Las tres quedaron respondidas el 2026-08-04.**

| Pregunta | Estado (2026-08-04) |
|---|---|
| ¿Cómo se sirve `media.aspe.ai`? | **Respondida:** CloudFront → S3 `aspe-files-bucket` en `us-east-1`. |
| ¿Los objetos son de lectura pública (gate del stored-XSS)? | **Respondida: sí.** Saqué una URL real de `bot_media_library` y `curl -I` → `200`, sirviendo el `content-type` almacenado. **Sin** `Content-Disposition`, **sin** `nosniff`, **sin** CSP. |
| ¿Qué clientes legítimos llaman al endpoint hoy? | **Respondida: dos, y ambos ya mandan `Bearer`.** `next/src/shared/utilities.js:244` (el token lo pone `src/hooks/useAuth.ts:40` como default global de axios) y `aspe-mobile/common/api/api.ts:249` (vía `axiosInstance.defaults`). Por eso ambos hacen `delete headers['Authorization']` antes del PUT a S3. |

Los logs de acceso **no** servían para cerrar la tercera: el pod de prod sólo retiene ~1.5 h y la
única llamada en la ventana era mi propia prueba. El código de los clientes fue la vía concluyente.

**Consecuencia para el rollout:** no hace falta el ciclo observe-only → enforce del #1691. Se puede
poner `enforce` directo.

---

## Orden sugerido de trabajo

1. Sacar de los logs de prod quién llama al endpoint (bloquea el rollout).
2. Confirmar lectura pública con un objeto real (define cuán agresivo va lo de content-type).
3. Auth + `bot_id` del principal + validación de UUID — el corazón del fix.
4. Allowlist de content-type + `ContentLengthRange`.
5. `Content-Disposition`/CSP en el serving, coordinado con infra (CloudFront).

## Referencia cruzada

- [`README.md`](README.md) — índice de la auditoría.
- [`hooks-hasura-sin-autenticacion.md`](hooks-hasura-sin-autenticacion.md) — mismo patrón exacto:
  identidad tomada del body y guard que debe ir en el router, no en el service/helper.
- [`ssrf-scrape-y-callwebhook.md`](ssrf-scrape-y-callwebhook.md) — el otro hallazgo con `SEC-SSRF-*`;
  ojo que **este no es SSRF real** a pesar de la etiqueta: no hay fetch de salida, es minteo de URLs
  de escritura. La numeración del audit los agrupó, el arreglo no tiene nada en común.
- Tracker maestro del audit: [#1700](https://github.com/aspe-ai/main_service/issues/1700).
