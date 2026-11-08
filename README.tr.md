<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/kurgu-lockup-dark.svg">
    <img src="docs/assets/kurgu-lockup-light.svg" alt="Kurgu" width="320">
  </picture>
</p>

# Kurgu

[English](README.md) | **Türkçe**

> **Videoyu kodlama ajanın yazar. Sen yönetirsin.** Ücretsiz, açık kaynak, kendi bilgisayarında çalışır.

Kurgu, tek bir düz dosyanın, `project.json`'un etrafında kurulmuş yerel bir video editörüdür. Claude Code zaman çizelgesini yazar (klipler, başlıklar, altyazılar, müzik, efektler). Sen de katman listesi, canlı önizleme ve özellik paneli olan tarayıcı editöründe Claude'un neredeyse doğru yaptığı yerleri düzeltirsin. Claude neyi elle değiştirdiğini ve neyi seçtiğini görür; yani "bu yazıyı büyüt" ya da "şurada kes" demen yeter. `render.py` sonucu mp4'e çevirir.

"Kurgu", İngilizcede "edit / montage" demek.

<p align="center"><img src="docs/assets/hero.gif" alt="Kurgu: tek istem, on dört stil, kendi editöründe düzenlendi" width="720"></p>

## Nasıl çalışır

```
  Claude Code  <------------>  project.json  <------------>  editör (tarayıcında)
  (skill + /kurgu)             (zaman çizelgesi)              katmanlar, önizleme, panel
       |                            |                                |
       |   changes.md ve            |                                |  .kurgu/state.json yazar
       |   .kurgu/state.json okur   v                                |  (seçim, oynatma başlığı)
       |                       render.py  ----> output.mp4 <---------+
       +--------------------------------------------------------------
              her şey 127.0.0.1'de çalışır, hiçbir şey yüklenmez
```

- **project.json** tek gerçek kaynaktır: tuval boyutu, fps ve katman listesi (`video`, `image`, `text`, `color`, `audio`); zamanlar, konumlar, geçişler, efektler ve ses zarfları.
- **Editör**, `server.py`'nin sunduğu küçük bir yerel web uygulamasıdır. Elle yaptığın her değişiklik `project.json`'a kaydedilir ve `changes.md`'ye yazılır; Claude neyi değiştirdiğini buradan öğrenir.
- **Seçim ve oynatma başlığı** `.kurgu/state.json`'a yazılır. Claude "bu yazı" ve "burada" ifadelerini böyle çözer.
- **render.py** kareleri numpy ile birleştirir, ffmpeg'e aktarır ve sesi karıştırır.

## Kurulum

Tek komut. Gerekirse [uv](https://docs.astral.sh/uv/)'yi, kendine ait bir Python 3.12'yi (sistemde Python olması gerekmez), Kurgu'yu ve statik bir ffmpeg'i kurar; yönetici izni istemez. Güncellemek için aynı komutu yeniden çalıştır.

| İşletim sistemi | Komut |
|---|---|
| macOS / Linux | `curl -LsSf https://raw.githubusercontent.com/leodavincs/kurgu/main/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1 \| iex"` |

Sonra **yeni** bir terminal aç ve `kurgu proje/klasoru` yaz. `kurgu --help` bütün komutları sıralar. `kurgu doctor` makineyi denetler (Python, ffmpeg, donanım kodlayıcıları, boş disk; ajanlar için `--json`, eksik ffmpeg'i indirmek için `--fix`).

**Klondan kurulum** (bir dalı ya da kendi değişikliklerini denemek için): `KURGU_SOURCE=/kurgu/klasorun sh install.sh`. `KURGU_SOURCE` bir klasör, bir adres ya da `git+https://...` olabilir; kodu değiştirince komutu yeniden çalıştır.

Chrome (ya da Chromium) yalnızca kodla video araçları için gerekir: `kurgu elements` ve `kurgu rerender`. Editörün kendisi onsuz çalışır. Kurgu Chrome'unu bulamazsa `KURGU_CHROME=/chrome/yolu` ver.

### Kullanıcın için kurulum (kodlama ajanı için: Claude Code, Codex, ...)

Kullanıcı "Kurgu'yu kur ve projemi aç" dediğinde aynen şunu uygula:

1. **İşletim sistemini bul** (macOS/Linux'ta `uname -s`, Windows'ta `$env:OS` / `ver`).
2. **O sistemin tek satırlık komutunu çalıştır** (yukarıdaki tablo). Çıktıyı sonuna kadar oku; `Done. Next step:  kurgu <your project folder>` ile biter.
3. **Denetle:** `kurgu doctor --json` (komut bu kabukta henüz PATH'te değilse `%USERPROFILE%\.local\bin\kurgu.exe` ya da `~/.local/bin/kurgu`). `"ok": true` hazır demektir.
4. **Projeyi aç:** `kurgu "<proje klasörü>"` komutunu arka planda çalıştır (adresi yazar ve tarayıcıyı açar; masaüstü tarayıcısı yoksa `--no-open`) ve adresi kullanıcıya söyle. Yolu tırnak içine al: boşluklu, Türkçe harfli (İ ı ş ğ) ya da emojili klasör adları sorun değil.

Bir şey ters giderse:

- **PowerShell "komut dosyası çalıştırma devre dışı" / yürütme ilkesi.** Tek satırlık komut bu tek süreç için zaten `-ExecutionPolicy Bypass` kullanır; aynen yaz, normal (yönetici olmayan) PowerShell'den. Kabuk şirket ilkesiyle kilitliyse kullanıcıdan kendi PowerShell penceresinden çalıştırmasını iste.
- **Şirket proxy'si / indirme yok.** `HTTPS_PROXY` (ve `HTTP_PROXY`) değişkenini proxy adresine ayarla: `$env:HTTPS_PROXY="http://proxy.sirket:8080"` (PowerShell) ya da `export HTTPS_PROXY=...`; komutu yeniden çalıştır. Yalnız ffmpeg inmezse Kurgu yine kurulur; bir ffmpeg sürümünü elle indir, `KURGU_FFMPEG` ve `KURGU_FFPROBE` değişkenlerini `ffmpeg` ve `ffprobe` dosyalarının tam yoluna ayarla, sonra `kurgu doctor` çalıştır.
- **Kurulumdan hemen sonra `kurgu` bulunamıyor.** Kurulumu çalıştıran terminalin PATH'i eskidir. Yeni terminal aç ya da tam yolla çağır (kurulum yazdırır; klasör `uv tool dir --bin` ile görünür).
- **Yalnız ffmpeg:** `kurgu setup-ffmpeg` (`--force` yeniden indirir, `--json` ajanlar için).
- **Windows Defender / SmartScreen.** Bir betiğin indirdiği dosyalarda "internetten indirildi" işareti olmaz; SmartScreen normalde sessiz kalır. Defender `ffmpeg.exe`'yi ilk açılışta birkaç saniye tarayabilir; normaldir.
- **Kaldırma:** `uv tool uninstall kurgu`, sonra veri klasörünü sil (Windows `%LOCALAPPDATA%\Kurgu`, macOS `~/Library/Application Support/Kurgu`, Linux `~/.local/share/kurgu`). İsteğe bağlı: `uv cache clean`.

### ffmpeg nereden gelir

Sıra: `KURGU_FFMPEG` / `KURGU_FFPROBE` (dosya ya da klasör) → PATH'teki `ffmpeg` (sistemdeki her zaman önce gelir) → `kurgu setup-ffmpeg`'in Kurgu'nun veri klasörüne indirdiği kopya (`%LOCALAPPDATA%\Kurgu\ffmpeg`, `~/Library/Application Support/Kurgu/ffmpeg`, `~/.local/share/kurgu/ffmpeg`) → bilinen kurulum klasörleri (Homebrew, winget, Chocolatey, Scoop, ...). Editör kendiliğinden hiçbir şey indirmez; yalnız kurulum betiği, `kurgu setup-ffmpeg` ya da `kurgu doctor --fix` indirir. İndirme SHA-256 ile ve `-version` çalıştırılarak doğrulanır.

Kullanılan statik derlemeler (hepsi FFmpeg'in GPL derlemesi, özgür yazılım; senin makinene iner, Kurgu'nun içinde gelmez; bkz. https://ffmpeg.org/legal.html): Windows ve Linux için [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), macOS için [ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de/). Kendininkini istersen `brew install ffmpeg`, `sudo apt install ffmpeg` ya da `winget install Gyan.FFmpeg` de olur.

**FFmpeg uyumluluğu.** CI, ffmpeg'e dayanan testleri (dışa aktarma, proxy, dalga biçimi, ses seviyesi, şeritler, kare eşleşmesi) Linux'ta ffmpeg 6.1, 7.1 ve 8.1 ile çalıştırır; tüm testler de her çalıştırıcının kendi ffmpeg'iyle koşar. `kurgu doctor` sürümünü gösterir ve 6.1'in altındaysa uyarır.

### Diğer yollar

Tek gereksinim Python 3.10+ ve ffmpeg/ffprobe'dur; tek satırlık komut ikisini de sağlar. Yeni Python kurulumları sistem Python'una çıplak `pip install`'ı reddeder (PEP 668), o yüzden yalıtılmış ortam kullan:

```bash
uvx --from git+https://github.com/leodavincs/kurgu kurgu proje/klasoru     # kurulum yok, doğrudan git'ten çalışır
pipx install git+https://github.com/leodavincs/kurgu                         # kalıcı komut (ffmpeg için: kurgu setup-ffmpeg)
```

**Sanal ortam (klondan, geliştirme için):**

```bash
git clone https://github.com/leodavincs/kurgu.git && cd kurgu
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/check_deps.py        # `kurgu doctor` ile aynı
```

İsteğe bağlı, otomatik altyazı için (konuşma tanıma tamamen yerelde çalışır, model ilk kullanımda bir kez iner): `uv tool install --force --python 3.12 --with faster-whisper git+https://github.com/leodavincs/kurgu` (Apple Silicon'da `--with mlx-whisper`), ya da `pipx inject kurgu faster-whisper`, ya da sanal ortamında `pip install faster-whisper`.

### Claude Code eklentisi olarak

```
/plugin marketplace add leodavincs/kurgu
/plugin install kurgu@kurgu
```

Eklenti MCP sunucusunu [`uvx`](https://docs.astral.sh/uv/) ile tanıtır; macOS, Linux ve Windows'ta aynı çalışır ve Python bağımlılıklarını kendisi kurar. Önce uv'yi kur (`brew install uv`, `winget install astral-sh.uv` ya da `pipx install uv`) ya da ffmpeg'i de kuran yukarıdaki tek satırlık kurulumu çalıştır. Onsuz ffmpeg yine gerekir (`uvx --from git+https://github.com/leodavincs/kurgu kurgu setup-ffmpeg`). Claude Code eklenti komutlarını ad alanıyla verir; editör komutu **`/kurgu:kurgu proje/klasoru`** olur (`/kurgu` yazınca tamamlanabilir). uv kullanmak istemezsen Kurgu'yu pipx ile kur ve MCP sunucusunu elle ekle: `claude mcp add kurgu -- kurgu-mcp`.

## Kullanım

1. **Claude'a söyle.** Claude Code'da: "clip.mp4'ten 30 saniyelik dikey bir fragman yap, başa başlık, Türkçe altyazı ekle, müziği arkaya koy." Claude dosyaları `media/` klasörüne kopyalar ve `project.json`'u yazar.
2. **Editörü aç.** Claude Code'da `/kurgu:kurgu proje/klasoru` yaz; eklenti yoksa `kurgu proje/klasoru`. Tarayıcın `http://127.0.0.1:8765` adresinde açılır.
3. **İnce ayar yap.** Yazıyı önizlemede sürükle, klipleri kırp, yazı tipini değiştir, efekt ekle, ses fazla yüksekse o ana not bırak.
4. **Yenisini iste.** Bir katmanı seç ya da oynatma başlığını bir yere bırak ve Claude'a söyle: "kısalt", "bunu yukarı al", "burada kes". Claude önce `project.json`'u, `changes.md`'yi ve seçimini okur; elle yaptığın değişiklikleri korur.
5. **Render al.** Editördeki Render düğmesi ya da:

```bash
kurgu-render proje/klasoru --draft              # yarım çözünürlük, hızlı
kurgu-render proje/klasoru                      # tam kalite
kurgu-render proje/klasoru --frame 12.3 --output kare.png
```

Önce taslak, güzel görününce tam kalite. (Klondan çalışıyorsan `python render.py ...` aynı işi yapar.)

## İnceleme: izle, not al, ajanına gönder

Kurgu **İnceleme kipinde** açılır: büyük önizleme, altında kelimeleriyle birlikte okunur tek bir ses dalgası, sağda notların. Yalnız elle kurgu yaparken gereken her şey ☰ menüde (tam editör için `Shift+E`).

1. **İzle.** *İzle + not* ile tam ekrana geç. Yazmaya başlayınca video durur; `Enter` notu o saniyeye ekler, video kaldığı yerden devam eder.
2. **Göster.** Yeri tarif edemiyor musun? Karenin üstüne çiz: elips, dikdörtgen ya da ok, beş renkte (`Alt+O/B/A`, `Alt+1…5`). Çizim projenin kendi pikseliyle saklanır; ajanın eline "sağ üst bir yer" değil, tam koordinat geçer.
3. **Gönder.** *Ajana gönder* düğmesine bir tık. Her not saniyesi, altındaki sahne ve katmanlar, çizdiğin işaretler (renk, konum, boyut, altındaki katmanlar) ve işaretlerin işlendiği kare görüntüsüyle gider. Hepsi proje klasöründe `NOTLAR.md` ve `.kurgu/inbox.jsonl` içine yazılır.
4. **Ajan düzeltir.** Claude Code `kurgu notes wait <proje>` komutunu arka planda çalıştırır, sen gönderince uyanır; Codex aynı işi kendi aracıyla yapar (bkz. *Diğer ajanlarla kullanım*). İstersen ajanı Kurgu uyandırsın: `kurgu setup-agent --claude --wake` / `--codex --wake` her gönderide ajanı arka planda başlatır, terminale bakman bile gerekmez.
   Her not, ekrandaki her katmanın ölçülmüş kutusunu (sol, üst, sağ, alt kenar, piksel olarak) ve konum sayılarının neyi gösterdiğini de taşır; ajan kenarları çevresiyle hizalar, notu kapatmadan önce `kurgu frame <proje> <saniye>` ile sonucun karesine (tarayıcısız bir JPEG) bakar.
   Belirsiz bir not ("daha dinamik olsun") ya en küçük net okumasıyla uygulanır ve seçilen değerler cevapta yazılır, ya da ajan soru sorar: not *soruldu* olur, cevabı editörde yazarsın, ajana geri gider.
   Zamana yayılan bir değişikliği kontrol etmek için ajan tek tek kare yerine tek bir `kurgu strip <proje> <t0> <t1>` çağrısı (etiketli kontak sayfası) yapar.
   Her not, kendi anı çevresinde ölçülmüş ses bilgisini de taşır (tepe ve RMS düzeyi, sessizlikler, döküm varsa kelime zamanları).
5. **Cevabı gör.** Düzeltilen not, listede ajanın tek satırlık cevabıyla *düzeltildi* olur. Ajan çalışırken yaptığın yeni gönderi kaybolmaz. Ajan çalışırken gönderilen notlarda *Ajan çalışıyor…* yazar; ajan hiç başlayamadıysa (örneğin giriş yapılmamışsa) nedenini söyleyen bir uyarı çıkar, yanında `.kurgu/hook.log` bağlantısı olur; ajan bitti ama bir not açık kaldıysa onu da söyler. Yeniden üretilmiş bir klibin eski ve yeni görüntüsünü karşılaştırmak için `B`'ye bas (önceki sürüm yoksa kısa bir mesaj çıkar).
   Editörde tuşlar: `W` ya da `F` izle kipini tam ekran açar, `N` not kutusuna atlar, `Enter` notu ekler, `Esc` çıkar, `B` önce / sonra arasında geçiş yapar. İzle kipi açılırken yazdıkların kaybolmaz. Çizim kare üstünde yalnız seçtiğin ya da üstüne geldiğin notun için görünür (çizimli notlarda küçük bir daire simgesi olur).

<!-- kurgu:generated:agent-side -->
Ajan tarafı (teknik): her notta ayrıca `bounds` (kutu) ve `anchor` (konum açıklaması) alanları gider. Komutlar: `kurgu notes wait | list | done | ask | add`, `kurgu frame <proje> <saniye> [-o cikti.jpg] [--full]`, `kurgu strip <proje> <t0> <t1> [--n 6]`, MCP `notes_wait`, `notes_list`, `notes_done`, `notes_ask`, `render_frame`, `render_strip`. Klibe gömülü yazı ve şekiller için `kurgu detect <proje> <saniye> [--layer ID] [--json]` (takma ad `kurgu ocr`, MCP `detect`); kodla üretilmiş klibi kaynağından yeniden üretmek için `kurgu rerender <proje> <katman|birim>` (MCP `rerender`). "Kurgu notlarımı bekle ve gelince uygula" dersen ajan bekle, düzelt, kapat döngüsünde kalır; Kurgu projede hangi ajan oturumunun çalıştığını `.kurgu/agents.json` içine yazar, uyandırma kancası o oturumu sürdürür. Ayrıntı `AGENTS.md` ve `SPEC.md` içinde.
<!-- kurgu:generated:end -->

## Diğer ajanlarla kullanım

Kurgu, ajanların kendi araç protokolüyle (MCP; `kurgu-mcp`, `mcp_server.py` ile aynı kod) konuşan bir sunucuyla gelir; MCP destekleyen her ajan aynı yetkileri alır: editörü açar, neyi seçtiğini görür, projeyi doğrulanmış toplu değişikliklerle düzenler, işlenmiş kareye bakar, render alır. Repo kökündeki `AGENTS.md`, o dosyayı okuyan ajanlara Kurgu ile nasıl çalışacaklarını anlatır.

Araçlar:
<!-- kurgu:generated:tool-names -->
`open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render_strip`, `rerender`, `detect`, `render`, `get_changes`, `get_brief`, `update_brief`, `get_notes`, `update_note`, `add_note`, `notes_list`, `notes_wait`, `notes_done`, `notes_ask`, `list_fonts`, `list_media`, `import_file`, `transcribe`, `make_captions`
<!-- kurgu:generated:end -->

Hepsi isteğe bağlı `project_dir` alır (varsayılan: sunucunun çalışma klasörü ya da `KURGU_PROJECT_DIR` ortam değişkeni). Aşağıdaki örnekler yukarıdaki tek satırlık kurulumu (ya da `pipx install git+https://github.com/leodavincs/kurgu`) yaptığını varsayar; bu, `kurgu-mcp` komutunu PATH'ine koyar (ajan bulamazsa tam yolu yaz: klasörü `uv tool dir --bin` ya da `pipx` gösterir). Klondan çalışıyorsan sanal ortamının Python'unu ve `mcp_server.py`'nin tam yolunu kullan.

**Claude Code.** Eklentiyi kur (yukarıya bak). MCP sunucusu eklentinin `.mcp.json` dosyasıyla (`uvx` üzerinden) kendiliğinden tanıtılır; ayar gerekmez. Eklenti olmadan: `claude mcp add kurgu -- kurgu-mcp`.

**Deneysel:** editördeki Ask AI kutusu Claude Code yoksa Codex'e düşer, ama bu yol henüz uçtan uca test edilmedi. Özel ajan komutu yalnızca `KURGU_AGENT_CMD` veya `~/.config/kurgu/config.json` içinden okunur, proje klasöründen asla.

**OpenAI Codex (CLI, IDE, masaüstü uygulaması).** `kurgu setup-agent --codex` (`--project <klasör>` ile o projenin AGENTS.md dosyasına kısa bir Kurgu bölümü eklenir; `--dry-run` neyin değişeceğini gösterir). MCP sunucusunu `~/.codex/config.toml` içine `tool_timeout_sec = 3600` ile kaydeder (Codex'in varsayılanı 60 sn, `notes_wait`'i keserdi) ve skill'i `~/.agents/skills/kurgu` altına kurar. Codex'i yeniden başlat. Codex arka plandaki bir süreç tarafından uyandırılamaz; bu yüzden izle + not döngüsü tekrarlanan `notes_wait` çağrılarıyla işler (ya da Gönder'e basıp "notlara bak" dersin); `--wake` ile Gönder'e bastığında Kurgu ajanı kendisi başlatabilir (AGENTS.md'deki "Wake the agent" notlarına bak). Codex'i proje klasörünün içinde, `workspace-write` kum havuzuyla çalıştır.

**Uyanan Codex neyi çalıştırabilir.** `kurgu wake codex`, Codex'i kendi `CODEX_HOME` klasöründe (`~/.config/kurgu/codex-home`, `%APPDATA%\kurgu\codex-home`) üretilmiş bir execpolicy dosyasıyla (`rules/kurgu.rules`) başlatır; kendi `~/.codex` klasörün ve diğer Codex oturumların değişmez (model ayarların kopyalanır, girişin bağlanır, kopyalanmaz; `sessions` klasörün bağlanır, böylece `resume <uuid>` ve `--last` konuşmanı bulur). Codex kuralları yalnız yasaklayabilir; dosya ağ araçlarını (`curl`, `wget`, `ssh`), `rm`/`mv`/`cp`/`tee` gibi yazanları, `python`/`node`/`perl`/`awk`/`find`/`env`/`xargs`/`ffmpeg`'i, paket yöneticilerini, `git push|clone|fetch|pull`'u, `sed -i`'yi, `rg --pre`'yi, `-c` ile yeniden açılan kabukları ve not işi olmayan `kurgu` alt komutlarını yasaklar. Gerisi (`kurgu notes|frame|strip|rerender|detect|probe`, `ffprobe`, `cat`, `ls`, `head`, `sed -n`, `grep`, `rg`, `jq`) `workspace-write` kum havuzunda çalışır: yazma yalnız proje klasöründe, ağ yok. Codex'in yama aracıyla yapılan düzenlemeler (`project.json` böyle değişir) serbest kalır. Claude karşılığı `--allowedTools` listesidir. Kuralsız çalıştırmak için kullanıcı yapılandırma dosyasına (`~/.config/kurgu/config.json`) `"codex_policy": "off"` yaz; proje klasörü bunu yapamaz. Girişin `auth.json`'da değil işletim sistemi anahtar zincirindeyse uyandırma kısıtsız çalışmak yerine başlamaz. Windows'ta sembolik bağ kurulamazsa tam oturuma devam (`resume <uuid>`) kullanılamaz, yeni oturum açılır.

**Uyanan Claude Code neyi çalıştırabilir.** `claude -p ... --allowedTools` tam olarak şunu alır: `kurgu notes|frame|strip|rerender|detect|probe`, `kurgu --help`, `kurgu <komut> --help` ve `kurgu` MCP araçları; genel Bash yok, `ffprobe` yok (URL okuyabilir: `kurgu probe <proje> [katman|dosya]` kullan) ve `--setting-sources user` vardır, yani projenin kendi `.claude/settings*.json` kancaları ve `.mcp.json` sunucuları hiç yüklenmez.

**Cursor.** Projendeki `.cursor/mcp.json` (tüm projeler için `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "kurgu": {
      "command": "kurgu-mcp",
      "env": { "KURGU_PROJECT_DIR": "${workspaceFolder}" }
    }
  }
}
```

**Gemini CLI.** `~/.gemini/settings.json` (ya da projedeki `.gemini/settings.json`):

```json
{
  "mcpServers": {
    "kurgu": {
      "command": "kurgu-mcp",
      "timeout": 900000
    }
  }
}
```

Kurgu'nun çalışma biçimi değişmez: editör açıksa ajan değişikliği onun üzerinden yapar (sen canlı görürsün, `changes.md` içine `[agent]` diye yazılır); açık değilse `project.json`'a doğrudan yazar.

## Kodla video yapıyorsan

Videon kodla üretiliyorsa (cinecode, kendi sayfan, Remotion), videoya düşen bir notu bitmiş mp4'ün üstünde değil o kodda düzeltmek gerekir. `kurgu import` sahne listeni Kurgu projesine çevirir: her sahne, nereden geldiğini hatırlayan bir klip olur (`project.json` içinde `source`). `kurgu elements` bir sahnenin yazısının ekranda nerede durduğunu ölçer. İkisi de ek bağımlılık istemez (`elements` için yalnızca headless Chrome gerekir).

**cinecode** ([sarpstar/cinecode](https://github.com/sarpstar/cinecode), MIT). Taslağı ya da finali her zamanki gibi üret, sonra film klasöründe: `kurgu import . --elements`. `shots.json`'u (kareler, `fps`, `text_on_screen`, `vo`), en yeni `renders/film-vN.mp4` ya da `renders/draft-vN.mp4` dosyasını ve `audio/mix.json`'u okur; `kurgu/project.json` yazar (shot başına bir klip, shot karelerinden kesilmiş; `scenes.md` her shot'ın yazısını ve seslendirmesini listeler). Film klasöründeki mevcut hiçbir dosya değişmez. Kanvasa çizilen yazı ölçülemez; komut bunu söyler.

**Kare çizen düz bir HTML sayfası.** Kareye göre saf fonksiyon olan `window.renderFrame(i)` (ya da `captureFrame(i)`) sunan her sayfa olur: `kurgu elements sayfa.html --frames 0:240:6 -o sahne.kurgu.json`. DOM'daki yazı ve öğeler kare kare kutu alır; 2D kanvas API'siyle (`fillText`, `fill`/`stroke`, `fillRect`, `Path2D`, `drawImage`) çizilen yazı, şekil ve görseller de alır: `kurgu elements` bunları kendiliğinden kaydeder, sayfada değişiklik gerekmez. Kanvas yazısının metni sayfanın kendi kaynaklarında geçiyorsa `file:line` yazılır (`--source-root` bir klasör daha ekler, birden çok yerde geçiyorsa `line_ambiguous`), aynı renkteki yakın şekiller tek öğede birleşir, `--no-canvas` bunu kapatır. WebGL / three.js çıktısı pikseldir, yakalanmaz: aşağıdaki kancayı ya da OCR / görü yedeğini (yakında) kullan.

İstersen bir öğeye `data-kurgu-id="baslik"` (istersen `data-kurgu-src="sahne.js:41"`) yazarak ona sabit bir ad ve kaynak satırı verebilirsin.

**Motor yazarları için (WebGL / 3D sayfalar).** 2D kanvasın çizmediği her şey pikseldir, sayfadan ölçecek bir şey yoktur; bitmiş videoda `kurgu detect <proje> <saniye>` (OCR ve şekil bulma, yaklaşık) karedeki yazı ve şekilleri bulur. Kesin kutu için sayfan `window.kurguElements(i)` tanımlarsa `kurgu elements` onu `renderFrame(i)`'den hemen sonra çağırır ve cevabı DOM'da bulduklarıyla birleştirir. Kutuları sayfa pikseliyle döndürür:

```js
window.kurguElements = (i) => [
  { id: "title", label: "Fena değil", kind: "text", l: 116, t: 160, r: 640, b: 250,
    file: "engine/scene-s03.js", line: 41 },        // file ve line isteğe bağlı
];
```

**Geri kalan her şey.** Bir mp4 üret, yanına `scenes.json` yaz: `{"fps": 30, "video": "out/film.mp4", "scenes": [{"id": "intro", "start_frame": 0, "end_frame": 90, "file": "scenes/intro.html"}]}`, sonra `kurgu import <klasör>`. Remotion projesi tanınır ama henüz içe alınmaz; komut neyi yazman gerektiğini söyler. Var olan bir `project.json` `--force` olmadan asla ezilmez.

### Sıfırdan mini sahne

Boş bir klasörde üç dosya bütün döngüyü gösterir. `scenes/hello.html`, `i`. kareyi yalnızca `i`'ye bağlı olarak çizer:

```html
<canvas id="c" width="1280" height="720"></canvas>
<script>
const g = document.getElementById("c").getContext("2d");
window.renderFrame = (i) => {                      // ekrandaki her şey yalnızca kare numarasına bağlı
  g.fillStyle = `hsl(${i * 3}, 70%, 45%)`;
  g.fillRect(0, 0, 1280, 720);
  g.fillStyle = "#fff";
  g.font = "bold 96px sans-serif";
  g.fillText("Hello Kurgu", 100 + i * 4, 380);
};
</script>
```

`scenes.json` sahneyi (30 fps'de 0 ile 60. kare) ve ait olduğu filmi söyler:

```json
{"fps": 30, "video": "out/film.mp4",
 "scenes": [{"id": "hello", "start_frame": 0, "end_frame": 60, "file": "scenes/hello.html"}]}
```

İlk `out/film.mp4` olarak 2 sn'lik herhangi bir mp4 olur, örneğin `mkdir out && ffmpeg -f lavfi -i color=black:s=1280x720:d=2:r=30 -pix_fmt yuv420p out/film.mp4`. Sonra:

```bash
kurgu import .                       # 1. scenes.json -> ./kurgu/project.json (her sahne, kendi sayfasını hatırlayan bir klip)
kurgu kurgu                          # 2. aç; not yaz ("yazı büyük olsun, renk yavaş değişsin"), Gönder'e bas; ajan scenes/hello.html'i düzeltir
kurgu rerender kurgu hello           # 3. yalnız o sahneyi headless Chrome ile yeniden üretip yerine koyar; editör hemen yeni halini gösterir
```

Sonuca tarayıcısız bakmak için `kurgu frame kurgu 1.0`. 3. adım için Chrome kurulu olmalı.

## Editör turu

- **Katman listesi.** En öndeki katman en üstte, gruplu (Görüntü, Başlıklar, Ses). Gizle, kilitle, sırala, yeniden adlandır.
- **Zaman çizelgesi.** Videolar için kare şeridi, sesler için dalga formu, not işaretleri, oynatma başlığı, mıknatıs.
- **Doğrudan müdahaleli önizleme.** Katmanları tuval üzerinde sürükle, ölçekle, konumlandır; ok tuşları ince ayar yapar.
- **Özellik paneli.** Seçili katmanın tüm alanları: zamanlama, geçişler, konum, ölçek, opaklık, kırpma, sığdırma, ses, yazı ayarları ve ses zarfı.
- **Efektler.** Siyah-beyaz, parlaklık, kontrast, doygunluk, bulanıklık, yavaş yakınlaşma, kareyi dondurma; genel gren, vinyet ve sona doğru kararma.
- **Notlar.** Zaman çizelgesinde bir ana yorum iliştir. Claude bunları talimat olarak okur.
- **Gerçek kare karşılaştırma.** Önizleme hızlı bir yaklaşımdır. Tek tıkla `render.py`'nin ürettiği gerçek kareyi görüp karşılaştırabilirsin.
- **İçe aktarma.** Dosyaları pencereye sürükle ya da İçe aktar'ı kullan. Video, görsel ve ses `media/` klasörüne, yazı tipleri `fonts/` klasörüne gider ve seçili yazıya hemen uygulanabilir.
- **Yazı tipleri.** Proje, yerleşik ve sistem yazı tipleri, canlı önizlemeli. Önizleme ile render aynı yazı tipi dosyasını kullanır. Türkçe karakterler desteklenir.
- **EN / TR anahtarı** üst çubukta; editör tarayıcının diliyle açılır.

### Kısayollar

macOS'ta **Cmd**, Windows ve Linux'ta **Ctrl**.

| Tuş | İşlev |
|---|---|
| Boşluk | Oynat / duraklat (önizlemede Boşluk'a basılı tutup sürükleyerek kaydır) |
| Sol / Sağ | Önceki / sonraki kare |
| Shift + Sol / Sağ | 1 sn geri / ileri |
| `[` / `]` | Önceki / sonraki kare |
| Home / End | Başa / sona git |
| Oklar (önizleme odakta) | Seçili katmanı 1 px kaydır (Shift: 10 px) |
| Cmd/Ctrl + K | Seçili katmanı oynatma başlığında böl |
| Cmd/Ctrl + D | Seçili katmanları çoğalt |
| Cmd/Ctrl + A | Tüm katmanları seç |
| Delete / Backspace | Seçili katmanları sil |
| Cmd/Ctrl + Z / Shift + Cmd/Ctrl + Z | Geri al / yinele |
| Cmd/Ctrl + S | Şimdi kaydet (değişiklikler zaten kendiliğinden kaydedilir) |
| Cmd/Ctrl + E | Dışa aktarma penceresini aç / kapat |
| S | Mıknatısı aç / kapat |
| N | Oynatma başlığına not ekle |
| W / F | İzle + not, tam ekran (hemen yazmaya başlayabilirsin) |
| B | Yeniden üretilen klibin önce / sonra görüntüsü arasında geçiş |
| Shift + N | Not panelini göster / gizle |
| Alt + Sol / Sağ | Önceki / sonraki not |
| `\` | Medya panelini göster / gizle |
| Cmd/Ctrl + `+` / `-` | Önizlemeyi yakınlaştır / uzaklaştır (Cmd/Ctrl + tekerlek de yakınlaştırır) |
| Cmd/Ctrl + `0` / `1` | Sığdır / %100 |
| Cmd/Ctrl + Enter | Yapay zekâ kutusundaki isteği gönder |
| Esc | Pencereyi ya da menüyü kapat, yoksa seçimi temizle |

Bir metin alanına yazarken kısayollar çalışmaz.

## Proje biçimi

Ayrıntıların hepsi [SPEC.md](SPEC.md) içinde (İngilizce): katman türleri ve alanlar, efektler, yazı tipleri, render komutu ve yerel sunucu API'si. Claude bunun özetini `skills/kurgu/` altındaki skill ile alır.

## Sık sorulanlar

**Bir şey yükleniyor mu?** Hayır, bkz. [Gizlilik](#gizlilik).

**Hangi işletim sistemleri?** macOS, Linux ve Windows; Python 3.10+ ve PATH'te ffmpeg gerekir.

**Remotion'dan farkı ne?** Remotion videoları React kodudur. Kurgu videoları, Claude ile insanın birlikte düzenlediği bir JSON zaman çizelgesidir: insan kod okumak yerine Premiere tarzı bir editör kullanır, Claude da elle yapılan her değişikliği görüp korur.

**Kendi yazı tipimi kullanabilir miyim?** Evet: `.ttf`, `.otf` veya `.ttc` dosyasını editöre sürükle ya da `<proje>/fonts/` içine at.

**Dosyalarım nerede?** Proje klasöründeki `project.json`, `media/`, `fonts/` ve `changes.md`. `.kurgu/` önbellekleri, yedekleri (`project.json`'un son 50 sürümü) ve `state.json`'u tutar.

## Gizlilik

Makinenden hiçbir şey çıkmaz; telemetri, analitik ya da güncelleme kontrolü yoktur. Sunucu yalnızca `127.0.0.1`'e bağlanır; editör hiçbir CDN'den betik, yazı tipi ya da görsel yüklemez; yazı tipleri, önizleme kopyaları, küçük resimler ve render çıktıları proje klasöründe ya da yerel bir önbellek klasöründe kalır (macOS'ta `~/Library/Caches/kurgu`, Linux'ta `$XDG_CACHE_HOME/kurgu` ya da `~/.cache/kurgu`, Windows'ta `%LOCALAPPDATA%\kurgu\Cache`; taşımak için `KURGU_CACHE` ayarla). İki noktayı bilmekte fayda var:

- Bağladığın kodlama ajanı (Claude Code, Codex, Gemini...) kendi sağlayıcısıyla her zamanki gibi konuşur. Kurgu onu yalnızca başlatır ve isteğini ile projeyi ona verir.
- Otomatik altyazı, ilk kullanımda bir konuşma modelini Hugging Face'ten bir kez indirir. Kurgu bunun dışında hiçbir dış bağlantı kurmaz.

## Lisans

MIT, bkz. [LICENSE](LICENSE). Yerleşik yazı tiplerinin OFL lisansları `fonts/licenses/` altındadır. Katkı için: [CONTRIBUTING.md](CONTRIBUTING.md).
