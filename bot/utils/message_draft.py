"""
Model data buat draft pesan Components V2 yang dibangun interaktif lewat
/panel (panel builder) atau /announcement (announcement builder). Dua
command itu punya alur & UI kontrol sendiri-sendiri (lihat
bot.ui.panel_builder dan bot.ui.announcement_builder), tapi struktur data
draft-nya dan cara nge-render-nya jadi Components V2 sama persis, jadi
disatuin di sini biar gak duplikat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import discord

from bot.core.theme import COLOR_PRIMARY

PLACEHOLDER_TEXT = "*(Belum ada konten -- pake tombol di bawah buat mulai nambahin.)*"

# Nilai valid buat posisi banner & thumbnail -- dipake draft_from_dict() buat
# nolak nilai nyasar dari JSON lama/rusak (fallback ke default).
THUMBNAIL_POSITIONS = ("title", "description")

# Jumlah slot banner per pesan, dan titik sisip banner yang sifatnya tetap.
# Selain itu ada "line:N" = tepat SETELAH baris teks ke-N (lihat
# normalize_banner_position / banner_slot_choices di bawah).
BANNER_COUNT = 2
FIXED_BANNER_POSITIONS = ("top", "title", "header", "bottom")


@dataclass
class TextBlock:
    content: str


@dataclass
class SeparatorBlock:
    pass


Block = TextBlock | SeparatorBlock


@dataclass
class ButtonSpec:
    """Satu tombol di ActionRow -- dua tipe: tombol LINK (`url` diisi,
    Discord buka link-nya, bot gak pernah dapet interaction) atau tombol
    REPLY (`reply_button_id` diisi, nunjuk ke row di tabel
    panel_reply_buttons, klik-nya beneran masuk ke bot dan balas pesan).
    Cuma satu dari dua yang keisi -- gak ada yang isi keduanya."""

    label: str
    emoji: str | None = None
    url: str | None = None
    reply_button_id: int | None = None

    @property
    def is_link(self) -> bool:
        return self.url is not None


def normalize_banner_position(value) -> str:
    """Posisi banner valid = salah satu dari:
      "top"    paling atas, sebelum judul
      "title"  di bawah judul, sebelum deskripsi
      "header" di bawah judul & deskripsi, sebelum baris teks
      "line:N" tepat setelah baris teks ke-N (N >= 1)
      "bottom" paling bawah, setelah semua baris teks
    Nilai lain (data lama/rusak) jatuh ke "top"."""
    if value in FIXED_BANNER_POSITIONS:
        return value
    if isinstance(value, str) and value.startswith("line:"):
        number = value[5:]
        if number.isdigit() and int(number) >= 1:
            return f"line:{int(number)}"
    return "top"


@dataclass
class BannerSpec:
    """Satu slot banner. `url` kosong = slot belum diisi; `enabled` bisa
    dimatiin tanpa ngilangin URL-nya (buat on/off cepat). Cuma banner yang
    enabled DAN punya url yang kerender."""

    url: str | None = None
    enabled: bool = True
    position: str = "top"


def _default_banners() -> list[BannerSpec]:
    # Banner 1 default di atas, banner 2 default di bawah -- supaya kalau
    # dua-duanya diisi langsung kebagi dua ujung, bukan numpuk.
    return [BannerSpec(position="top"), BannerSpec(position="bottom")]


@dataclass
class MessageDraft:
    """State kerja satu pesan yang lagi dibangun. Semuanya optional/kosong
    di awal -- draft kosong dirender sebagai placeholder biar Container-nya
    gak pernah beneran kosong (Discord nolak Container tanpa isi).

    Tata letak FLEKSIBEL, diatur staff lewat tombol toggle di builder
    (lihat bot.ui.draft_builder_base):
      * `banners`: DUA slot banner, masing-masing bisa di-on/off dan
        diposisiin bebas (paling atas, di bawah judul, di bawah judul &
        deskripsi, setelah baris ke-N, atau paling bawah) -- lihat
        BannerSpec & normalize_banner_position().
      * `thumbnail_position`: "description" (default, sejajar di kanan
        deskripsi) atau "title" (sejajar di kanan judul). Kalau elemen
        yang dipilih gak ada isinya, thumbnail otomatis pindah ke elemen
        teks terdekat biar tetep kerender.
      * `title_description_separator`: True = ada garis pemisah antara
        judul & deskripsi (cuma muncul kalau dua-duanya keisi). Default
        off (nyambung)."""

    title: str | None = None
    description: str | None = None
    title_description_separator: bool = False
    blocks: list[Block] = field(default_factory=list)  # hasil "Add Line" + "Insert separator"
    thumbnail_url: str | None = None
    thumbnail_position: str = "description"  # "title" | "description"
    banners: list[BannerSpec] = field(default_factory=_default_banners)
    color: int = COLOR_PRIMARY
    buttons: list[ButtonSpec] = field(default_factory=list)

    def copy(self) -> "MessageDraft":
        """Deep-enough copy buat snapshot undo history -- list/dataclass di
        dalemnya di-copy juga, bukan di-share reference-nya."""
        return MessageDraft(
            title=self.title,
            description=self.description,
            title_description_separator=self.title_description_separator,
            blocks=[
                TextBlock(b.content) if isinstance(b, TextBlock) else SeparatorBlock()
                for b in self.blocks
            ],
            thumbnail_url=self.thumbnail_url,
            thumbnail_position=self.thumbnail_position,
            banners=[BannerSpec(b.url, b.enabled, b.position) for b in self.banners],
            color=self.color,
            buttons=[ButtonSpec(b.label, b.emoji, b.url, b.reply_button_id) for b in self.buttons],
        )

    def line_count(self) -> int:
        return sum(1 for b in self.blocks if isinstance(b, TextBlock))


def effective_banner_position(draft: MessageDraft, spec: BannerSpec) -> str:
    """Posisi banner yang BENERAN dipake renderer: "line:N" yang N-nya
    lebih besar dari jumlah baris teks sekarang (misal baris dihapus)
    dianggap "bottom" biar banner gak ilang."""
    key = normalize_banner_position(spec.position)
    if key.startswith("line:") and int(key[5:]) > draft.line_count():
        return "bottom"
    return key


def banner_slot_choices(draft: MessageDraft) -> list[tuple[str, str]]:
    """Daftar (value, label) titik sisip banner buat Select di builder --
    ngikutin isi draft sekarang (satu titik per baris teks). Maksimal 25
    (batas Select Discord), "Paling bawah" selalu ikut."""
    fixed = [
        ("top", "Paling atas"),
        ("title", "Di bawah judul (sebelum deskripsi)"),
        ("header", "Di bawah judul & deskripsi"),
    ]
    lines: list[tuple[str, str]] = []
    number = 0
    for blk in draft.blocks:
        if isinstance(blk, TextBlock):
            number += 1
            preview = blk.content.replace("\n", " ")[:50]
            lines.append((f"line:{number}", f"Setelah baris {number}: {preview}"))
    return fixed + lines[:21] + [("bottom", "Paling bawah")]


def draft_to_dict(draft: MessageDraft) -> dict:
    """Serialize MessageDraft ke dict siap json.dumps -- dipake
    bot.ui.panel_builder buat nyimpen draft ke tabel panel_drafts biar
    bisa dilanjutin edit belakangan, lewat sesi/proses bot manapun."""
    return {
        "title": draft.title,
        "description": draft.description,
        "title_description_separator": draft.title_description_separator,
        "blocks": [
            {"type": "text", "content": b.content} if isinstance(b, TextBlock) else {"type": "separator"}
            for b in draft.blocks
        ],
        "thumbnail_url": draft.thumbnail_url,
        "thumbnail_position": draft.thumbnail_position,
        "banners": [{"url": b.url, "enabled": b.enabled, "position": b.position} for b in draft.banners],
        "color": draft.color,
        "buttons": [
            {"label": b.label, "emoji": b.emoji, "url": b.url, "reply_button_id": b.reply_button_id}
            for b in draft.buttons
        ],
    }


def draft_from_dict(data: dict) -> MessageDraft:
    """Kebalikan draft_to_dict() -- rekonstruksi MessageDraft dari dict
    hasil json.loads(). Field yang gak ada di data (misal draft lama dari
    versi skema yang beda) di-default ke kosong, bukan KeyError."""
    blocks: list[Block] = []
    for b in data.get("blocks", []):
        if b.get("type") == "separator":
            blocks.append(SeparatorBlock())
        else:
            blocks.append(TextBlock(b.get("content", "")))
    buttons = [
        ButtonSpec(
            label=b["label"], emoji=b.get("emoji"), url=b.get("url"), reply_button_id=b.get("reply_button_id")
        )
        for b in data.get("buttons", [])
    ]
    # Draft lama (sebelum fitur tata letak fleksibel) gak punya tiga field
    # ini -- jatuh ke default. Nilai di luar daftar valid juga di-default-in.
    # Banner: format baru = list "banners" (maks BANNER_COUNT slot). Draft
    # lama cuma punya satu `banner_url` + `banner_position` -- itu jadi
    # banner 1, slot sisanya diisi default.
    raw_banners = data.get("banners")
    banners: list[BannerSpec] = []
    if isinstance(raw_banners, list):
        for b in raw_banners[:BANNER_COUNT]:
            if isinstance(b, dict):
                banners.append(
                    BannerSpec(
                        url=b.get("url") or None,
                        enabled=bool(b.get("enabled", True)),
                        position=normalize_banner_position(b.get("position", "top")),
                    )
                )
    else:
        banners.append(
            BannerSpec(
                url=data.get("banner_url") or None,
                enabled=True,
                position=normalize_banner_position(data.get("banner_position", "top")),
            )
        )
    defaults = _default_banners()
    while len(banners) < BANNER_COUNT:
        banners.append(defaults[len(banners)])
    thumbnail_position = data.get("thumbnail_position", "description")
    if thumbnail_position not in THUMBNAIL_POSITIONS:
        thumbnail_position = "description"

    return MessageDraft(
        title=data.get("title"),
        description=data.get("description"),
        title_description_separator=bool(data.get("title_description_separator", False)),
        blocks=blocks,
        thumbnail_url=data.get("thumbnail_url"),
        thumbnail_position=thumbnail_position,
        banners=banners,
        color=data.get("color", COLOR_PRIMARY),
        buttons=buttons,
    )


def _group_blocks(blocks: list[Block]) -> list[list[TextBlock]]:
    """Pecah `blocks` jadi beberapa grup teks, dipisah tiap ketemu
    SeparatorBlock -- dipake buat render maupun buat nentuin titik sisip
    separator baru."""
    groups: list[list[TextBlock]] = [[]]
    for blk in blocks:
        if isinstance(blk, SeparatorBlock):
            groups.append([])
        else:
            groups[-1].append(blk)
    return groups


def _banner_slots(draft: MessageDraft) -> dict[str, list[str]]:
    """Titik sisip -> daftar URL banner yang kerender di situ (urutan
    banner 1 dulu baru 2). Cuma banner enabled + punya url yang ikut."""
    slots: dict[str, list[str]] = {}
    for spec in draft.banners:
        if spec.enabled and spec.url:
            slots.setdefault(effective_banner_position(draft, spec), []).append(spec.url)
    return slots


def _tidy_separators(entries: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Beresin pemisah OTOMATIS ("sep_auto", yang nemplok di sekitar
    banner): dibuang kalau ada di ujung, atau udah ada pemisah di sebelahnya
    -- pemisah yang staff sisipin sendiri ("sep") selalu menang dan gak
    pernah dibuang."""
    out: list[tuple[str, str | None]] = []
    for kind, payload in entries:
        if kind == "sep_auto":
            if not out or out[-1][0] in ("sep", "sep_auto"):
                continue
        elif kind == "sep" and out and out[-1][0] == "sep_auto":
            out.pop()
        out.append((kind, payload))
    while out and out[-1][0] == "sep_auto":
        out.pop()
    return out


def render_draft_container(draft: MessageDraft) -> discord.ui.Container:
    """Tata letak (urutan dari atas ke bawah), banner bisa nongol di titik
    sisip manapun (lihat normalize_banner_position):

      [banner "top"] -> judul -> [banner "title"] -> [pemisah judul/
      deskripsi kalau toggle on] -> deskripsi -> [banner "header"] ->
      baris teks (dipisah separator; banner "line:N" nyelip tepat setelah
      baris ke-N) -> [banner "bottom"].

    Dua banner di titik yang sama tampil berurutan (banner 1 dulu).
    Banner otomatis diapit pemisah tipis kecuali di ujung konten.

    Thumbnail nempel di KANAN teks lewat discord.ui.Section (accessory
    Section emang selalu kerender di sisi kanan teksnya): di deskripsi
    kalau thumbnail_position="description" (default), di judul kalau
    "title". Kalau elemen targetnya kosong, thumbnail pindah ke elemen
    teks terdekat (judul/deskripsi yang satunya, terus baris teks
    pertama) -- Section butuh minimal satu TextDisplay."""
    slots = _banner_slots(draft)
    # Entry = (jenis, isi): title/desc/body = teks, sep = pemisah dari staff,
    # sep_auto = pemisah otomatis di sekitar banner, banner = URL gambar,
    # thumbonly = Section placeholder buat thumbnail tanpa teks sama sekali.
    entries: list[tuple[str, str | None]] = []

    def add_banners(key: str) -> None:
        for url in slots.get(key, []):
            entries.append(("sep_auto", None))
            entries.append(("banner", url))
            entries.append(("sep_auto", None))

    add_banners("top")

    has_text = bool(draft.title or draft.description or draft.line_count())
    if draft.thumbnail_url and not has_text:
        entries.append(("thumbonly", None))

    if draft.title:
        entries.append(("title", f"## {draft.title}"))
    add_banners("title")
    if draft.description:
        if draft.title and draft.title_description_separator:
            entries.append(("sep", None))
        entries.append(("desc", draft.description))
    add_banners("header")

    pending: list[str] = []
    seen_lines = 0

    def flush() -> None:
        if pending:
            entries.append(("body", "\n".join(pending)))
            pending.clear()

    for blk in draft.blocks:
        if isinstance(blk, SeparatorBlock):
            flush()
            entries.append(("sep", None))
        else:
            pending.append(blk.content)
            seen_lines += 1
            if f"line:{seen_lines}" in slots:
                flush()
                add_banners(f"line:{seen_lines}")
    flush()
    add_banners("bottom")

    entries = _tidy_separators(entries)

    # Tentuin entry yang kebagian thumbnail (urutan prioritas ngikutin
    # thumbnail_position, terus fallback ke baris teks pertama).
    thumb_index: int | None = None
    if draft.thumbnail_url:
        preferred = ("desc", "title") if draft.thumbnail_position == "description" else ("title", "desc")
        for kind in (*preferred, "body"):
            thumb_index = next((i for i, (k, _) in enumerate(entries) if k == kind), None)
            if thumb_index is not None:
                break

    def _separator() -> discord.ui.Separator:
        return discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)

    def _thumbnail() -> discord.ui.Thumbnail:
        return discord.ui.Thumbnail(media=draft.thumbnail_url)

    children: list = []
    for i, (kind, payload) in enumerate(entries):
        if kind in ("sep", "sep_auto"):
            children.append(_separator())
        elif kind == "banner":
            children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=payload)))
        elif kind == "thumbonly":
            # Gak ada teks sama sekali buat ditempelin -- Section tetep
            # butuh satu TextDisplay, jadi pake placeholder tak-terlihat
            # (zero-width space) biar strukturnya valid.
            children.append(discord.ui.Section(discord.ui.TextDisplay("\u200b"), accessory=_thumbnail()))
        elif i == thumb_index:
            children.append(discord.ui.Section(discord.ui.TextDisplay(payload), accessory=_thumbnail()))
        else:
            children.append(discord.ui.TextDisplay(payload))

    if not children:
        children.append(discord.ui.TextDisplay(PLACEHOLDER_TEXT))

    return discord.ui.Container(*children, accent_colour=draft.color)


def render_draft_action_row(draft: MessageDraft) -> discord.ui.ActionRow | None:
    """ActionRow berisi tombol yang ditambahin lewat "Add Link Button" /
    "Add Reply Button". Return None kalau belum ada tombol -- caller yang
    mutusin mau nempelin ke Container atau skip sama sekali."""
    if not draft.buttons:
        return None
    row = discord.ui.ActionRow()
    for b in draft.buttons[:5]:  # ActionRow maksimal 5 komponen
        if b.is_link:
            row.add_item(
                discord.ui.Button(label=b.label[:80], style=discord.ButtonStyle.link, url=b.url, emoji=b.emoji)
            )
        else:
            # Tombol Reply -- custom_id-nya harus PERSIS format yang
            # dikenalin PanelReplyButton (lihat bot.ui.panel_reply_button)
            # biar bot bisa nangkep klik-nya dan tetep jalan abis restart.
            row.add_item(
                discord.ui.Button(
                    label=b.label[:80], style=discord.ButtonStyle.secondary, emoji=b.emoji,
                    custom_id=f"noctra:panelbtn:{b.reply_button_id}",
                )
            )
    return row


def render_draft_layout(draft: MessageDraft) -> discord.ui.LayoutView:
    """Bungkus draft jadi LayoutView siap kirim/edit lewat `view=...`."""

    class _DraftLayout(discord.ui.LayoutView):
        def __init__(self) -> None:
            super().__init__(timeout=None)
            container = render_draft_container(draft)
            action_row = render_draft_action_row(draft)
            if action_row is not None:
                container.add_item(action_row)
            self.add_item(container)

    return _DraftLayout()


def render_draft_preview_embed(draft: MessageDraft) -> discord.Embed:
    """Preview APPROX pake Embed biasa -- dipake Announcement Builder biar
    staff bisa liat progress draft secara live TANPA harus posting apapun
    ke channel tujuan dulu (beda sama Panel Builder, yang emang udah punya
    pesan asli buat langsung di-refresh live). Ini BUKAN hasil akhir --
    begitu beneran dikirim, isinya dirender ulang penuh pake Components V2
    lewat render_draft_layout(), jadi bisa aja ada beda tampilan dikit."""
    from bot.core.theme import COLOR_MUTED

    lines: list[str] = []
    if draft.description:
        if draft.title and draft.title_description_separator:
            lines.append("⸻")
        lines.append(draft.description)
    groups = _group_blocks(draft.blocks)
    for i, group in enumerate(groups):
        if i > 0:
            lines.append("⸻")
        lines.extend(b.content for b in group)
    description = "\n".join(lines) if lines else PLACEHOLDER_TEXT

    embed = discord.Embed(title=draft.title or None, description=description, color=draft.color or COLOR_MUTED)
    if draft.thumbnail_url:
        embed.set_thumbnail(url=draft.thumbnail_url)
    # Embed cuma bisa satu gambar besar -- preview nampilin banner aktif
    # yang PERTAMA; dua-duanya baru kelihatan di hasil akhir (Components V2).
    active_banners = [b for b in draft.banners if b.enabled and b.url]
    if active_banners:
        embed.set_image(url=active_banners[0].url)
    if draft.buttons:
        parts = []
        for b in draft.buttons:
            prefix = f"{b.emoji} " if b.emoji else ""
            if b.is_link:
                parts.append(f"{prefix}[{b.label}]({b.url})")
            else:
                parts.append(f"{prefix}**{b.label}** _(balasan)_")
        embed.add_field(name="Tombol", value=", ".join(parts), inline=False)
    footer = "Preview -- tampilan akhir bisa beda dikit (dirender pake Components V2)"
    if len(active_banners) > 1:
        footer = "Preview -- cuma nampilin banner 1, hasil akhir nampilin dua-duanya (Components V2)"
    embed.set_footer(text=footer)
    return embed
