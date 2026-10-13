# VioletStem — Operación de prueba privada con tester (requiere autorización)

> **ACCESO EXTERNO: DESACTIVADO por defecto. No inicies ningún túnel sin
> autorización expresa. No configures ningún inicio automático de túneles.**

## 1. Iniciar VioletStem (local, siempre igual)
```powershell
Set-Location C:\Users\ACER\Documents\WebSite2
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```
Verificar (sin credenciales, en local con auth off):
- `http://127.0.0.1:8000/api/health` → `status: ok`, `jobs_active: 0`.
- `http://127.0.0.1:8000/api/engine` → `demucs-htdemucs`, `kind: neural`,
  `device: cuda` (en este PC). Si dice `cpu` con mención de fallback, la GPU
  no está disponible: funciona igual, más lento.

## 2. Activar autenticación (OBLIGATORIO antes de cualquier acceso externo)
```powershell
python -c "import hashlib,secrets; s=secrets.token_bytes(16); print('pbkdf2$600000$'+s.hex()+'$'+hashlib.pbkdf2_hmac('sha256',b'CLAVE-LARGA-ALEATORIA-AQUI',s,600000).hex())"
```
- La contraseña debe ser larga, aleatoria y exclusiva del tester (generador del
  sistema o `secrets.token_urlsafe(24)`); no reutilices claves ni uses ficticias.
- Copia de seguridad: `Copy-Item .env $env:TEMP\violetstem-env.bak` (fuera del
  proyecto; `.env.bak*`/`*.bak` están en `.gitignore`). Nunca sobrescribas sin copia.
- En `.env`: `AUTH_ENABLED=true`, `AUTH_USER=tester`, `AUTH_PASS_SHA256=<verifier>`.
- Reinicia el servidor. Comprueba desde otra ventana:
  sin credenciales → 401 en `/` y `/api/health`; con credenciales → 200.
- El verificador es PBKDF2-HMAC-SHA256 (lento de romper si se filtra).
  Intentos erróneos limitados por IP (5/5 min → 429); la clave correcta
  siempre funciona y resetea el contador.
- **Nunca compartas la clave por el mismo canal que el enlace.**

## 3. Iniciar el túnel — SOLO tras autorización expresa
Opción recomendada (sin cuentas, tester no instala nada):
```powershell
C:\Users\ACER\bin\cloudflared.exe tunnel --url http://127.0.0.1:8000 --allowed-mail tester@su-correo.com
```
- Binario verificado el 2026-10-12: `cloudflared 2026.10.0` oficial de
  github.com/cloudflare (portable, sin instalador; flag `--allowed-mail`
  presente en su ayuda). Sin firma desacoplada publicada: integridad =
  origen TLS oficial + versión. No sustituir por copias de terceros.
- Anota la URL `https://...trycloudflare.com` que imprime y **compártela solo
  con el tester** (por canal distinto al de la clave).
- El tester abre la URL, introduce su correo, recibe PIN de un solo uso,
  y después el usuario/clave de VioletStem (doble capa).
- Alternativa robusta (requiere cuenta Cloudflare + dominio propio, pendiente):
  túnel administrado + Cloudflare Access con regla de correo del tester,
  configurando Access ANTES de crear la ruta pública. Ver `docs/plan-fase3.md`.

## 4. Revocar el acceso inmediatamente
1. `Ctrl+C` en la ventana de `cloudflared` (la URL muere al instante; verifica
   abriendo la URL: debe fallar la conexión).
2. Después, con el túnel ya detenido: `AUTH_ENABLED=false` + reinicio.
   (Orden: primero cerrar exposición externa, después relajar auth.)
3. Para reanudar otro día se genera una URL nueva (las Quick cambian en cada arranque).

> Stopping the tunnel does NOT delete files the tester already downloaded.
> Retention (1 h) only deletes server-side copies.

## 5. Detener VioletStem de forma segura
`Ctrl+C` en su ventana (o `Stop-Process -Name python` verificando que es el
servidor). Los jobs en curso se pierden; los completados recientes siguen en
disco pero sus IDs dan 404 tras reiniciar (registro en memoria) — el tester
debe re-subir. Limpieza automática por retención (1 h) sigue vigente.

## 8. Limpieza de credenciales de prueba y temporales
- Las credenciales de TEST (solo entorno aislado) viven únicamente en archivos
  de `Temp\opencode\` y variables del proceso que los usa: bórralos al terminar
  (`Remove-Item Temp\opencode\auth_*.py`) y detén ese servidor.
- La credencial REAL del tester solo existe en `.env` (gitignored) y en el canal
  por el que se compartió: al terminar la prueba, rota o elimina la línea
  `AUTH_PASS_SHA256`, reinicia, y borra el mensaje donde se envió.

## 6. Si el PC se apaga / pierde Internet / se reinicia
- El túnel muere con la conexión: el tester verá errores de conexión.
- Al volver: arranca VioletStem (§1), verifica (§1), activa auth si se perdió
  (está en `.env`, persiste), y SOLO con autorización arranca un túnel nuevo
  (§3, URL nueva) y avisa al tester. Nunca reutilices una URL vieja.
- Comprueba que ningún job antiguo se reporte como completado: tras reinicio
  todo ID previo → 404 "Invalid or expired" (verificado en tests).

## 7. Prohibido en esta faseTúneles sin `--allowed-mail` ni auth, abrir puertos del router, reglas de
firewall para exponer, inicio automático de túneles, publicar la URL, compartir
clave y enlace por el mismo canal, desactivar auth "para probar".

## 9. Checklist de sesion supervisada (SOLO tras autorizacion expresa)
1. Local: 401 sin creds en / y /api/health; 200 con creds.
2. Arrancar: cloudflared tunnel --url http://127.0.0.1:8000 --allowed-mail <correo-tester>.
3. URL por un canal + contrasena VioletStem por OTRO canal.
4. Tester: abre URL, introduce su correo y el PIN recibido.
5. El navegador pide usuario/clave de VioletStem (segunda capa).
6. Sube MP3, separa en CUDA, descarga WAVs y ZIP; confirma duraciones.
7. Negativo: sin clave (401) y con clave mala (401/429) no entra ni descarga.
8. Cierre: Ctrl+C al tunel y comprobar que la URL ya no conecta; despues AUTH_ENABLED=false + reinicio si se desea.
Sesion corta y supervisada; sin promesa de disponibilidad si el PC se suspende o pierde red.
