# 🔭 tv-webb-screensaver

Turn an **Android TV** screensaver into a window to the universe: **NASA's Astronomy Picture of the Day (APOD)** plus images from the **James Webb** and **Hubble** space telescopes, refreshed automatically every day.

Instead of the same old photographer shots (Google's *Backdrop*), the TV shows:

- **NASA APOD**: today's picture comes up **often**, with its explanation (optionally translated and summarized) in a side panel that never covers the image.
- **ESA/Webb** (*Picture of the Month*) and **ESA/Hubble** (*Picture of the Week*), plus nebulae, galaxies and stars: a **random selection that changes every day**.

Also:

- **No distortion:** every image is fit to the TV's exact resolution, and the borders are filled with a blurred copy of the same image.
- **Captions:** title, date, constellation and distance, in English or Spanish.
- **Real photos only:** charts, spectra, and versions with compasses or labels are discarded.

```
┌──────────────────────┐    daily cron     ┌───────────────────┐
│ NASA APOD · ESA/Webb │ ───────────────▶  │  Home server      │
│ ESA/Hubble           │   webb_sync.py    │  /srv/webb (pool) │
└──────────────────────┘                   │  (Samba share)    │
                                           └─────────┬─────────┘
                                                     │ SMB
                                           ┌─────────▼─────────┐
                                           │  Android TV       │
                                           │  Aerial Views     │
                                           │  (screensaver)    │
                                           └───────────────────┘
```

Tested on a **Skyworth Android TV** with an Ubuntu mini PC as the server.

---

## Requirements

- An **Android TV / Google TV** (Skyworth, Sony, TCL, Chromecast, Nvidia Shield, etc.)
- A **Linux server** on the same network that is always on (mini PC, Raspberry Pi, NAS…)
- The **[Aerial Views](https://github.com/theothernt/AerialViews)** app (free, open source, on the Play Store)
- `adb` on the server (or on any computer on the network)

## Layout

```
scripts/
├── webb_sync.py        # fetches APOD/Webb/Hubble, filters, fits, adds text, builds the daily pool
├── setup-samba.sh      # creates the shared folder for the TV
└── tv-screensaver.sh   # connects over adb and switches the screensaver
.env.example            # copy to .env for your local settings (git-ignored)
```

---

## Setup

### 1. Find the TV's IP address

From the server:

```bash
ip a                              # see your network range, e.g. 192.168.1.0/24
sudo nmap -sn 192.168.1.0/24
```

Look for the TV maker in the list. Heads-up: **Skyworth shows up as `Shenzhen Chuangwei-RGB Electronics`** (Chuangwei is its Chinese name).

You can also find it on the TV under **Settings → Network & Internet**.

> 💡 Give the TV a **fixed IP** in your router (DHCP reservation). If its IP changes, the adb connection breaks.

### 2. Enable adb on the TV

1. **Settings → Device Preferences → About** → press **Build** 7 times.
2. **Device Preferences → Developer options** → turn on **USB debugging** (on many TVs this also enables network adb on port 5555).
3. On the server:

```bash
sudo apt install -y adb
export TV=192.168.1.65            # your TV's IP
./scripts/tv-screensaver.sh connect
```

A prompt appears on the TV: check **"Always allow from this computer"** and accept. `adb devices` must show `device`, not `unauthorized`.

### 3. Create the shared folder

```bash
./scripts/setup-samba.sh
```

It asks for a Samba password: that's the one you'll enter on the TV. By default it shares `/srv/webb` as `webb`, with your user.

### 4. Fetch and prepare the images

```bash
sudo apt install -y python3-pil fonts-dejavu-core
adb shell wm size                       # the TV's real resolution, e.g. 1920x1080
cp .env.example .env                    # and set WEBB_SIZE to what wm size returned
./scripts/webb_sync.py
```

The first run takes a while: it reads every image page and processes them all. After that it only handles new ones.

#### What the TV shows (the daily *pool*)

The shared folder does **not** hold everything. On every run it is rebuilt with:

| What | How many | Variable |
|---|---|---|
| Latest APOD, **repeated** so it shows up often | 8 copies | `POOL_APOD_WEIGHT` |
| APODs from previous days | 6 | `POOL_APOD_RECENT` |
| **Random** Webb + Hubble (changes daily) | 40 | `POOL_RANDOM` |

With the defaults, roughly **1 in 7 images is today's APOD**. The copies are *hardlinks*, so they take no extra space. The full library lives in `~/.cache/webb/rendered`.

> Turn on **shuffle** in Aerial Views: otherwise all APOD copies play back to back.

#### Configuration

Everything is set through environment variables, a `.env` file at the repo root, or the equivalent flag (see `--help`). Real environment variables win over `.env`.

| Variable | Values | Default |
|---|---|---|
| `WEBB_SOURCES` | any combination of `webb,hubble,apod` | all three |
| `WEBB_DEST` | folder the TV reads | `/srv/webb` |
| `WEBB_CACHE` | originals, library and state | `~/.cache/webb` |
| `WEBB_SIZE` | TV resolution | `1920x1080` |
| `WEBB_LANG` | `en` / `es` | `en` |
| `WEBB_CAPTION` | `1` with text / `0` image only | `1` |
| `WEBB_TRANSLATE` | `none` / `claude` / `argos` | `none` |
| `WEBB_FONT` | path to a `.ttf` | DejaVu Sans |
| `APOD_API_KEY` | free key from [api.nasa.gov](https://api.nasa.gov) | `DEMO_KEY` |
| `APOD_DAYS` | how many days of APOD to fetch per run | `14` |
| `APOD_TEXT` | `summary` / `full` / `none` | `summary` |

`DEMO_KEY` is enough for one run a day, but getting your own key is better (free, arrives instantly by email).

#### Text, language and translation

- **Webb and Hubble** get a short caption at the bottom: title, date, constellation, distance and credit.
- **APOD** gets a **panel on the right** with title, date, credit and the explanation. The whole image goes on the left: the text never covers it. The font size adjusts itself so everything fits.
- Dates and distances are written in the chosen language with no external dependencies. Titles and explanations come in English from the sources; to translate or condense them:
  - `WEBB_TRANSLATE=claude` + `ANTHROPIC_API_KEY=...` (optional `CLAUDE_MODEL`): translates when `WEBB_LANG=es`, and **condenses the APOD explanation to 2–3 sentences** (in any language), which is what reads comfortably from the couch. Each text is processed only once and cached; it costs cents per month. **Recommended.**
  - `WEBB_TRANSLATE=argos`: offline with [Argos Translate](https://github.com/argosopentech/argos-translate) (`pip install argostranslate` + the en→es package). Translates but doesn't summarize, so the APOD text stays long.
  - Without it, APOD shows the full explanation with a smaller font.

If you change language, resolution or text settings, the script **re-renders everything from the cache** without downloading again.

#### Inspect an image

```bash
./scripts/webb_sync.py --debug potm2610a     # Webb metadata and whether it's accepted
```

#### Run it daily

APOD is published once a day, so the cron is **daily** (8:31 AM here). Keys and language live in `.env`:

```bash
crontab -l 2>/dev/null | grep -v webb | crontab -
(crontab -l 2>/dev/null; echo "31 8 * * * $PWD/scripts/webb_sync.py >/dev/null 2>&1") | crontab -
```

The run history is in `/srv/webb/.sync.log`.

### 5. Configure Aerial Views on the TV

1. Install **Aerial Views** from the Play Store.
2. In the app, turn off the Apple videos and the other providers.
3. Enable the **Samba / SMB** source with:
   - Host: the server's IP (e.g. `192.168.1.72`)
   - Share: `webb`
   - Samba user and password
4. Make sure it includes **photos**, not just videos, and turn on **shuffle**.
5. If there's a photo scaling option, pick **fit** (not crop or stretch): the images already come at the exact size.

### 6. Set it as the screensaver

Android TV hides the option to pick another screensaver, so it's done over adb:

```bash
./scripts/tv-screensaver.sh status    # 👈 write down what it returns (your original)
./scripts/tv-screensaver.sh aerial
./scripts/tv-screensaver.sh test      # starts it right away to check
```

If a nebula shows up: done 🌌

---

## Going back to the original screensaver

```bash
./scripts/tv-screensaver.sh restore
```

The original value **varies by TV**. On the Skyworth it's `com.google.android.backdrop/.Backdrop`; on others it's `com.google.android.apps.tv.dreamx/.service.Backdrop`. Use whatever `status` returned before you changed anything:

```bash
BACKDROP=<what-you-wrote-down> ./scripts/tv-screensaver.sh restore
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Connection refused` | adb isn't listening. Turn on **USB debugging** and restart the TV. If **Wireless debugging** exists, use `adb pair` with the code the TV shows, then `adb tcpip 5555`. |
| `failed to authenticate` / `unauthorized` | The prompt on the TV hasn't been accepted. Press Home (so the screensaver isn't running) and reconnect. |
| The authorization prompt **doesn't show up** | On the TV: *Revoke USB debugging authorizations* + toggle USB debugging off/on. On the server: `adb kill-server && rm -f ~/.android/adbkey*` and reconnect. If it still fails, unplug the TV for 10 s. |
| Authorization keeps getting lost | "Always allow" wasn't checked, or you ran `sudo adb` (root uses a different key). Always run adb without sudo. |
| Distorted images | `WEBB_SIZE` doesn't match the TV: check `adb shell wm size`. Also check the photo scaling in Aerial Views. |
| APOD hardly ever shows up | Turn on shuffle in Aerial Views or raise `POOL_APOD_WEIGHT`. |
| A source brings nothing | Check the output or `.sync.log`: if a category page moved, adjust it in `ESO_SITES`. |
| An image with text slipped through | Run `--debug <id>` to see its type and title. If it makes sense, add the word to `TEXT_HINTS` in `webb_sync.py`. |
| Black screen in Aerial Views | SMB support is picky about paths: use a simple share, no subfolders, with user and password. |

---

## How `webb_sync.py` works

1. **APOD:** queries `api.nasa.gov/planetary/apod` for the last `APOD_DAYS` days. Skips videos and images narrower than 1000 px.
2. **Webb and Hubble:** both run on the same ESA platform. It reads *Picture of the Month* / *Picture of the Week* and some categories (set in `ESO_SITES`), and for each new image reads the title, **Type**, date, constellation and distance. Only `Observation` images whose title doesn't suggest text on top (*annotated*, *compass*, *spectrum*, *illustration*…) are kept.
3. Downloads the original (Webb/Hubble: `wallpaper_uhd` in 4K or `large`; APOD: `hdurl`) to `~/.cache/webb/raw`.
4. Renders the TV version to `~/.cache/webb/rendered`: whole image without distortion, blurred background, and the caption or text panel.
5. Rebuilds the TV folder with the daily pool. Files are written atomically, so the TV never sees a half-written one.

---

## Next step: artificial life on the TV 🧬

An Android screensaver is just a `DreamService`. The next idea is a minimal Kotlin app with a `WebView` pointing to a simulation served from the home server (e.g. `http://gilbert.local:8080`), alternating with the space images by time of day: the universe during the day, artificial-life dots at night.

---

## Credits

- Images: **ESA/Webb** and **ESA/Hubble** ([usage terms](https://esawebb.org/copyright/)), and **NASA APOD** (each picture has its own author and many carry their own copyright; the credit is shown in the panel). This project is meant for personal use at home: check the rights of each image before redistributing.
- Screensaver: [Aerial Views](https://github.com/theothernt/AerialViews) by Neil Turner.

## License

The scripts in this repo are released under the [MIT](LICENSE) license.
