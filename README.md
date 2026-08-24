# Servidor de hackathon en LAN

Plataforma para hackathons en red local: los equipos entregan su código desde
un navegador, cada entrega se evalúa dentro de un contenedor Docker aislado, y
hay cola y leaderboard. El acceso se controla por dirección MAC y el
administrador ve en todo momento qué dispositivos están conectados.

**Sin dependencias externas**: solo Python 3.11+ y Docker. Nada de `pip`, nada
de internet en el momento del evento.

```bash
python3 run_server.py
```

```
  Equipos: http://192.168.10.180:8000
  Admin:   http://192.168.10.180:8000/admin
```

---

## Índice

- [Cómo funciona](#cómo-funciona)
- [Puesta en marcha](#puesta-en-marcha)
- [Uso: participantes](#uso-participantes)
- [Uso: administrador](#uso-administrador)
- [Crear un problema nuevo](#crear-un-problema-nuevo)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Limitaciones y seguridad](#limitaciones-y-seguridad)
- [Resolución de problemas](#resolución-de-problemas)

---

## Cómo funciona

```
Dispositivo del equipo                 Servidor                        Docker
─────────────────────                  ────────                        ──────
  navegador ─── HTTP ──────▶  ¿MAC en la whitelist?
                              └── no ─▶ 403 + aviso al admin
                              └── sí ─▶ equipo identificado
  sube solution.py ────────▶  validación (AST + requirements)
  pulsa «Ejecutar» ────────▶  cola persistente (FIFO, N en paralelo)
                                        │
                                        ├─▶ docker build  (imagen del problema
                                        │                  + deps del equipo)
                                        └─▶ docker run --network none
                                                --memory --cpus --pids-limit
                                                datasets montados read-only
                                        ◀── JSON con el resultado
                              score del plugin ─▶ leaderboard público
```

En paralelo, un monitor barre la LAN cada 20 segundos, detecta qué
dispositivos de la whitelist están presentes y registra cada conexión y
desconexión en un historial que solo ve el administrador.

---

## Puesta en marcha

### 1. Requisitos

| | |
|---|---|
| Python | 3.11 o superior (usa `tomllib`, de la librería estándar) |
| Docker | El usuario que arranca el servidor debe poder ejecutar `docker` |
| Red | El servidor debe estar en el **mismo segmento L2** que los participantes |

Comprueba Docker antes del evento:

```bash
docker run --rm hello-world
```

### 2. Configurar `config.toml`

Lo mínimo imprescindible:

```toml
[admin]
password = "pon-una-contraseña-de-verdad"   # ← cámbiala

[network]
subnet = "192.168.10.0/24"    # "auto" suele acertar, pero fijarla es más seguro
```

### 3. Dar de alta equipos y dispositivos

Dos vías equivalentes:

- **`devices.toml`** — se lee al arrancar, cómodo para preparar todo antes.
- **Panel de admin** — en caliente, durante el evento.

```toml
[[admins]]
mac = "00:11:22:33:44:55"
label = "Portátil del organizador"

[[teams]]
name = "Equipo Alpha"
devices = [
  { mac = "aa:bb:cc:11:22:33", label = "Portátil de Ana" },
  { mac = "aa:bb:cc:44:55:66", label = "Portátil de Beto" },
]
```

> **Aviso importante sobre las MAC aleatorias.** Los móviles y los portátiles
> recientes generan una MAC distinta para cada red Wi-Fi. Los participantes
> deben desactivar esa opción para la red del evento (Android: *Ajustes → Wi-Fi
> → red → Privacidad → Usar MAC del dispositivo*; iOS: *Dirección Wi-Fi
> privada → Desactivada*; Windows 11: *Wi-Fi → Direcciones de hardware
> aleatorias*), o bien registrar la MAC aleatoria concreta que su dispositivo
> muestre en esa red.

### 4. Colocar los datasets

El problema de huellas espera los datasets donde estaban originalmente, es
decir **fuera** del repositorio:

```
Activos/
├── fingers/
│   ├── k/     ← dataset público
│   ├── k4/    ← validación parcial (muestra aleatoria por run)
│   └── k5/    ← validación final
└── bot_simula/   ← este proyecto
```

Si faltan, el panel de admin lo avisa en rojo en la página de Problemas. La
ruta se cambia en `problems/fingerprint/manifest.toml`, sección `[datasets]`.

### 5. Arrancar

```bash
python3 run_server.py
```

El servidor imprime las URL de acceso. Repártelas y listo.

---

## Uso: participantes

Todo pasa por el navegador; no hay nada que instalar.

| Página | Para qué |
|---|---|
| `/` | Problemas activos y estado de cada uno |
| `/p/<problema>` | Enunciado, entrega de archivos, botón de ejecutar, límites |
| `/run/<id>` | Resultado detallado de una evaluación |
| `/runs` | Historial de evaluaciones del equipo |
| `/leaderboard` | Ranking de todos los problemas, con score numérico visible |

Flujo: subir los archivos → **Entregar** (se validan al momento) → **Ejecutar
evaluación**. Como antes, **hay que entregar de nuevo antes de cada ejecución**,
y existe un tiempo de espera entre ejecuciones configurable por problema.

La página se actualiza sola cuando cambia el estado del run (cola → build →
ejecución → resultado).

---

## Uso: administrador

Entra en `/admin` con la contraseña de `config.toml`. Con
`require_admin_mac = true` (por defecto) además hace falta que la MAC del
dispositivo esté marcada como admin.

| Página | Para qué |
|---|---|
| `/admin` | Resumen, abrir/cerrar entregas, estado de red y Docker, últimos avisos |
| `/admin/devices` | Quién está conectado ahora, alta y baja de MAC, MAC desconocidas detectadas |
| `/admin/events` | **Historial completo de conexiones y desconexiones**, filtrable por equipo y tipo |
| `/admin/teams` | Crear y borrar equipos |
| `/admin/problems` | Activar/desactivar y abrir/cerrar cada problema; recargar plugins sin reiniciar |
| `/admin/queue` | Cola en curso, cancelar runs encolados, resetear esperas |

Las notificaciones de conexión y desconexión llegan en vivo al panel (y como
notificación del navegador si le das permiso al hacer clic una vez). **Toda la
información de red es exclusiva del administrador**: ninguna página de equipo
la expone.

Un dispositivo que no esté en la whitelist recibe un 403 que le muestra su
propia MAC, y aparece automáticamente en *Dispositivos → MAC desconocidas*
para darlo de alta con un clic.

---

## Crear un problema nuevo

```bash
cp -r problems/_plantilla problems/mi_problema
```

Edita `manifest.toml` (el `slug` debe coincidir con el nombre de la carpeta) y
recarga desde *Admin → Problemas → Recargar problemas*. **No se toca nada de
`app/`.**

Cada problema es una carpeta autocontenida:

| Archivo | Dónde corre | Qué hace |
|---|---|---|
| `manifest.toml` | servidor | Metadatos, límites, datasets, imagen Docker, contrato de entrega, parámetros |
| `evaluator.py` | **contenedor** | `evaluate(ctx) -> dict`: ejecuta el código del equipo y mide |
| `scoring.py` | servidor | `score()`, `summary()`, `detail_blocks()`: puntuación y presentación |
| `template/` | — | Archivos de ejemplo descargables por los participantes |
| otros `.py` | **contenedor** | Código propio del problema (p. ej. `matchers.py`) |
| `Dockerfile` | — | Opcional; si no está, se genera desde `[docker]` |

`problems/_plantilla/README.md` documenta el contrato completo, y
`problems/_plantilla/` es un ejemplo funcional que no necesita datasets.

---

## Estructura del proyecto

```
bot_simula/
├── config.toml            configuración del evento
├── devices.toml           precarga de equipos y MAC (opcional)
├── run_server.py          arranque
├── app/
│   ├── config.py          carga de config.toml
│   ├── db.py              SQLite: equipos, dispositivos, eventos, runs
│   ├── problems.py        descubrimiento y carga de plugins
│   ├── validator.py       validación de entregas según el manifest
│   ├── runner.py          build y ejecución del contenedor
│   ├── harness.py         entrypoint DENTRO del contenedor
│   ├── queue.py           cola persistente y workers
│   ├── auth.py            identificación por MAC y sesión de admin
│   ├── events.py          bus de eventos para SSE
│   ├── network/           arp.py · scanner.py · monitor.py
│   └── web/               http.py · html.py · routes_team.py · routes_admin.py · server.py
├── problems/
│   ├── fingerprint/       el reto de huellas
│   └── _plantilla/        plantilla y ejemplo (las carpetas con «_» se ignoran)
└── data/                  se crea sola: base de datos y entregas
```

---

## Limitaciones y seguridad

**Una dirección MAC se falsifica en segundos.** El control por MAC es una
comodidad dentro de una red controlada, no una medida de seguridad. Por eso el
panel de administración exige además contraseña, y la sesión de admin queda
ligada por firma HMAC a la MAC con la que se inició: robar la cookie no sirve
desde otro dispositivo.

Aislamiento de las entregas — cada evaluación corre con:

- `--network none`: sin red de ningún tipo dentro del contenedor
- `--memory`, `--cpus`, `--pids-limit` según el manifest
- `--security-opt no-new-privileges`
- datasets montados en modo solo lectura
- timeout con aborto forzado

No es una sandbox a prueba de un atacante decidido (Docker no es una frontera
de seguridad fuerte), pero sí impide fugas de datos por red, agotamiento de
recursos y escrituras en los datasets.

Otros límites conocidos:

- **Aislamiento de clientes en el AP**: si está activado, el servidor no puede
  resolver ni descubrir a los demás dispositivos. Compruébalo antes del evento.
- **Retardo en la detección**: una desconexión tarda `scan_interval ×
  miss_threshold` (por defecto ~60 s) en confirmarse. Es deliberado: sin esa
  histéresis, el ahorro de energía del Wi-Fi generaría desconexiones falsas.
  Una conexión, en cambio, se detecta al instante en cuanto el dispositivo hace
  cualquier petición web.
- **HTTP sin TLS**: pensado para una LAN cerrada.
- Con `arp-scan` instalado y el servidor como root, el barrido es más rápido y
  fiable; si no, se usa un barrido por ping que no necesita privilegios.

---

## Resolución de problemas

**«No se pudo determinar la dirección MAC de tu dispositivo»**
El cliente no está en el mismo segmento L2, o el AP tiene aislamiento de
clientes. Comprueba con `ip neigh show` en el servidor si aparece su IP.

**El monitor de red sale como «detenido» en el panel**
Revisa `[network]` en `config.toml`: `enabled = true` y una `subnet` válida. Si
`subnet = "auto"` no acierta (por ejemplo con VPN activa), fíjala a mano.

**Las evaluaciones fallan con «Falló la construcción de la imagen Docker»**
Casi siempre es un paquete de `requirements.txt` del equipo. El log completo del
build está en la página del run, visible para el administrador.

**El primer build tarda mucho**
Descarga la imagen base y los paquetes. Constrúyela una vez antes del evento:

```bash
docker pull python:3.11-slim
```

Después, las capas quedan cacheadas y cada build es cuestión de segundos.

**Quiero empezar de cero**
Para el servidor y borra `data/`. Se pierden equipos, dispositivos, historial y
resultados; `devices.toml` los vuelve a precargar al arrancar.
