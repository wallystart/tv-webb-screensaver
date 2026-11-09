# 🔭 tv-webb-screensaver

Convierte el protector de pantalla de una **Android TV** en una ventana al universo: la **Foto astronómica del día de la NASA (APOD)** más imágenes de los telescopios **James Webb** y **Hubble**, que se actualizan solas todos los días.

En lugar de las mismas fotos de fotógrafos de siempre (el *Backdrop* de Google), la tele muestra:

- **APOD de la NASA**: la foto de hoy pasa **seguido**, con su explicación traducida y resumida en un panel al costado que nunca tapa la imagen.
- **ESA/Webb** (*Picture of the Month*) y **ESA/Hubble** (*Picture of the Week*), más nebulosas, galaxias y estrellas: una selección **al azar que cambia cada día**.

Además:


- **Sin deformar:** cada imagen se encaja en la resolución exacta de la tele y los bordes se rellenan con la misma imagen desenfocada.
- **Con leyenda:** título, fecha, constelación y distancia, en español o inglés.
- **Solo fotos reales:** se descartan gráficos, espectros y versiones con brújula o etiquetas.

```
┌──────────────────────┐    cron diario    ┌───────────────────┐
│ NASA APOD · ESA/Webb │ ───────────────▶  │  Servidor casero  │
│ ESA/Hubble           │   webb_sync.py    │  /srv/webb (pool) │
└──────────────────────┘                   │  (share Samba)    │
                                           └─────────┬─────────┘
                                                     │ SMB
                                           ┌─────────▼─────────┐
                                           │  Android TV       │
                                           │  Aerial Views     │
                                           │  (screensaver)    │
                                           └───────────────────┘
```

Probado con una **Skyworth con Android TV** y una mini PC con Ubuntu como servidor.

---

## Qué necesitás

- Una **Android TV / Google TV** (Skyworth, Sony, TCL, Chromecast, Nvidia Shield, etc.)
- Un **servidor Linux** en la misma red que esté prendido siempre (mini PC, Raspberry Pi, NAS…)
- La app **[Aerial Views](https://github.com/theothernt/AerialViews)** (gratis, open source, en Play Store)
- `adb` en el servidor (o en cualquier compu de la red)

## Estructura

```
scripts/
├── webb_sync.py       # baja APOD/Webb/Hubble, filtra, encaja, agrega textos y arma el pool del día
├── setup-samba.sh      # crea la carpeta compartida para la tele
└── tv-screensaver.sh   # conecta por adb y cambia el protector de pantalla
```

---

## Instalación

### 1. Encontrar la IP de la tele

Desde el servidor:

```bash
ip a                              # para ver el rango de tu red, ej. 192.168.1.0/24
sudo nmap -sn 192.168.1.0/24
```

Buscá el fabricante de la tele en la lista. Ojo: **Skyworth aparece como `Shenzhen Chuangwei-RGB Electronics`** (Chuangwei es su nombre chino).

También la ves en la tele en **Configuración → Red e Internet**.

> 💡 Reservale una **IP fija** a la tele en el router (DHCP reservation). Si cambia de IP, se rompe la conexión por adb.

### 2. Habilitar adb en la tele

1. **Configuración → Preferencias del dispositivo → Información** → apretá 7 veces sobre **Compilación**.
2. **Preferencias del dispositivo → Opciones para desarrolladores** → activá **Depuración USB** (en muchas teles eso también habilita adb por red en el puerto 5555).
3. En el servidor:

```bash
sudo apt install -y adb
export TV=192.168.1.65            # tu IP
./scripts/tv-screensaver.sh connect
```

Aparece un aviso en la tele: tildá **"Permitir siempre desde esta computadora"** y aceptá. `adb devices` tiene que mostrar `device`, no `unauthorized`.

### 3. Crear la carpeta compartida

```bash
./scripts/setup-samba.sh
```

Te pide una contraseña de Samba: es la que vas a cargar en la tele. Por defecto comparte `/srv/webb` como `webb`, con tu usuario.

### 4. Bajar y preparar las imágenes

```bash
sudo apt install -y python3-pil fonts-dejavu-core
adb shell wm size                       # resolución real de la tele, ej. 1920x1080
export WEBB_SIZE=1920x1080              # poné la que devolvió
./scripts/webb_sync.py
```

La primera vez tarda: lee la página de cada imagen y las procesa todas. Después solo trabaja con las nuevas.

#### Cómo se arma lo que ve la tele (el *pool* del día)

La carpeta compartida **no tiene todo**. En cada corrida se rearma con:

| Qué | Cuántas | Variable |
|---|---|---|
| APOD más reciente, **repetida** para que pase seguido | 8 copias | `POOL_APOD_WEIGHT` |
| APOD de los días anteriores | 6 | `POOL_APOD_RECENT` |
| Webb + Hubble **al azar** (cambia cada día) | 40 | `POOL_RANDOM` |

Con los valores por defecto, más o menos **1 de cada 7 imágenes es la APOD de hoy**. Las copias son *hardlinks*, así que no ocupan espacio extra. La biblioteca completa queda en `~/.cache/webb/rendered`.

> En Aerial Views activá el **orden aleatorio (shuffle)**: si no, las copias de la APOD salen todas seguidas.

#### Configuración

Todo se ajusta con variables de entorno (o con el flag equivalente, ver `--help`):

| Variable | Valores | Default |
|---|---|---|
| `WEBB_SOURCES` | cualquier combinación de `webb,hubble,apod` | las tres |
| `WEBB_DEST` | carpeta que lee la tele | `/srv/webb` |
| `WEBB_CACHE` | originales, biblioteca y estado | `~/.cache/webb` |
| `WEBB_SIZE` | resolución de la tele | `1920x1080` |
| `WEBB_LANG` | `es` / `en` | `es` |
| `WEBB_CAPTION` | `1` con textos / `0` imagen sola | `1` |
| `WEBB_TRANSLATE` | `none` / `claude` / `argos` | `none` |
| `WEBB_FONT` | ruta a un `.ttf` | DejaVu Sans |
| `APOD_API_KEY` | key gratis de [api.nasa.gov](https://api.nasa.gov) | `DEMO_KEY` |
| `APOD_DAYS` | cuántos días de APOD traer en cada corrida | `14` |
| `APOD_TEXT` | `summary` / `full` / `none` | `summary` |

`DEMO_KEY` alcanza para una corrida por día, pero conviene pedir una key propia (gratis, llega al instante por mail).

#### Textos, idioma y traducción

- **Webb y Hubble** llevan una leyenda corta abajo: título, fecha, constelación, distancia y crédito.
- **APOD** lleva un **panel a la derecha** con título, fecha, crédito y la explicación. La imagen va entera a la izquierda: el texto nunca la tapa. El tamaño de letra se ajusta solo para que entre.
- Fechas y distancias salen en el idioma elegido sin depender de nada externo. Los títulos y la explicación vienen en inglés; para traducirlos:
  - `WEBB_TRANSLATE=claude` + `ANTHROPIC_API_KEY=...` (opcional `CLAUDE_MODEL`): traduce y además **resume la explicación de la APOD a 2 o 3 oraciones**, que es lo que se lee cómodo desde el sillón. Cada texto se procesa una sola vez y queda guardado; cuesta centavos por mes. **Recomendado.**
  - `WEBB_TRANSLATE=argos`: offline con [Argos Translate](https://github.com/argosopentech/argos-translate) (`pip install argostranslate` + paquete en→es). Traduce pero no resume, así que el texto de la APOD queda largo.
  - Sin traducción, la APOD muestra la explicación completa en inglés con letra más chica.

Si cambiás idioma, resolución o textos, el script **re-renderiza todo desde el caché** sin volver a descargar.

#### Ver qué detecta de una imagen

```bash
./scripts/webb_sync.py --debug potm2610a     # metadatos de Webb y si se acepta o descarta
```

#### Automático, todos los días

La APOD sale una vez por día, así que el cron es **diario** (8:31 de la mañana):

```bash
crontab -l 2>/dev/null | grep -v webb | crontab -
(crontab -l 2>/dev/null; echo "31 8 * * * WEBB_SIZE=1920x1080 WEBB_TRANSLATE=claude ANTHROPIC_API_KEY=sk-... APOD_API_KEY=... $PWD/scripts/webb_sync.py >/dev/null 2>&1") | crontab -
```

El historial queda en `/srv/webb/.sync.log`.

### 5. Configurar Aerial Views en la tele

1. Instalá **Aerial Views** desde Play Store.
2. En la app, desactivá los videos de Apple y demás proveedores.
3. Activá la fuente **Samba / SMB** con:
   - Host: la IP del servidor (ej. `192.168.1.72`)
   - Share: `webb`
   - Usuario y contraseña de Samba
4. Asegurate de que incluya **fotos**, no solo videos, y activá el **orden aleatorio**.
5. Si hay una opción de escala de fotos, elegí **ajustar / fit** (no recortar ni estirar): las imágenes ya vienen del tamaño justo.

### 6. Ponerlo como protector de pantalla

Android TV esconde la opción de elegir otro protector, así que se hace por adb:

```bash
./scripts/tv-screensaver.sh status    # 👈 anotá lo que devuelve (es tu original)
./scripts/tv-screensaver.sh aerial
./scripts/tv-screensaver.sh test      # lo arranca ya, para probar
```

Si aparece una nebulosa: listo 🌌

---

## Volver al protector original

```bash
./scripts/tv-screensaver.sh restore
```

El valor original **varía según la tele**. En la Skyworth es `com.google.android.backdrop/.Backdrop`; en otras es `com.google.android.apps.tv.dreamx/.service.Backdrop`. Usá el que te devolvió `status` antes de cambiar nada:

```bash
BACKDROP=<lo-que-anotaste> ./scripts/tv-screensaver.sh restore
```

---

## Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| `Connection refused` | adb no está escuchando. Activá **Depuración USB** y reiniciá la tele. Si existe **Depuración inalámbrica**, usá `adb pair` con el código que muestra la tele y después `adb tcpip 5555`. |
| `failed to authenticate` / `unauthorized` | Falta aceptar el aviso en la tele. Apretá Home (que no esté en el protector) y reconectá. |
| El aviso de autorización **no aparece** | En la tele: *Revocar autorizaciones de depuración USB* + apagar/prender Depuración USB. En el servidor: `adb kill-server && rm -f ~/.android/adbkey*` y reconectá. Si sigue, desenchufá la tele 10 s. |
| Se pierde la autorización | No quedó tildado "Permitir siempre", o corriste `sudo adb` (root usa otra clave). Usá siempre adb sin sudo. |
| Imágenes deformadas | `WEBB_SIZE` no coincide con la tele: revisá `adb shell wm size`. También revisá la escala de fotos en Aerial Views. |
| La APOD casi no aparece | Activá el shuffle en Aerial Views o subí `POOL_APOD_WEIGHT`. |
| Una fuente no trae nada | Mirá la salida o el `.sync.log`: si una página de categoría cambió de URL, se ajusta en `ESO_SITES`. |
| Se coló una imagen con texto | Corré `--debug <id>` para ver su tipo y título. Si conviene, sumá la palabra a `TEXT_HINTS` en `webb_sync.py`. |
| Pantalla negra en Aerial Views | El soporte SMB es quisquilloso con las rutas: usá un share simple, sin subcarpetas, con usuario y contraseña. |

---

## Cómo funciona `webb_sync.py`

1. **APOD:** pide a `api.nasa.gov/planetary/apod` los últimos `APOD_DAYS` días. Descarta los videos y las imágenes de menos de 1000 px.
2. **Webb y Hubble:** las dos usan la misma plataforma de ESA. Lee *Picture of the Month* / *Picture of the Week* y algunas categorías (se cambian en `ESO_SITES`), y de cada imagen nueva lee título, **Type**, fecha, constelación y distancia. Solo se quedan las de tipo `Observation` cuyo título no sugiera texto encima (*annotated*, *compass*, *spectrum*, *illustration*…).
3. Baja el original (Webb/Hubble: `wallpaper_uhd` en 4K o `large`; APOD: `hdurl`) a `~/.cache/webb/raw`.
4. Genera la versión para la tele en `~/.cache/webb/rendered`: imagen entera sin deformar, fondo desenfocado, y la leyenda o el panel de texto.
5. Rearma la carpeta de la tele con el pool del día. Los archivos se escriben de forma atómica, así que la tele nunca ve uno a medias.

## Próximo paso: vida artificial en la tele 🧬

Un protector de pantalla de Android es solo un `DreamService`. La idea siguiente es una app mínima en Kotlin con un `WebView` que apunte a una simulación servida desde el servidor (ej. `http://gilbert.local:8080`), y que alterne con Webb según la hora: universo de día, puntos de vida artificial de noche.

---

## Créditos

- Imágenes: **ESA/Webb** y **ESA/Hubble** ([condiciones de uso](https://esawebb.org/copyright/)), y **NASA APOD** (cada foto tiene su autor y muchas tienen copyright propio; el crédito se muestra en el panel). Este proyecto es para uso personal en casa: antes de redistribuirlas, revisá los derechos de cada una.
- Protector de pantalla: [Aerial Views](https://github.com/theothernt/AerialViews) de Neil Turner.

## Licencia

Los scripts de este repo están bajo licencia [MIT](LICENSE).
