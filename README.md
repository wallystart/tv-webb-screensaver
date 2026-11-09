# 🔭 tv-webb-screensaver

Convierte el protector de pantalla de una **Android TV** en una ventana al universo: imágenes del **telescopio James Webb** que se actualizan solas cada semana.

En lugar de las mismas fotos de fotógrafos de siempre (el *Backdrop* de Google), la tele muestra nebulosas, galaxias y la *Picture of the Month* de ESA/Webb:

- **Sin deformar:** cada imagen se encaja en la resolución exacta de la tele y los bordes se rellenan con la misma imagen desenfocada.
- **Con leyenda:** título, fecha, constelación y distancia, en español o inglés.
- **Solo fotos reales:** se descartan gráficos, espectros y versiones con brújula o etiquetas.

```
┌──────────────────────┐   cron semanal    ┌───────────────────┐
│  esawebb.org (CDN)   │ ───────────────▶  │  Servidor casero  │
│  imágenes + datos    │   webb_sync.py    │  /srv/webb        │
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
├── webb_sync.py       # baja, filtra, encaja y agrega leyenda a las imágenes
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
adb shell wm size                       # resolución real de la tele, ej. 1920x1080 o 3840x2160
export WEBB_SIZE=3840x2160              # poné la que devolvió
./scripts/webb_sync.py --prune          # --prune borra las que quedaron descartadas
```

La primera vez tarda: lee la página de cada imagen y las procesa. Después solo trabaja con las nuevas.

#### Configuración

Todo se ajusta con variables de entorno (o con el flag equivalente, ver `--help`):

| Variable | Valores | Default |
|---|---|---|
| `WEBB_DEST` | carpeta que lee la tele | `/srv/webb` |
| `WEBB_CACHE` | originales y estado (fuera del share) | `~/.cache/webb` |
| `WEBB_SIZE` | resolución de la tele | `3840x2160` |
| `WEBB_LANG` | `es` / `en` | `es` |
| `WEBB_CAPTION` | `1` con leyenda / `0` sin leyenda | `1` |
| `WEBB_TRANSLATE` | `none` / `claude` / `argos` (traducción del título) | `none` |
| `WEBB_FONT` | ruta a un `.ttf` | DejaVu Sans |

La fecha y la distancia se escriben en el idioma elegido sin depender de nada externo. El **título** viene en inglés desde ESA; para traducirlo:

- `WEBB_TRANSLATE=claude` + `ANTHROPIC_API_KEY=...` (opcional `CLAUDE_MODEL`). Cuesta centavos y cada título se traduce una sola vez: queda guardado.
- `WEBB_TRANSLATE=argos`: offline, con [Argos Translate](https://github.com/argosopentech/argos-translate) (`pip install argostranslate` y el paquete en→es).

Si cambiás idioma, resolución o leyenda, el script **re-renderiza todo desde el caché** sin volver a descargar.

#### Ver qué detecta de una imagen

```bash
./scripts/webb_sync.py --debug potm2610a     # metadatos y si se acepta o descarta
./scripts/webb_sync.py --only potm2610a      # procesar solo esa
```

#### Automático, todos los lunes a las 9

```bash
(crontab -l 2>/dev/null; echo "0 9 * * 1 WEBB_SIZE=3840x2160 $PWD/scripts/webb_sync.py >/dev/null 2>&1") | crontab -
```

El historial queda en `/srv/webb/.sync.log`.

### 5. Configurar Aerial Views en la tele

1. Instalá **Aerial Views** desde Play Store.
2. En la app, desactivá los videos de Apple y demás proveedores.
3. Activá la fuente **Samba / SMB** con:
   - Host: la IP del servidor (ej. `192.168.1.72`)
   - Share: `webb`
   - Usuario y contraseña de Samba
4. Asegurate de que incluya **fotos**, no solo videos.
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
| Se coló una imagen con texto | Corré `--debug <id>` para ver su tipo y título. Si conviene, sumá la palabra a `TEXT_HINTS` en `webb_sync.py`. |
| Pantalla negra en Aerial Views | El soporte SMB es quisquilloso con las rutas: usá un share simple, sin subcarpetas, con usuario y contraseña. |

---

## Cómo funciona `webb_sync.py`

1. Lee las páginas de *Picture of the Month* y de algunas categorías (nebulosas, galaxias, estrellas, agujeros negros, primeras imágenes) y extrae los IDs, por ejemplo `potm2610a`. Las categorías se cambian en `PAGES`.
2. Para cada ID nuevo lee su página: título, **Type**, fecha, constelación y distancia. Solo se quedan las de tipo `Observation` cuyo título no sugiera texto encima (*annotated*, *compass*, *spectrum*, *illustration*…).
3. Baja el original del CDN (`wallpaper_uhd` en 4K y, si no existe, `large`) a un caché fuera del share.
4. Genera la versión para la tele: imagen entera centrada, fondo desenfocado y oscurecido, leyenda abajo con un degradé para que se lea sobre cualquier imagen. La escritura es atómica, así que la tele nunca ve un archivo a medias.

## Próximo paso: vida artificial en la tele 🧬

Un protector de pantalla de Android es solo un `DreamService`. La idea siguiente es una app mínima en Kotlin con un `WebView` que apunte a una simulación servida desde el servidor (ej. `http://gilbert.local:8080`), y que alterne con Webb según la hora: universo de día, puntos de vida artificial de noche.

---

## Créditos

- Imágenes: **ESA/Webb, NASA & ESA** y los equipos de cada observación. Antes de redistribuirlas, revisá las [condiciones de uso](https://esawebb.org/copyright/).
- Protector de pantalla: [Aerial Views](https://github.com/theothernt/AerialViews) de Neil Turner.

## Licencia

Los scripts de este repo están bajo licencia [MIT](LICENSE).
