# VioletStem — Plan Fase 3: persistencia y preparación para despliegue
**Estado: PLAN, no implementado. Requiere aprobación. VioletStem NO está listo para producción.**

Fuentes de datos: código inspeccionado (Fases 0–2), mediciones locales
(`scripts/measure_perf.py`, `scripts/eval_separation.py`), precios web
verificados el 2026-10-11 (Hetzner, Hugging Face, Railway, Render, RunPod).
Todo precio/cifra ajena a nuestras mediciones se marca VERIFICADO o ESTIMADO.

## 1. Arquitectura actual (verificada)
- **Monolito en un proceso**: FastAPI (`backend/app.py`: 7 endpoints + health/engine)
  sirve API y frontend estático. Sin workers separados, sin DB, sin colas.
- **Jobs en memoria**: `job_manager._jobs: dict` + `ThreadPoolExecutor(max_workers=2)`.
  Sobre el límite → 503 inmediato (fail-fast, sin cola de espera).
- **Motor**: Demucs `htdemucs` CPU primario (torch 2.5.1+cpu, demucs 4.0.1, pesos 80 MB
  en `%USERPROFILE%\.cache\torch\hub\checkpoints\`); fallback DSP solo si falta torch.
- **Archivos**: `STORAGE_DIR/jobs/<id>/` (upload.mp3, decoded.wav, vocals.wav,
  instrumental.wav, stems.zip). Limpieza por hilo cada 300 s, retención 3600 s.
- **Seguridad existente**: triple validación MP3, IDs 32 chars no predecibles,
  rate-limit/IP en memoria, descargas solo si `completed`, sin cuentas.
- **Tests**: 15/15. **Medido en este PC** (Ryzen 7 8845HS, 32 GB): 12 s estéreo →
  ~10 s separación (×0.84 realtime CPU), working set ~1.2 GB; torch ocupa 1.13 GB
  en disco. Job peor caso (10 min): ~435 MB temporales.
- **Gaps conocidos**: Dockerfile sin Demucs; reinicio pierde el registro (404s +
  huérfanos); timeout 600 s justo para el límite de 600 s de audio.

## 2. Riesgos antes de publicar (ordenados)
- **R1 — Amnesia en reinicio**: registro en memoria; archivos huérfanos no reclamados.
- **R2 — Sin HTTPS/Auth en app**: TLS debe terminar en proxy; rate-limit en memoria
  no funciona entre réplicas y la IP real tras proxy no se distingue (X-Forwarded-For
  se ignora a propósito hoy).
- **R3 — Abuso económico/técnico**: cada canción ≈ 2.5 min CPU; anónimo + gratis =
  incentivo a saturar. Sin cuentas hay que endurecer límites o aceptar el riesgo.
- **R4 — Ráfagas → 503 sin cola**: correcto técnicamente, pobre UX pública.
- **R5 —_timeout/duración al límite**: 10 min × 0.84 = 8.4 min + decode/cola ≈ timeout 600 s.
  En VPS compartido (más lento) fallará antes.
- **R6 — Build Docker sin ML** + pesos descargados en runtime (arranque lento / sin red = fallo).
- **R7 — Egress**: en PaaS se paga por GB (audio+ZIP por descarga); con retención 1 h
  y re-descargas, el coste sale del cómputo.
- **R8 — Legal/operativo**: términos y privacidad existen pero sin proceso de
  reclamaciones (takedown), sin logs centralizados ni alertas. Sin backup (aceptable:
  todo es temporal).

## 3. Propuesta de persistencia (SQLite, sin cambios de contrato API)
- **Nuevo `backend/store.py`**: `jobs.db` en modo WAL junto a `STORAGE_DIR`.
  Tabla `jobs(id PK, original_name, stem_base, status, stage, message, engine,
  created_at, updated_at, duration, vocals_size, instrumental_size,
  vocals_duration, instrumental_duration, error)`. **Solo rutas relativas**
  `jobs/<id>/…`; **jamás audio en la DB**. `sqlite3` stdlib + lock (sin nuevas deps).
- **Máquina de estados**: `queued → processing → completed | failed | cancelled`.
  Al arrancar: todo `processing` → **`interrupted`** con mensaje "El servidor se
  reinició durante el proceso. Vuelve a intentarlo." **Nunca se sirve un
  interrupted como completado** (las descargas exigen `completed` + archivos
  presentes y tamaño > 0).
- **Reintento = job nuevo** (propuesto; simple y auditable; ver decisión D2).
- **Rehidratación**: `completed/failed/cancelled` se recuperan solo si sus archivos
  verifican (existen, >0 bytes, duraciones compatibles); si no → `failed` honesto.
- **Huérfanos**: sweep al arrancar — dirs sin fila → `quarantine/` + log, borrado tras
  gracia configurable; filas sin dir → `failed`.
- **Limpieza segura**: solo estados finales + expirados; jamás `processing`;
  "en uso" = `processing` o dentro de retención (las descargas son lecturas cortas;
  si una descarga vive más que la retención, el siguiente GET devuelve 410 ya
  implementado — no se interrumpe un stream abierto porque el fichero se borra,
  no se trunca: en POSIX el fd sobrevive; en Windows documentar reinicio de descarga).
- **Cambios concretos**: crear `store.py`; `job_manager.py` delega registro en store
  (misma API interna); `app.py` sin cambios de endpoints/códigos; migración one-shot
  con log (importa completados verificables, resto a `interrupted`/purga).
- **Tests nuevos**: round-trip store, recovery con DB prefabricada (processing →
  interrupted no servido), quarantine, retención con DB, 15/15 existentes intactos.
- Descartado: Postgres/Redis (infra no aprobada, sobredimensión).

## 4. Comparación de alojamiento (VERIFICADO salvo marca)
| Opción | Specs | Coste | Demucs viable | Contras |
|---|---|---|---|---|
| Este PC / local | 8c/16t, 32 GB | €0 | Sí (medido) | No público |
| **Hetzner CX33** | 4 vCPU, 8 GB, 80 GB, 20 TB tráfico | **€8.49/mes + €0.50 IPv4, sin IVA** (VERIFICADO, tarifas jun-2026) | Sí: 2 jobs × 1.2 GB + SO caben en 8 GB | Sysadmin propio; stock limitado según terceros; sin GPU |
| Hetzner CX43 | 8 vCPU, 16 GB, 160 GB | **€15.99/mes + IPv4** (VERIFICADO) | Sí, margen para 3–4 concurrentes (medir antes) | Idem |
| **HF Spaces CPU Upgrade** | 8 vCPU, 32 GB, 50 GB disco | **$0.03/h ≈ $22/mes always-on** (VERIFICADO) | Sí (mejor RAM que CX33) | Sleep/cold-start si se configura ahorro; disco 50 GB; egress; requiere plan de pago para compute |
| HF Spaces T4-small | 4 vCPU, 15 GB + T4 16 GB | **$0.40/h ≈ $290/mes always-on** (VERIFICADO) | Sí + rápido | Solo viable con sleep agresivo o por picos |
| Railway Hobby | hasta 48 GB/servicio, vol. 5 GB | $5 base + **$10/GB RAM/mes** (VERIFICADO); **~$50–60/mes p. 4 GB always-on (ESTIMADO)** | Sí en Hobby (Free/Trial 0.5–1 GB: NO) | Coste impredecible; egress $0.05/GB; volumen 5 GB justo |
| Render 2c-8g | 2 CPU, 8 GB | **$135/mes** (VERIFICADO) | Sí pero caro | Starter/Standard (≤2 GB: NO alcanzan); Pro 2c-4g $85/mes al límite |
| RunPod GPU | RTX 4000 Ada **$0.18–0.28/h** (VERIFICADO oct-2026) | Solo picos | Fase GPU, no piloto | Complejidad (pod + storage + red) |
**Recomendación del plan**: piloto en **Hetzner CX33 (~€9/mes)** con Docker+HTTPS,
o HF Spaces CPU Upgrade si se prefiere cero sysadmin. GPU solo si la demanda lo exige.

## 5. Estimación de recursos (medida, con límites declarados)
Base medida: ×0.84 realtime CPU, ~1.2 GB pico, ~435 MB disco/job peor caso.
| Audio | Tiempo CPU (ESTIMADO) | Disco pico/job | Notas |
|---|---|---|---|
| 3 min | ~2.5 min | ~150 MB | Caso típico |
| 10 min (límite) | ~8.4 min + decode | ~435 MB | Roza timeout 600 s → proponer MAX_DURATION 420 s o timeout mayor (D3) |
| 2 concurrentes | Suma lineal (pool=2) | ~870 MB | CX33 8 GB: OK con margen |
Límites de la estimación: medida en Ryzen 8845HS (VPS compartido ×1.5–2 más lento,
ESTIMADO); pico RAM muestreado antes/después (el pico real instantáneo puede ser
mayor); sin medir con 3+ concurrentes ni con `htdemucs_ft`.

## 6. Secuencia de implementación y pruebas (tras aprobación)
1. **P1 store + recovery** (sin tocar endpoints): unit tests store, recovery simulado,
   quarantine; gate: 15/15 + nuevos en verde.
2. **P2 límites coherentes**: MAX_DURATION/timeout/concurrencia según D3; test de
   timeout simulado; doc.
3. **P3 Docker ML**: stage con `requirements-ml.txt`, pesos pre-descargados en build
   (opcional, ver D6); test de build + arranque offline.
4. **P4 staging local prod-like** (`.env` producción, 2 concurrentes, canciones 3 min):
   carga ligera, reinicio a mitad de job → `interrupted` verificado, retención real.
5. **P5 piloto** (solo con D4 aprobada): despliegue, checklist go-live (HTTPS, 404/503
   reales, ZIP), monitor 1 semana, informe. Sin declarar "producción" antes.

## 7. Decisiones que necesitan tu aprobación
- **D1**: ¿SQLite según §3 (recomendado) o mantener memoria + purga manual?
- **D2**: Reintento tras `interrupted`: ¿job nuevo (recomendado) o reencolar mismo ID?
- **D3**: Límites: ¿MAX_DURATION 600→420 s, timeout 600 s, concurrentes 2, retención 1 h?
- **D4**: Piloto: ¿Hetzner CX33 (recomendado) o HF Spaces CPU Upgrade?
- **D5**: ¿Cuentas de usuario? Recomendación: **no para el piloto** (fricción vs abuso);
  alternativa: límites duros (30 req/min, 50 MB, 1 h retención, 503 visible) + revisión
  de abuso a 30 días.
- **D6**: ¿Docker con ML (+~1.2 GB imagen, recomendado) o imagen slim + fallback DSP?
- **D7**: HTTPS/dominio: ¿Caddy con ACME auto (recomendado) o túnel Cloudflare?
- **D8**: ¿GPU (RunPod) ahora o solo si el piloto CPU se satura?
