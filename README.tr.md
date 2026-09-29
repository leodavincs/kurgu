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

<!-- screenshot -->

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

Kurgu için Python 3.10+ ve PATH'te ffmpeg (ffprobe dahil) gerekir.

| İşletim sistemi | ffmpeg |
|---|---|
| macOS | `brew install ffmpeg` |
| Debian / Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg`, ya da yönetici izni olmadan: taşınabilir bir sürümü (ör. BtbN `ffmpeg-master-latest-win64-gpl.zip`) istediğin yere aç, `KURGU_FFMPEG` / `KURGU_FFPROBE` değişkenlerini `bin\ffmpeg.exe` / `bin\ffprobe.exe` yoluna ayarla |

Yollardan birini seç. Yeni Python sürümleri sistem Python'una çıplak `pip install` yapmana izin vermez (PEP 668, "externally-managed-environment"); bu yüzden izole bir ortam kullan:

**uvx (kurulum yok, doğrudan git'ten çalışır):** [uv](https://docs.astral.sh/uv/) Python'u ve bağımlılıkları senin yerine getirir.

```bash
uvx --from git+https://github.com/leodavincs/kurgu kurgu proje/klasoru
```

**pipx (kalıcı bir `kurgu` komutu):**

```bash
pipx install git+https://github.com/leodavincs/kurgu
kurgu proje/klasoru              # editör
kurgu-render proje/klasoru       # render
```

**Sanal ortam (klonlayıp geliştirmek için):**

```bash
git clone https://github.com/leodavincs/kurgu.git && cd kurgu
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/check_deps.py
```

`check_deps.py` neyin eksik olduğunu ve nasıl kurulacağını söyler. İsteğe bağlı, otomatik altyazı için (konuşma tanıma tamamen yerelde çalışır, model ilk kullanımda bir kez iner): `pipx inject kurgu faster-whisper` (Apple Silicon'da `mlx-whisper`) ya da sanal ortamının içinde `pip install faster-whisper`.

### Claude Code eklentisi olarak

```
/plugin marketplace add leodavincs/kurgu
/plugin install kurgu@kurgu
```

Eklenti MCP sunucusunu [`uvx`](https://docs.astral.sh/uv/) ile tanıtır; macOS, Linux ve Windows'ta aynı çalışır ve Python bağımlılıklarını kendisi kurar. Önce uv'yi kur (`brew install uv`, `winget install astral-sh.uv` ya da `pipx install uv`). ffmpeg yine gerekir. Claude Code eklenti komutlarını ad alanıyla verir; editör komutu **`/kurgu:kurgu proje/klasoru`** olur (`/kurgu` yazınca tamamlanabilir). uv kullanmak istemezsen Kurgu'yu pipx ile kur ve MCP sunucusunu elle ekle: `claude mcp add kurgu -- kurgu-mcp`.

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

## Diğer ajanlarla kullanım

Kurgu bir MCP sunucusu (`kurgu-mcp`, stdio; `mcp_server.py` ile aynı kod) ile gelir; MCP destekleyen her ajan aynı yetkileri alır: editörü açar, neyi seçtiğini görür, projeyi doğrulanmış toplu değişikliklerle düzenler, işlenmiş kareye bakar, render alır. Repo kökündeki `AGENTS.md`, o dosyayı okuyan ajanlara Kurgu ile nasıl çalışacaklarını anlatır.

Araçlar: `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `list_fonts`, `list_media`, `import_file`. Hepsi isteğe bağlı `project_dir` alır (varsayılan: sunucunun çalışma klasörü ya da `KURGU_PROJECT_DIR` ortam değişkeni). Aşağıdaki örnekler `pipx install git+https://github.com/leodavincs/kurgu` yaptığını varsayar; bu, `kurgu-mcp` komutunu PATH'ine koyar (Windows'ta ajan bulamazsa pipx'in gösterdiği tam yolu yaz). Klondan çalışıyorsan sanal ortamının Python'unu ve `mcp_server.py`'nin tam yolunu kullan.

**Claude Code.** Eklentiyi kur (yukarıya bak). MCP sunucusu eklentinin `.mcp.json` dosyasıyla (`uvx` üzerinden) kendiliğinden tanıtılır; ayar gerekmez. Eklenti olmadan: `claude mcp add kurgu -- kurgu-mcp`.

**Deneysel:** editördeki Ask AI kutusu Claude Code yoksa Codex'e düşer, ama bu yol henüz uçtan uca test edilmedi. Özel ajan komutu yalnızca `KURGU_AGENT_CMD` veya `~/.config/kurgu/config.json` içinden okunur, proje klasöründen asla.

**OpenAI Codex CLI.** `~/.codex/config.toml` dosyasına ekle (ya da `codex mcp add kurgu -- kurgu-mcp` çalıştır):

```toml
[mcp_servers.kurgu]
command = "kurgu-mcp"
tool_timeout_sec = 900   # varsayılan 60 sn; tam render daha uzun sürer
```

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
