# Webhook de Stripe: secreto elegido por el atacante + sin idempotencia por `event.id` (SEC-PAY-01 / SEC-PAY-02)

| | |
|---|---|
| **ClickUp** | https://app.clickup.com/t/9013463087/86ajfwqfc |
| **GitHub** | https://github.com/aspe-ai/main_service/issues/1694 |
| **Severidad** | Alta — confirmada adversarialmente. **SEC-PAY-01 escala: ver "El secreto de test está en dev"** |
| **Estado** | **Arreglado en código, en review** — PR [#2095](https://github.com/aspe-ai/main_service/pull/2095) (2026-08-04). Pendiente: SQL en prod + **rotación del secreto de test** |
| **Repo afectado** | `main_service` — `src/services/stripe_service.py` |

> **Actualización 2026-08-04 (investigación de confirmación).** Diagnóstico confirmado; tres
> cosas cambiaron respecto de lo escrito abajo:
>
> 1. **La pregunta de negocio quedó respondida con datos.** En la cuenta Stripe
>    `acct_1J8T8BLQVySUT2DE` (Agentiza), test mode tiene 7 endpoints y el único habilitado hacia
>    nosotros es `https://main-dev.aspe.ai/stripe_webhooks`. **No existe ningún endpoint de test
>    apuntando a prod** → se quitó la rama de test, no hizo falta el "probar-las-dos-llaves".
> 2. **SEC-PAY-02 no es "suma créditos otra vez" en todos los caminos.** Compra manual /
>    autorecharge (`invoiceitem`) y el delta de `subscription_update` son **aditivos** → doble
>    crédito real. La renovación normal (`subscription_cycle`) escribe **absoluto**: no suma,
>    pero **resetea al quota completo**, devolviendo créditos ya consumidos. Y el
>    `subscription_update` con delta **no estaba guardado** — `claim_upgrade` sólo cubre la ruta
>    `metadata.upgrade=true`.
> 3. **El disparador orgánico tiene nombre:** `send_otp_to_customer` corre *después* del grant y
>    convertía cualquier fallo en `HTTPException(400)` → non-2xx → reintento de Stripe sobre un
>    webhook que ya otorgó créditos. El voice fee y los add-ons sí estaban en try/except; el OTP
>    era el único hueco vivo. Ya va envuelto.
>
> **Daño histórico: no encontrado.** 17 pares sospechosos en `credit_purchases`; los 3 más
> pegados (31s, 46s, 61s) corresponden cada uno a **dos facturas pagadas distintas** en Stripe
> live — compras repetidas legítimas. El defecto era latente.
>
> **Nota de implementación:** se implementó **claim-and-release** (`INSERT ... ON CONFLICT DO
> NOTHING RETURNING`, con `DELETE` del claim si el handler falla), no "todo en una transacción"
> como propone la sección de abajo: el handler mezcla Postgres con Firebase, la API de Stripe y
> emails, y no hay transacción que abarque eso. Retención: el volumen es de cientos de
> eventos/mes, así que el `DELETE` de 90 días quedó documentado en el `.sql` en vez de montar un
> CronJob.
>
> **Ojo:** `src/tests/conftest.py` dejaba `STRIPE_WEBHOOK_SECRET` y `STRIPE_WEBHOOK_SECRET_TEST`
> con **el mismo valor**, así que ningún test podía haber detectado SEC-PAY-01.

---

## El problema en una frase

El webhook de Stripe **sí** verifica firmas (buena base), pero dos fallas de lógica lo perforan:
**(1)** el secreto con el que se verifica la firma se elige leyendo `livemode` del **body sin
verificar**, así que quien manda la petición decide contra qué llave se comprueba su falsificación; y
**(2)** no hay idempotencia por `event.id`, así que un `invoice.payment_succeeded` firmado que se
reentrega otorga créditos duplicados — pérdida financiera directa.

---

# SEC-PAY-01 — El secreto de verificación lo elige el body sin verificar

## El código, hoy

`src/services/stripe_service.py:75-81`:

```python
payload, sig_header = await extract_payload_and_headers(request)
dev = query_extractor(payload)      # :49-53 → json.loads del body y lee livemode. SIN VERIFICAR.
secret = get_stripe_secret(dev)     # :92-96 → STRIPE_WEBHOOK_SECRET_TEST si dev, si no el live
event = verify_webhook_signature(payload, sig_header, secret)   # verifica con la llave que eligió el body

stripe.api_key = select_stripe_api_key(event.livemode)   # ✅ este sí usa el livemode YA verificado
```

La línea 81 es la ironía del hallazgo: el patrón correcto —derivar `livemode` del `event` ya
verificado— **ya está ahí, una línea más abajo**. Sólo no se aplicó a la decisión que importa.

El ataque es directo: mandas un `invoice.payment_succeeded` con `"livemode": false` y forzás que se
verifique contra el secreto de **test**. Si conocés ese secreto, firmás la falsificación, `construct_event`
la acepta, y los handlers de abajo otorgan créditos o mutan la suscripción de la organización que vos
nombraste en el payload.

Ambos secretos son campos **obligatorios** en settings (`settings.py:173-177`, los dos con `Field(...)`),
así que la rama de test no es un residuo de dev: está viva en producción y no arranca sin ella.

## El secreto de test está en dev — y eso cambia la severidad

El ticket dejó abierto *"¿el secreto de test es distinto del live en prod, y se maneja con el mismo
cuidado / se ha compartido a configs de dev?"*. Lo verifiqué contra el cluster. Huellas SHA de los
primeros 8 chars, sin exponer valores:

| Secreto | Namespace | Prefijo | Huella |
|---|---|---|---|
| `STRIPE_WEBHOOK_SECRET` | `aspe` (prod) | `whsec_9F…` | `bc71290f` |
| `STRIPE_WEBHOOK_SECRET_TEST` | `aspe` (prod) | `whsec_i5…` | **`3901844b`** |
| `STRIPE_WEBHOOK_SECRET` | `aspe-dev` | `whsec_i5…` | **`3901844b`** |
| `STRIPE_WEBHOOK_SECRET_TEST` | `aspe-dev` | `whsec_i5…` | **`3901844b`** |

Las tres últimas son **el mismo secreto**. O sea: la llave con la que producción acepta webhooks
"de test" es exactamente la que está sentada en el secret de dev, y en dev es además el secreto
*principal*.

Esto convierte el hallazgo de *"explotable si el secreto de test se conoce"* a **"la precondición ya
está cumplida"**: cualquiera con acceso al namespace de dev, o cualquier filtración de esa config,
tiene hoy una llave capaz de firmar eventos que **prod acepta**, con sólo poner `"livemode": false`.
No hay que adivinar nada.

**Consecuencia para el plan: la rotación de `STRIPE_WEBHOOK_SECRET_TEST` no es opcional, es parte del
fix.** Arreglar sólo el código deja la llave compartida circulando.

De paso: `STRIPE_API_KEY` en prod hoy es `sk_live_…` (huella `d47508ae`), distinta de
`STRIPE_API_KEY_TEST` (`sk_test_…`). Esto ya no es la situación histórica de la cuenta Agentiza en
`sk_test`, así que la nota vieja sobre eso quedó obsoleta.

## El arreglo

La decisión de fondo es una pregunta de negocio: **¿producción necesita aceptar webhooks de test?**

- **Si no** (lo más probable) → **quitar la rama de test de prod**. Verificar siempre contra
  `STRIPE_WEBHOOK_SECRET`, borrar `query_extractor` del camino de verificación y usar
  `event.livemode` verificado para todo lo demás. Elimina la superficie de raíz.
- **Si sí** → **probar las dos llaves de forma independiente**: verificar con la live; si tira
  `SignatureVerificationError`, reintentar con la de test. Dos intentos por request, costo
  despreciable. Lo esencial es que el body deje de **elegir** la llave.

En cualquiera de los dos casos: `query_extractor` no debe volver a decidir con qué secreto se
verifica, y el secreto de test se rota.

---

# SEC-PAY-02 — Sin idempotencia por `event.id`: se pueden duplicar créditos

## Qué existe y qué no

No existe **ninguna** tabla ni caché de eventos procesados. Verificado:

```
grep -rn "processed_stripe_events|stripe_events|processed_events" src/   →  0 resultados
```

El único guard es por autorecharge, en `handle_invoice_payment_succeeded`
(`src/services/stripe_service.py:612-646`): busca `AutorechargeRequestsRepository.get_by_invoice_id(invoice_id)`,
y si está `completed` sale temprano; si está `processing` lo marca `completed` **antes** de sumar
créditos, para que un reintento no vuelva a sumar. Bien pensado — pero **sólo cubre autorecharge**.

Para **compras manuales y renovaciones de suscripción** ese lookup devuelve `None` (no existe fila de
autorecharge), la ejecución cae de largo, y llega sin condición al grant en `:739`:

```python
result, total_credits = await update_or_insert_organization_credits(...)
```

Una reentrega firmada del mismo `invoice.payment_succeeded` vuelve a correr todo el handler y suma
los créditos otra vez.

## Por qué esto pasa solo, sin atacante

Es lo que hace al hallazgo caro: **no requiere malicia**. Stripe reintenta ante cualquier respuesta
no-2xx, y el grant de créditos ocurre **antes** de pasos posteriores que pueden fallar (débito de fee
de voz, envío de mail). Entonces: grant OK → falla un paso de abajo → 500 → Stripe reintenta →
**segundo grant**. Se duplica de forma orgánica, en el curso normal de operación.

Nota operativa relacionada: los caminos de débito escriben el saldo de forma **absoluta**, no
relativa. Por eso los ajustes manuales de saldo se pisan si la org está activa — y por eso un doble
grant no se "corrige solo": queda cristalizado en el saldo.

## Lo que sí se arregló por otro lado (no acreditar a este ticket)

Desde el diagnóstico de junio aparecieron dos guards nuevos en el camino de **upgrade de plan**
(`_apply_plan_upgrade`), que probablemente salieron del épico de suscripciones #1812:

- `:1042-1047` — skip idempotente si el `price_id` del upgrade ya está aplicado en la org.
- `:1073-1090` — check-and-set atómico vía `SubscriptionsRepository.claim_upgrade`: una reentrega
  concurrente pierde el claim y sale sin aplicar el delta.

Eso cierra el doble-cobro **en upgrades**, y el patrón de claim atómico es un buen molde a imitar.
Pero **no toca el caso central**: compra manual y renovación siguen sin dedup por `event.id`.

## El arreglo

Tabla `processed_stripe_events` con **índice único en `event.id`**, e `INSERT` al inicio de
`handle_stripe_webhook`, **en la misma transacción** que el grant de créditos:

1. `INSERT` del `event.id` al entrar. Si choca contra el índice único → ya se procesó → cortar y
   devolver 200 (que Stripe no reintente).
2. Aplica a **todos** los tipos de evento, no sólo invoices. El `event.id` es único global en Stripe
   y está hecho justo para esto — es el patrón que Stripe recomienda.
3. Transaccional con la escritura de créditos: si un paso posterior falla, el reintento no puede
   duplicar porque el registro del evento y el grant viven o mueren juntos.
4. Retención: los eventos son inmutables; ~90 días alcanza, con un borrado periódico de lo más viejo.

**Por qué `event.id` y no `invoice_id`:** un mismo invoice dispara varios tipos de evento, así que
deduplicar por invoice es menos preciso y colisiona con eventos legítimamente distintos.

### Trampa de despliegue: la migración es manual

`main_service` **no tiene runner de migraciones automático**. El SQL de la tabla + índice único hay
que correrlo **a mano en la base antes de desplegar** el código que la usa, o el handler revienta en
el primer webhook. La carpeta `migrations/` es scratch y está en `.gitignore`, así que el SQL hay que
dejarlo donde quede registro. Y toda escritura/DDL en prod se pide y se confirma antes de correrla.

---

## Preguntas abiertas del issue, ya respondidas

| Pregunta | Respuesta (verificada 2026-08-03) |
|---|---|
| ¿`STRIPE_WEBHOOK_SECRET_TEST` es distinto del live en prod? | **Sí**, distinto del live (`3901844b` vs `bc71290f`). |
| ¿Se ha compartido a configs de dev? ¿Hay que rotarlo? | **Sí, está en dev hoy** — es el mismo valor que los DOS secretos de `aspe-dev`. **Rotación obligatoria** como parte del fix. |
| ¿Prod necesita aceptar webhooks de test? | **No** (verificado 2026-08-04 contra la API de Stripe): en test mode no hay ningún endpoint apuntando a prod, sólo `main-dev.aspe.ai`. → se quitó la rama de test. |
| ¿Política de limpieza de la tabla de eventos? | ~90 días de retención con borrado periódico. |

---

## Orden sugerido de trabajo

1. **Rotar `STRIPE_WEBHOOK_SECRET_TEST`** y dejar de compartirlo con dev. Es independiente del código
   y reduce el riesgo de inmediato.
2. Definir si prod acepta test-mode → implementar SEC-PAY-01 (quitar rama, o probar-las-dos).
3. SEC-PAY-02: SQL de la tabla a mano en la base, después el código del guard.

## Referencia cruzada

- [`hooks-hasura-sin-autenticacion.md`](hooks-hasura-sin-autenticacion.md) — el otro pendiente de la
  misma auditoría (issue #1692).
- Issue [#1691](https://github.com/aspe-ai/main_service/issues/1691) (firmas de Meta) **sí está
  resuelto** (25-29 jun 2026) y sirve de molde: helper compartido `src/helpers/meta_signature.py` y
  rollout observe-only → enforce.
- Épico de suscripciones #1812 — de ahí salieron los guards de `_apply_plan_upgrade`; conviene
  coordinar para no chocar en `stripe_service.py`.
