#!/usr/bin/env python3
"""
Folder Cleanup 2 - organize a folder into tidy subfolders, safely.

Double-click to open (Python 3 must be installed).

Flow:   1. Choose a folder   2. Scan + review every change   3. Done (with undo)

Safety rules
  - Nothing is ever deleted and nothing is ever overwritten.
  - Nothing moves until you press "Organize files" on the review screen.
  - Every move is written to a journal the moment it happens, so even after a
    crash or power cut, "Undo" can put every file back.
  - Folders, hidden files and system files are never touched.

Shares its undo log with cleanup.py (the command-line version).
"""

import hashlib
import html
import json
import os
import queue
import re
import shutil
import site
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:  # optional: photo previews for JPG/WEBP
    from PIL import Image, ImageTk
except Exception:  # noqa
    Image = ImageTk = None

# =============================================================================
# Configuration
# =============================================================================

CATEGORIES = {
    "Images":        {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".tiff", ".ico", ".avif"},
    "Documents":     {".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf", ".md", ".pages", ".epub"},
    "Spreadsheets":  {".xls", ".xlsx", ".csv", ".ods", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Video":         {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".m4v"},
    "Audio":         {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma"},
    "Archives":      {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Installers":    {".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg", ".deb", ".appimage"},
    "Fonts":         {".ttf", ".otf", ".woff", ".woff2", ".fon"},
    "Code":          {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".xml", ".html", ".htm",
                      ".css", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".php", ".rb", ".sh",
                      ".bat", ".cmd", ".ps1", ".lua", ".sql", ".ini", ".cfg", ".properties", ".ipynb"},
}
MINECRAFT = "Minecraft"
MC_EXTS = {".jar", ".mcworld", ".mcpack", ".mcaddon", ".mctemplate", ".mrpack", ".litematic",
           ".schem", ".schematic", ".nbt"}
MC_CONTAINERS = {".zip", ".rar", ".7z"}
MC_WORDS = ("minecraft", "modpack", "resourcepack", "resource-pack", "resource_pack", "resource pack",
            "texturepack", "texture-pack", "shaderpack", "shaders", "optifine", "fabric", "forge",
            "curseforge", "modrinth", "bukkit", "spigot", "paper-", "datapack", "litematica", "mcworld")
OTHER = "Other"
DUPLICATES_DIR = "_Duplicates"
BUILTIN_GROUPS = list(CATEGORIES) + [MINECRAFT, OTHER]

LOG_NAME = ".cleanup_log.json"
JOURNAL_NAME = ".cleanup_journal.jsonl"
OLD_LOG_NAME = ".opruim_log.json"
SETTINGS_FILE = Path.home() / ".folder_cleanup_settings.json"
SKIP_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp"}  # unfinished downloads
SKIP_NAMES = {"desktop.ini", "thumbs.db", LOG_NAME, JOURNAL_NAME, OLD_LOG_NAME}
BAD_FOLDER_CHARS = set('\\/:*?"<>|')

DEFAULT_SETTINGS = {
    "groups": {**{g: True for g in CATEGORIES}, MINECRAFT: False, OTHER: False},
    "duplicates": True,
    "conflicts": "keep",        # keep | skip
    "skip_recent": False,
    "skip_large": False,
    "large_gb": 10,
    "rules": [],                # [{"ext": ".psd", "folder": "Design"}]
    "lang": None,
}

# Icons: Windows' own icon font (Segoe Fluent Icons / MDL2), with plain fallbacks elsewhere.
ICONS = {
    DUPLICATES_DIR: ("", "⧉"), "Images": ("", "▣"),
    "Documents": ("", "≡"), "Spreadsheets": ("", "▦"),
    "Presentations": ("", "▭"), "Video": ("", "▶"),
    "Audio": ("", "♫"), "Archives": ("", "▤"),
    "Installers": ("", "⬇"), "Fonts": ("", "A"), "Code": ("", "‹›"),
    MINECRAFT: ("", "▩"), OTHER: ("", "…"),
    "rule": ("", "#"), "folder": ("", "▢"), "search": ("", "⌕"),
    "settings": ("", "⚙"), "back": ("", "←"),
    "forward": ("", "→"), "check": ("", "✓"),
    "shield": ("", "✓"), "undo": ("", "↶"),
    "open": ("", "↗"), "warning": ("", "!"), "bulb": ("", "★"),
    "close": ("", "×"), "add": ("", "+"), "clock": ("", "◷"),
    "size": ("", "▤"),
}

# Windows 11-style palette
BG = "#f3f3f3"
CARD = "#ffffff"
BORDER = "#e3e3e3"
DIVIDER = "#ededed"
INK = "#1b1b1b"
MUTED = "#616161"
FAINT = "#8a8a8a"
ACCENT = "#2f6fdf"
ACCENT_HOVER = "#2a62c6"
ACCENT_SOFT = "#e9f0fc"
GREEN = "#0f7b3f"
GREEN_SOFT = "#e6f4ea"
AMBER = "#8a5300"
AMBER_SOFT = "#fff4ce"
DISABLED = "#c5c5c5"

# =============================================================================
# Text (English + Dutch)
# =============================================================================

TEXT = {
    "en": {
        "app": "Folder Cleanup",
        "steps": ["Choose", "Review", "Done"],
        "step_of": "Step {n} of 3",
        "settings_tip": "Settings",
        # choose
        "c_title": "Organize your files",
        "c_sub": "Folder Cleanup scans a folder and groups your files into clear folders such as "
                 "Images, Documents and Videos. You always see what will change before anything is moved.",
        "change": "Change folder",
        "what": "What will happen?",
        "what_items": ["Files are grouped into folders by type",
                       "Exact duplicates are set aside so you can check them",
                       "You review every change before anything moves",
                       "Nothing is ever deleted or overwritten — and you can undo it all"],
        "scan": "Scan folder",
        "undo_last": "Undo last cleanup ({n} files, {date})",
        # scanning
        "s_title": "Scanning {name}…",
        "s_sub": "Looking through your files",
        "s_found": "{n} files found · {size}",
        "s_dups": "Checking for duplicates",
        # review
        "r_title": "Your folder is ready to organize",
        "r_sub": "We found {n} files ({size}) that can be organized. Untick anything you'd rather leave where it is.",
        "r_none_title": "Nothing to organize",
        "r_none": "This folder already looks tidy. You can change which file types are included in Settings.",
        "r_safe_title": "Safe to organize",
        "r_safe": "Nothing will be deleted or overwritten. You can undo all changes afterwards.",
        "r_attention_title": "{n} files need your attention",
        "r_attention_title_1": "1 file needs your attention",
        "r_attention": "A file with the same name already exists in the destination folder. "
                       "You'll choose what happens to each one in the next step.",
        "r_selected": "{n} files selected · {size}",
        "r_selected_1": "1 file selected · {size}",
        "r_sub_1": "We found 1 file ({size}) that can be organized. Untick it if you'd rather leave it where it is.",
        "r_skipped": "{n} files were left out by your settings (recent or very large files).",
        "sug_mc": "{n} files look like Minecraft files (mods, worlds, resource packs).",
        "sug_mc_btn": "Create “Minecraft” folder",
        "sug_other": "{n} files don't fit any category, so they'll stay where they are.",
        "sug_other_1": "1 file doesn't fit any category, so it will stay where it is.",
        "sug_other_btn": "Put them in “Other”",
        "files": "{n} files",
        "file": "1 file",
        "dup_desc": "Exact copies of files you already have. The original stays put.",
        "other_desc": "Files that don't fit any other group",
        "rule_desc": "Your own rule",
        "back": "Back",
        "review": "Review changes",
        # preview
        "p_title": "Review changes",
        "p_sub": "Click any file to see what's in it, or to leave it where it is.",
        "p_search": "Search files…",
        "p_show": "Show",
        "f_all": "All files",
        "f_attention": "Needs attention",
        "f_staying": "Staying where they are",
        "p_count": "{n} files will be moved · {m} stay where they are",
        "p_nomatch": "No files match your search.",
        "copy_of": "copy of {name}",
        "tag_conflict": "name already exists",
        "tag_stays": "stays here",
        "safe": "Nothing will be deleted or overwritten. You can undo everything afterwards.",
        "organize": "Organize files",
        "working": "Organizing…",
        # viewer
        "v_pick": "Click a file on the left to see what's in it.",
        "v_moves": "Moves to",
        "v_stays": "Stays where it is",
        "v_size": "Size",
        "v_changed": "Last changed",
        "v_copy_of": "Copy of",
        "v_identical": "Identical content — checked byte by byte",
        "v_move_this": "Move this file",
        "v_conflict": "A file called “{name}” already exists in {folder}/.",
        "v_conflict_same": "It has exactly the same content.",
        "v_keep": "Keep both",
        "v_keep_sub": "This one is saved as “{name}”",
        "v_skip": "Skip",
        "v_skip_sub": "Leave this file where it is",
        "v_compare": "Open the existing file",
        "v_open": "Open",
        "v_show": "Show in folder",
        "v_open_orig": "Open original",
        "v_none": "No preview for this type of file. Click “Open” to look at it.",
        "v_pillow": "Previews of JPG/WEBP photos need a small free add-on (Pillow).",
        "v_install": "Turn on photo previews",
        "v_installing": "Installing… this takes about half a minute.",
        "v_install_fail": "That didn't work automatically. Open a terminal and type:  pip install pillow",
        "v_zip": "Contains {n} items:",
        "v_more": "…and {n} more",
        "v_empty": "(empty)",
        # done
        "d_title": "All cleaned up!",
        "d_sub": "Your {name} folder is now organized.",
        "d_moved": "Moved",
        "d_size": "Organized",
        "d_cats": "Categories",
        "d_tip": "“_Duplicates” only holds copies. Check it, then delete it yourself to free up {size}.",
        "d_skipped": "{n} files were not moved",
        "d_skipped_1": "1 file was not moved",
        "d_view": "View",
        "d_undo": "Undo changes",
        "open": "Open folder",
        "finish": "Done",
        "sk_title": "Files that were not moved",
        "sk_name": "File",
        "sk_reason": "Reason",
        "why_conflict": "Name already existed — you chose Skip",
        "why_excluded": "You chose to leave it",
        "why_missing": "The file was gone",
        "why_recent": "Changed in the last 24 hours",
        "why_large": "Larger than {n} GB",
        "why_error": "Couldn't move: {err}",
        "close": "Close",
        # undo
        "undo_title": "Undo organization?",
        "undo_q": "{n} files will be moved back to their original locations.\n\nNothing will be deleted.",
        "undo_done": "Everything has been restored.",
        "undo_missing": "{n} files could not be found (perhaps you deleted them) and were skipped.",
        "undo_renamed": "{n} files got a new name like “file (2)” because the old spot was taken.",
        "no_undo": "There's nothing to undo.",
        # settings
        "settings": "Settings",
        "set_sub": "Choose what Folder Cleanup organizes. Changes are saved automatically.",
        "set_cats": "Categories",
        "mc_desc": "Mods (.jar), worlds, modpacks, resource packs, shaders",
        "set_rules": "Your own rules",
        "set_rules_sub": "Send a file type to a folder of your choice, e.g. .psd → Design.",
        "set_rules_none": "No rules yet.",
        "set_rule_ext": "Extension",
        "set_rule_folder": "Folder",
        "set_rule_add": "Add rule",
        "set_rule_bad_ext": "Type an extension like  .psd",
        "set_rule_bad_folder": "Type a folder name without these characters:  \\ / : * ? \" < > |",
        "set_dups_title": "Duplicates & name clashes",
        "set_dups": "Find exact duplicates",
        "set_dups_sub": "Copies go to “_Duplicates”; the original stays.",
        "set_clash": "When a file with the same name already exists",
        "set_clash_keep": "Keep both (adds a number to the name)",
        "set_clash_skip": "Skip it (leave the file where it is)",
        "set_excl": "Leave these alone",
        "set_recent": "Files changed in the last 24 hours",
        "set_recent_sub": "Handy for files you're still downloading or working on.",
        "set_large": "Files larger than",
        "set_large_unit": "GB",
        "set_folders": "Folders",
        "set_hidden": "Hidden and system files",
        "set_safety": "Safety",
        "set_never": "Files are never deleted or overwritten",
        "set_preview": "You always see a preview first",
        "set_history": "Every change is recorded so you can undo it",
        "always_on": "Always on",
        "lang_btn": "Nederlands",
        "not_found": "That folder can't be found.",
        "error": "Something went wrong:\n{err}\n\nAny files already moved can still be undone.",
        "names": {DUPLICATES_DIR: "Duplicate copies", "Images": "Images", "Documents": "Documents",
                  "Spreadsheets": "Spreadsheets", "Presentations": "Presentations", "Video": "Videos",
                  "Audio": "Music & audio", "Archives": "Zip files & archives",
                  "Installers": "Installers", "Fonts": "Fonts", "Code": "Code & config files",
                  MINECRAFT: "Minecraft", OTHER: "Other files"},
    },
    "nl": {
        "app": "Map Opruimen",
        "steps": ["Kiezen", "Controleren", "Klaar"],
        "step_of": "Stap {n} van 3",
        "settings_tip": "Instellingen",
        "c_title": "Ruim je bestanden op",
        "c_sub": "Map Opruimen bekijkt een map en zet je bestanden in duidelijke mappen, zoals "
                 "Images, Documents en Video. Je ziet altijd eerst wat er verandert voordat er iets wordt verplaatst.",
        "change": "Andere map",
        "what": "Wat gaat er gebeuren?",
        "what_items": ["Bestanden worden per soort in mappen gezet",
                       "Exacte dubbele bestanden worden apart gezet, zodat je ze kunt nakijken",
                       "Je controleert elke wijziging voordat er iets verplaatst wordt",
                       "Er wordt nooit iets verwijderd of overschreven — en alles is terug te zetten"],
        "scan": "Map scannen",
        "undo_last": "Laatste opruimactie terugzetten ({n} bestanden, {date})",
        "s_title": "{name} wordt gescand…",
        "s_sub": "Je bestanden worden bekeken",
        "s_found": "{n} bestanden gevonden · {size}",
        "s_dups": "Zoeken naar dubbele bestanden",
        "r_title": "Je map is klaar om op te ruimen",
        "r_sub": "We vonden {n} bestanden ({size}) die opgeruimd kunnen worden. Vink uit wat je liever laat staan.",
        "r_none_title": "Niets op te ruimen",
        "r_none": "Deze map ziet er al netjes uit. Bij Instellingen kies je welke soorten bestanden meedoen.",
        "r_safe_title": "Veilig om op te ruimen",
        "r_safe": "Er wordt niets verwijderd of overschreven. Je kunt alles achteraf terugzetten.",
        "r_attention_title": "{n} bestanden hebben je aandacht nodig",
        "r_attention_title_1": "1 bestand heeft je aandacht nodig",
        "r_attention": "In de doelmap staat al een bestand met dezelfde naam. "
                       "In de volgende stap kies je per bestand wat er gebeurt.",
        "r_selected": "{n} bestanden geselecteerd · {size}",
        "r_selected_1": "1 bestand geselecteerd · {size}",
        "r_sub_1": "We vonden 1 bestand ({size}) dat opgeruimd kan worden. Vink het uit als je het liever laat staan.",
        "r_skipped": "{n} bestanden zijn overgeslagen door je instellingen (recent of erg groot).",
        "sug_mc": "{n} bestanden lijken Minecraft-bestanden (mods, werelden, resource packs).",
        "sug_mc_btn": "Map “Minecraft” maken",
        "sug_other": "{n} bestanden passen in geen enkele categorie en blijven dus staan.",
        "sug_other_1": "1 bestand past in geen enkele categorie en blijft dus staan.",
        "sug_other_btn": "Zet ze in “Other”",
        "files": "{n} bestanden",
        "file": "1 bestand",
        "dup_desc": "Exacte kopieën van bestanden die je al hebt. Het origineel blijft staan.",
        "other_desc": "Bestanden die nergens anders bij passen",
        "rule_desc": "Je eigen regel",
        "back": "Terug",
        "review": "Wijzigingen controleren",
        "p_title": "Wijzigingen controleren",
        "p_sub": "Klik op een bestand om te zien wat erin zit, of om het te laten staan.",
        "p_search": "Bestanden zoeken…",
        "p_show": "Toon",
        "f_all": "Alle bestanden",
        "f_attention": "Aandacht nodig",
        "f_staying": "Blijven staan",
        "p_count": "{n} bestanden worden verplaatst · {m} blijven staan",
        "p_nomatch": "Geen bestanden gevonden.",
        "copy_of": "kopie van {name}",
        "tag_conflict": "naam bestaat al",
        "tag_stays": "blijft staan",
        "safe": "Er wordt niets verwijderd of overschreven. Je kunt alles achteraf terugzetten.",
        "organize": "Bestanden opruimen",
        "working": "Bezig met opruimen…",
        "v_pick": "Klik links op een bestand om te zien wat erin zit.",
        "v_moves": "Gaat naar",
        "v_stays": "Blijft staan",
        "v_size": "Grootte",
        "v_changed": "Laatst gewijzigd",
        "v_copy_of": "Kopie van",
        "v_identical": "Precies dezelfde inhoud — byte voor byte gecontroleerd",
        "v_move_this": "Dit bestand verplaatsen",
        "v_conflict": "Er staat al een bestand “{name}” in {folder}/.",
        "v_conflict_same": "Het heeft precies dezelfde inhoud.",
        "v_keep": "Beide houden",
        "v_keep_sub": "Deze wordt opgeslagen als “{name}”",
        "v_skip": "Overslaan",
        "v_skip_sub": "Laat dit bestand staan waar het staat",
        "v_compare": "Bestaand bestand openen",
        "v_open": "Openen",
        "v_show": "Toon in map",
        "v_open_orig": "Origineel openen",
        "v_none": "Geen voorbeeld voor dit soort bestand. Klik op “Openen” om het te bekijken.",
        "v_pillow": "Voor voorbeelden van JPG/WEBP-foto's is een kleine gratis uitbreiding nodig (Pillow).",
        "v_install": "Fotovoorbeelden aanzetten",
        "v_installing": "Bezig met installeren… dit duurt ongeveer een halve minuut.",
        "v_install_fail": "Dat lukte niet automatisch. Open een terminal en typ:  pip install pillow",
        "v_zip": "Bevat {n} onderdelen:",
        "v_more": "…en nog {n}",
        "v_empty": "(leeg)",
        "d_title": "Helemaal opgeruimd!",
        "d_sub": "Je map {name} is nu netjes georganiseerd.",
        "d_moved": "Verplaatst",
        "d_size": "Opgeruimd",
        "d_cats": "Categorieën",
        "d_tip": "“_Duplicates” bevat alleen kopieën. Kijk ze na en verwijder de map zelf om {size} vrij te maken.",
        "d_skipped": "{n} bestanden zijn niet verplaatst",
        "d_skipped_1": "1 bestand is niet verplaatst",
        "d_view": "Bekijken",
        "d_undo": "Wijzigingen terugzetten",
        "open": "Map openen",
        "finish": "Klaar",
        "sk_title": "Bestanden die niet zijn verplaatst",
        "sk_name": "Bestand",
        "sk_reason": "Reden",
        "why_conflict": "Naam bestond al — je koos Overslaan",
        "why_excluded": "Je koos om het te laten staan",
        "why_missing": "Het bestand was er niet meer",
        "why_recent": "Gewijzigd in de afgelopen 24 uur",
        "why_large": "Groter dan {n} GB",
        "why_error": "Kon niet verplaatsen: {err}",
        "close": "Sluiten",
        "undo_title": "Opruimen terugzetten?",
        "undo_q": "{n} bestanden worden teruggezet op hun oude plek.\n\nEr wordt niets verwijderd.",
        "undo_done": "Alles is teruggezet.",
        "undo_missing": "{n} bestanden waren niet meer te vinden (misschien heb je ze verwijderd) en zijn overgeslagen.",
        "undo_renamed": "{n} bestanden kregen een nieuwe naam zoals “bestand (2)” omdat hun oude plek bezet was.",
        "no_undo": "Er is niets om terug te zetten.",
        "settings": "Instellingen",
        "set_sub": "Kies wat Map Opruimen opruimt. Wijzigingen worden automatisch bewaard.",
        "set_cats": "Categorieën",
        "mc_desc": "Mods (.jar), werelden, modpacks, resource packs, shaders",
        "set_rules": "Je eigen regels",
        "set_rules_sub": "Stuur een bestandstype naar een map naar keuze, bijv. .psd → Design.",
        "set_rules_none": "Nog geen regels.",
        "set_rule_ext": "Extensie",
        "set_rule_folder": "Map",
        "set_rule_add": "Regel toevoegen",
        "set_rule_bad_ext": "Typ een extensie zoals  .psd",
        "set_rule_bad_folder": "Typ een mapnaam zonder deze tekens:  \\ / : * ? \" < > |",
        "set_dups_title": "Dubbele bestanden & dezelfde namen",
        "set_dups": "Exacte dubbele bestanden zoeken",
        "set_dups_sub": "Kopieën gaan naar “_Duplicates”; het origineel blijft staan.",
        "set_clash": "Als er al een bestand met dezelfde naam bestaat",
        "set_clash_keep": "Beide houden (er komt een nummer achter de naam)",
        "set_clash_skip": "Overslaan (het bestand blijft staan)",
        "set_excl": "Hier blijft de app vanaf",
        "set_recent": "Bestanden gewijzigd in de afgelopen 24 uur",
        "set_recent_sub": "Handig voor bestanden die nog downloaden of waar je mee bezig bent.",
        "set_large": "Bestanden groter dan",
        "set_large_unit": "GB",
        "set_folders": "Mappen",
        "set_hidden": "Verborgen bestanden en systeembestanden",
        "set_safety": "Veiligheid",
        "set_never": "Bestanden worden nooit verwijderd of overschreven",
        "set_preview": "Je ziet altijd eerst een voorbeeld",
        "set_history": "Elke wijziging wordt bijgehouden zodat je hem kunt terugzetten",
        "always_on": "Altijd aan",
        "lang_btn": "English",
        "not_found": "Die map is niet te vinden.",
        "error": "Er ging iets mis:\n{err}\n\nWat al verplaatst is, kun je nog terugzetten.",
        "names": {DUPLICATES_DIR: "Dubbele kopieën", "Images": "Afbeeldingen", "Documents": "Documenten",
                  "Spreadsheets": "Spreadsheets", "Presentations": "Presentaties", "Video": "Video's",
                  "Audio": "Muziek & audio", "Archives": "Zip-bestanden & archieven",
                  "Installers": "Installatieprogramma's", "Fonts": "Lettertypen",
                  "Code": "Code & configuratie", MINECRAFT: "Minecraft", OTHER: "Overige bestanden"},
    },
}

# =============================================================================
# File logic (no window code)
# =============================================================================


def builtin_category(ext):
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return None


def looks_minecraft(path: Path) -> bool:
    ext, name = path.suffix.lower(), path.name.lower()
    return ext in MC_EXTS or (ext in MC_CONTAINERS and any(w in name for w in MC_WORDS))


def classify(path: Path, settings):
    """Returns (group or None if switched off, natural group)."""
    ext = path.suffix.lower()
    for rule in settings["rules"]:
        if ext == rule["ext"]:
            return rule["folder"], rule["folder"]
    natural = None
    if looks_minecraft(path):
        natural = MINECRAFT
        if settings["groups"].get(MINECRAFT):
            return MINECRAFT, MINECRAFT
    cat = builtin_category(ext) or OTHER
    if settings["groups"].get(cat):
        return cat, natural or cat
    return None, natural or cat


def hash_file(path: Path, on_bytes=None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
            if on_bytes:
                on_bytes(len(block))
    return h.hexdigest()


def free_target(target: Path) -> Path:
    """Never overwrite: returns 'file (2).pdf' etc. if the name is taken."""
    if not target.exists():
        return target
    i = 2
    while True:
        candidate = target.with_name(f"{target.stem} ({i}){target.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def this_program():
    try:
        return Path(sys.argv[0]).resolve()
    except Exception:  # noqa
        return None


def is_hidden_or_system(st) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x2 or attrs & 0x4)  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


class Item:
    """One file in the plan."""
    __slots__ = ("path", "size", "mtime", "group", "dest", "conflict", "identical", "copy_of")

    def __init__(self, path, size, mtime, group):
        self.path, self.size, self.mtime, self.group = path, size, mtime, group
        self.dest = None
        self.conflict = self.identical = False
        self.copy_of = None


def group_order(g):
    if g == DUPLICATES_DIR:
        return (0, "")
    if g in CATEGORIES:
        return (1 + list(CATEGORIES).index(g), "")
    if g == MINECRAFT:
        return (50, "")
    if g == OTHER:
        return (1000, "")
    return (100, g.lower())


def scan_folder(folder: Path, settings: dict, report):
    """Builds the plan. report(kind, **info) is called with progress updates."""
    me = this_program()
    now = time.time()
    limit = max(1, int(settings.get("large_gb", 10))) * 1024 ** 3
    items, skipped = [], []
    disabled, found = Counter(), Counter()
    mc_seen = total = 0

    for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        try:
            if not p.is_file() or p.is_symlink():
                continue
            name = p.name
            if (name.startswith(".") or name.startswith("~$") or name.lower() in SKIP_NAMES
                    or p.suffix.lower() in SKIP_SUFFIXES or (me and p.resolve() == me)):
                continue
            st = p.stat()
            if is_hidden_or_system(st):
                continue
        except OSError:
            continue
        group, natural = classify(p, settings)
        if natural == MINECRAFT and not settings["groups"].get(MINECRAFT):
            mc_seen += 1
        if group is None:
            disabled[natural] += 1
            continue
        if settings["skip_recent"] and now - st.st_mtime < 86400:
            skipped.append((p, "recent"))
            continue
        if settings["skip_large"] and st.st_size > limit:
            skipped.append((p, "large"))
            continue
        items.append(Item(p, st.st_size, st.st_mtime, group))
        total += st.st_size
        found[group] += 1
        report("list", n=len(items), size=total, found=dict(found))
    report("list", n=len(items), size=total, found=dict(found), force=True)

    hashes = {}

    def get_hash(p, tick=None):
        if p not in hashes:
            hashes[p] = hash_file(p, tick)
        return hashes[p]

    # exact duplicates
    if settings["duplicates"]:
        by_size = defaultdict(list)
        for it in items:
            if it.size > 0:
                by_size[it.size].append(it)
        candidates = [g for g in by_size.values() if len(g) > 1]
        to_hash = sum(it.size for g in candidates for it in g) or 1
        done = 0

        def tick(n):
            nonlocal done
            done += n
            report("hash", frac=min(1.0, done / to_hash))

        for group in candidates:
            by_hash = defaultdict(list)
            for it in group:
                try:
                    by_hash[get_hash(it.path, tick)].append(it)
                except OSError:
                    pass
            for same in by_hash.values():
                if len(same) > 1:
                    same.sort(key=lambda it: (len(it.path.name), it.mtime))
                    for c in same[1:]:
                        c.copy_of = same[0].path
                        c.group = DUPLICATES_DIR
    report("hash", frac=1.0, force=True)

    # destinations + name clashes (never overwrite)
    for it in items:
        it.dest = folder / it.group / it.path.name
        if it.dest.exists():
            it.conflict = True
            try:
                if it.dest.is_file() and it.dest.stat().st_size == it.size:
                    it.identical = get_hash(it.path) == get_hash(it.dest)
            except OSError:
                pass

    plan = defaultdict(list)
    for it in items:
        plan[it.group].append(it)
    plan = {g: plan[g] for g in sorted(plan, key=group_order)}
    return {"plan": plan, "skipped": skipped, "disabled": dict(disabled), "mc_seen": mc_seen}


# ---------- undo history ----------

def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_runs(folder: Path, runs):
    path = folder / LOG_NAME
    if not runs:
        try:
            path.unlink()
        except OSError:
            pass
        return
    data = {"version": 2, "date": runs[-1]["date"], "runs": runs,
            # flat list kept for the command-line version (cleanup.py --undo)
            "actions": [a for r in runs for a in r["actions"]]}
    tmp = folder / (LOG_NAME + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_runs(folder: Path):
    """All cleanup runs, oldest first. Also recovers an interrupted run from the journal."""
    runs, changed = [], False
    old = folder / OLD_LOG_NAME
    if old.exists():
        data = _read_json(old) or {}
        acts = [{"from": a["van"], "to": a["naar"]} for a in data.get("acties", [])]
        if acts:
            runs.append({"date": data.get("datum", ""), "actions": acts})
        changed = True
    data = _read_json(folder / LOG_NAME)
    if data:
        if "runs" in data:
            runs += data["runs"]
        elif data.get("actions"):
            runs.append({"date": data.get("date", ""), "actions": data["actions"]})
    journal = folder / JOURNAL_NAME
    if journal.exists():
        acts = []
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                try:
                    acts.append(json.loads(line))
                except ValueError:
                    pass
        except OSError:
            pass
        if acts:
            runs.append({"date": datetime.now().isoformat(timespec="seconds"), "actions": acts})
        changed = True
    if changed:
        save_runs(folder, runs)
        for p in (old, journal):
            try:
                p.unlink()
            except OSError:
                pass
    return runs


def organize(folder: Path, items):
    """Moves the given items. Returns (moved [(item, target)], failed [(item, reason)])."""
    runs = load_runs(folder)
    moved, failed = [], []
    journal = open(folder / JOURNAL_NAME, "a", encoding="utf-8")
    try:
        for it in items:
            if not it.path.exists():
                failed.append((it, "missing"))
                continue
            try:
                it.dest.parent.mkdir(exist_ok=True)
                target = free_target(it.dest)
                shutil.move(str(it.path), str(target))
            except OSError as e:
                failed.append((it, str(e)))
                continue
            journal.write(json.dumps({"from": str(it.path), "to": str(target)}, ensure_ascii=False) + "\n")
            journal.flush()
            moved.append((it, target))
    finally:
        journal.close()
        if moved:
            runs.append({"date": datetime.now().isoformat(timespec="seconds"),
                         "actions": [{"from": str(it.path), "to": str(t)} for it, t in moved]})
            save_runs(folder, runs)
        try:
            (folder / JOURNAL_NAME).unlink()
        except OSError:
            pass
    return moved, failed


def undo_last(folder: Path):
    """Undoes the most recent run. Returns counts, or None if there is nothing to undo."""
    runs = load_runs(folder)
    if not runs:
        return None
    run = runs[-1]
    actions = run["actions"]
    restored = missing = renamed = 0
    dirs = set()
    i = len(actions)
    try:
        while i > 0:
            a = actions[i - 1]
            moved_to, original = Path(a["to"]), Path(a["from"])
            dirs.add(moved_to.parent)
            if moved_to.exists():
                target = free_target(original)
                renamed += target != original
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_to), str(target))
                restored += 1
            else:
                missing += 1
            i -= 1
    finally:
        if i > 0:      # stopped halfway: keep what still needs undoing
            run["actions"] = actions[:i]
        else:
            runs.pop()
        save_runs(folder, runs)
        for d in dirs:
            try:
                if d.is_dir() and d != folder and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass
    return {"restored": restored, "missing": missing, "renamed": renamed}


# ---------- previews ----------

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm", ".py", ".ini",
                 ".cfg", ".yml", ".yaml", ".toml", ".js", ".css", ".bat", ".ps1", ".properties"}


def text_snippet(path: Path, limit=2000):
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def office_text(path: Path, limit=1500):
    """Plain text from a .docx / .pptx / .xlsx (zip files with XML inside)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" in names:
                parts, pattern = ["word/document.xml"], r"<w:t[^>]*>([^<]*)</w:t>"
            elif any(n.startswith("ppt/slides/slide") for n in names):
                parts = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                               key=lambda n: int(re.findall(r"\d+", n)[0]))
                pattern = r"<a:t>([^<]*)</a:t>"
            elif "xl/sharedStrings.xml" in names:
                parts, pattern = ["xl/sharedStrings.xml"], r"<t[^>]*>([^<]*)</t>"
            else:
                return None
            out = []
            for part in parts:
                xml = z.read(part).decode("utf-8", errors="replace")
                xml = re.sub(r"</w:p>|</a:p>|</si>", "\n", xml)
                for chunk in xml.split("\n"):
                    words = re.findall(pattern, chunk)
                    if words:
                        out.append(html.unescape("".join(words)))
                if sum(len(x) for x in out) > limit:
                    break
            return "\n".join(out)[:limit]
    except Exception:  # noqa
        return None


def zip_listing(path: Path, limit=14):
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            return names[:limit], len(names)
    except Exception:  # noqa
        return None


def open_in_explorer(path: Path):
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_in_folder(path: Path):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_in_explorer(path.parent)


# ---------- settings ----------

def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    saved = _read_json(SETTINGS_FILE) or {}
    s["groups"].update({k: bool(v) for k, v in saved.get("groups", {}).items() if k in s["groups"]})
    for k in ("duplicates", "skip_recent", "skip_large"):
        if k in saved:
            s[k] = bool(saved[k])
    if saved.get("conflicts") in ("keep", "skip"):
        s["conflicts"] = saved["conflicts"]
    try:
        s["large_gb"] = max(1, int(saved.get("large_gb", 10)))
    except (TypeError, ValueError):
        pass
    s["rules"] = [r for r in saved.get("rules", [])
                  if isinstance(r, dict) and valid_ext(r.get("ext")) and valid_folder(r.get("folder"))]
    if saved.get("lang") in ("en", "nl"):
        s["lang"] = saved["lang"]
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


def valid_ext(ext):
    return isinstance(ext, str) and re.fullmatch(r"\.[a-z0-9_\-]{1,15}", ext) is not None


def valid_folder(name):
    return (isinstance(name, str) and 0 < len(name.strip()) <= 40 and name.strip() not in (".", "..")
            and not (set(name) & BAD_FOLDER_CHARS) and name.strip() != DUPLICATES_DIR)


def system_language():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            return "nl" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x13 else "en"
        except Exception:  # noqa
            return "en"
    return "nl" if os.environ.get("LANG", "").lower().startswith("nl") else "en"


# =============================================================================
# Small custom widgets
# =============================================================================


def round_rect(c, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, splinesteps=16, **kw)


class Card(tk.Canvas):
    """A card with rounded corners. Put content in .inner"""

    def __init__(self, parent, app, pad=16, radius=8, fill=CARD, border=BORDER):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self.fill, self.border = fill, border
        self.pad, self.r = app.S(pad), app.S(radius)
        self.inner = tk.Frame(self, bg=fill)
        self.win = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        self.config(height=2 * self.pad + 2)  # inner must be partly visible to get mapped
        self.bind("<Configure>", self._redraw)
        self.inner.bind("<Configure>", self._fit)
        self.after_idle(self._fit)

    def _fit(self, _=None):
        if not self.winfo_exists():
            return
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if int(float(self["height"])) != h:
            self.config(height=h)

    def _redraw(self, e):
        self.delete("bg")
        round_rect(self, 1, 1, e.width - 2, e.height - 2, self.r,
                   fill=self.fill, outline=self.border, tags="bg")
        self.tag_lower("bg")
        self.itemconfigure(self.win, width=max(1, e.width - 2 * self.pad))

    def fill_space(self):
        """Make the card fill its space instead of fitting its content."""
        self.inner.unbind("<Configure>")
        self.inner.pack_propagate(False)

        def redraw(e):
            self.delete("bg")
            round_rect(self, 1, 1, e.width - 2, e.height - 2, self.r, fill=self.fill,
                       outline=self.border, tags="bg")
            self.tag_lower("bg")
            self.itemconfigure(self.win, width=max(1, e.width - 2 * self.pad),
                               height=max(1, e.height - 2 * self.pad))
        self.bind("<Configure>", redraw)
        return self


class Button(tk.Canvas):
    """Rounded button. kind: primary | secondary | subtle"""

    def __init__(self, parent, app, text, command, kind="primary", icon=None, tooltip=None):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=1)
        self.app, self.command, self.kind, self.icon = app, command, kind, icon
        self.enabled, self.hover = True, False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<space>", self._click)
        self.set_text(text)
        if tooltip:
            Tooltip(self, tooltip)

    def set_text(self, text):
        self.text = text
        a = self.app
        self.icon_w = (a.S(24) if text else a.S(16)) if self.icon else 0
        w = a.f_btn.measure(text) + self.icon_w + a.S(36 if self.kind != "subtle" else 20)
        self.config(width=w, height=a.S(36))
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self.draw()

    def _hover(self, on):
        self.hover = on
        self.draw()

    def _click(self, _=None):
        if self.enabled and self.command:
            self.command()
        return "break"

    def draw(self):
        a = self.app
        self.delete("all")
        w, h = int(float(self["width"])), int(float(self["height"]))
        if self.kind == "primary":
            fill = DISABLED if not self.enabled else (ACCENT_HOVER if self.hover else ACCENT)
            outline, fg = fill, "white"
        elif self.kind == "secondary":
            fill = "#f7f7f7" if self.hover and self.enabled else CARD
            outline, fg = BORDER, (INK if self.enabled else DISABLED)
        else:  # subtle
            base = self["bg"]
            fill = "#e7e7e7" if self.hover and self.enabled and base == BG else (
                "#f2f2f2" if self.hover and self.enabled else base)
            outline, fg = fill, (ACCENT if self.enabled else DISABLED)
        round_rect(self, 1, 1, w - 2, h - 2, a.S(5), fill=fill, outline=outline)
        total = self.icon_w + a.f_btn.measure(self.text)
        x = (w - total) / 2
        if self.icon:
            self.create_text(x + a.S(8), h / 2, text=a.glyph(self.icon), font=a.f_icon_s, fill=fg)
        if self.text:
            self.create_text(x + self.icon_w, h / 2, text=self.text, font=a.f_btn, fill=fg, anchor="w")


class Check(tk.Canvas):
    """Windows 11-style checkbox (or radio button) bound to a BooleanVar."""

    def __init__(self, parent, app, var, command=None, enabled=True, radio=False):
        size = app.S(20)
        super().__init__(parent, bg=parent["bg"], width=size, height=size, highlightthickness=0,
                         bd=0, cursor="hand2" if enabled else "arrow")
        self.app, self.var, self.command = app, var, command
        self.enabled, self.size, self.radio = enabled, size, radio
        self.bind("<ButtonRelease-1>", self.toggle)
        self.draw()

    def toggle(self, _=None):
        if not self.enabled:
            return
        if self.radio:
            if self.var.get():
                return
            self.var.set(True)
        else:
            self.var.set(not self.var.get())
        self.draw()
        if self.command:
            self.command()

    def draw(self):
        s, a = self.size, self.app
        self.delete("all")
        on = self.var.get()
        col = ACCENT if self.enabled else FAINT
        if self.radio:
            if on:
                self.create_oval(1, 1, s - 2, s - 2, fill=col, outline=col)
                r = s * .2
                self.create_oval(s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r, fill="white", outline="white")
            else:
                self.create_oval(1, 1, s - 2, s - 2, fill=CARD, outline="#8a8a8a")
            return
        if on:
            round_rect(self, 1, 1, s - 2, s - 2, a.S(4), fill=col, outline=col)
            self.create_line(s * .27, s * .52, s * .43, s * .68, s * .74, s * .34,
                             fill="white", width=max(2, a.S(2)), capstyle="round", joinstyle="round")
        else:
            round_rect(self, 1, 1, s - 2, s - 2, a.S(4), fill=CARD, outline="#8a8a8a")


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self.job = self.widget.after(500, self._show)

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        text = self.text() if callable(self.text) else self.text
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=text, bg="#2b2b2b", fg="white", padx=8, pady=4).pack()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.geometry(f"+{x}+{y}")

    def _hide(self, _=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def bind_toggle(widgets, check):
    for w in widgets:
        w.bind("<ButtonRelease-1>", check.toggle)
        if check.enabled:
            w.config(cursor="hand2")


# =============================================================================
# The app
# =============================================================================


class App:
    def __init__(self, root):
        self.root = root
        dpi = root.winfo_fpixels("1i")
        self.scale = max(1.0, dpi / 96.0) if sys.platform.startswith("win") else 1.0
        self.settings = load_settings()
        self.lang = self.settings.get("lang") or system_language()
        self.folder = Path.home() / "Downloads"
        if not self.folder.is_dir():
            self.folder = Path.home()

        self.result = None          # scan result
        self.sel = {}               # group -> BooleanVar (review screen)
        self.excluded = set()       # paths the user chose to leave
        self.choice = {}            # path -> "keep" | "skip" for name clashes
        self.done = None
        self.page = self.page_choose
        self.step = 1
        self.q = queue.Queue()
        self.busy = False
        self.installing = False
        self.primary = self.back = None

        self.setup_fonts()
        root.title(self.t("app"))
        root.configure(bg=BG)
        root.geometry(f"{self.S(980)}x{self.S(760)}")
        root.minsize(self.S(780), self.S(620))

        style = ttk.Style(root)
        style.configure("Clean.Treeview", font=self.f_body, rowheight=self.S(28),
                        background=CARD, fieldbackground=CARD, borderwidth=0)
        style.map("Clean.Treeview", background=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", INK)])
        style.layout("Clean.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Skip.Treeview", font=self.f_body, rowheight=self.S(26))

        self.header = tk.Frame(root, bg=BG)
        self.header.pack(fill="x", padx=self.S(28), pady=(self.S(18), 0))
        self.stepper = tk.Canvas(root, bg=BG, height=self.S(40), highlightthickness=0)
        self.stepper.pack(fill="x", padx=self.S(28), pady=(self.S(10), 0))
        self.stepper.bind("<Configure>", lambda e: self.draw_stepper())
        self.footer_wrap = tk.Frame(root, bg=BG)
        self.footer_wrap.pack(side="bottom", fill="x")
        tk.Frame(self.footer_wrap, bg=BORDER, height=1).pack(fill="x")
        self.footer = tk.Frame(self.footer_wrap, bg=BG)
        self.footer.pack(fill="x", padx=self.S(28), pady=self.S(14))
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=self.S(28), pady=(self.S(14), self.S(10)))

        root.bind("<Return>", self._key_enter)
        root.bind("<Escape>", self._key_escape)
        self.render()

    # ---------- helpers ----------
    def S(self, x):
        return int(round(x * self.scale))

    def t(self, key, **kw):
        if kw.get("n") in (1, "1") and key + "_1" in TEXT[self.lang]:
            key += "_1"
        v = TEXT[self.lang][key]
        return v.format(**kw) if kw else v

    def name(self, group):
        return TEXT[self.lang]["names"].get(group, group)

    def icon_key(self, group):
        return group if group in ICONS else "rule"

    def num(self, n):
        return f"{n:,}".replace(",", "." if self.lang == "nl" else ",")

    def nfiles(self, n):
        return self.t("file") if n == 1 else self.t("files", n=self.num(n))

    def glyph(self, key):
        mdl2, fallback = ICONS[key]
        return mdl2 if self.icon_family else fallback

    def fmt_date(self, iso):
        try:
            d = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return "?"
        return d.strftime("%d-%m-%Y %H:%M") if self.lang == "nl" else d.strftime("%b %d, %Y %H:%M")

    def setup_fonts(self):
        fams = set(tkfont.families(self.root))
        win = sys.platform.startswith("win")
        base = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in fams),
                    "Helvetica" if not win else "Segoe UI")
        semi = next((f for f in ("Segoe UI Variable Display Semib", "Segoe UI Semibold") if f in fams), None)
        self.icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        hw = "normal" if semi else "bold"
        semi = semi or base
        F = tkfont.Font
        self.f_title = F(family=semi, size=20, weight=hw)
        self.f_h2 = F(family=semi, size=12, weight=hw)
        self.f_strong = F(family=semi, size=10, weight=hw)
        self.f_body = F(family=base, size=10)
        self.f_small = F(family=base, size=9)
        self.f_btn = F(family=semi, size=10, weight=hw)
        self.f_huge = F(family=semi, size=34, weight=hw)
        self.f_stat = F(family=semi, size=18, weight=hw)
        self.f_app = F(family=semi, size=10, weight=hw)
        ifam = self.icon_family or base
        self.f_icon = F(family=ifam, size=15)
        self.f_icon_s = F(family=ifam, size=11)

    def label(self, parent, text, font=None, fg=INK, wrap=None, **kw):
        lb = tk.Label(parent, text=text, font=font or self.f_body, fg=fg, bg=parent["bg"],
                      anchor="w", justify="left", **kw)
        if wrap:
            lb.config(wraplength=self.S(wrap))
        return lb

    def icon_tile(self, parent, key, size=36, fill=ACCENT_SOFT, fg=ACCENT, font=None):
        s = self.S(size)
        c = tk.Canvas(parent, width=s, height=s, bg=parent["bg"], highlightthickness=0)
        round_rect(c, 0, 0, s - 1, s - 1, self.S(6), fill=fill, outline=fill)
        c.create_text(s / 2, s / 2, text=self.glyph(key), font=font or self.f_icon, fill=fg)
        return c

    def banner(self, parent, kind, title, text=None, button=None):
        """kind: safe | warn | tip"""
        fill, fg, icon = {"safe": (GREEN_SOFT, GREEN, "shield"), "warn": (AMBER_SOFT, AMBER, "warning"),
                          "tip": (ACCENT_SOFT, ACCENT, "bulb")}[kind]
        f = tk.Frame(parent, bg=fill)
        self.label(f, self.glyph(icon), self.f_icon_s, fg=fg).pack(
            side="left", anchor="n", padx=(self.S(12), self.S(10)), pady=self.S(10))
        if button:
            Button(f, self, button[0], button[1], "secondary").pack(side="right", padx=self.S(10),
                                                                   pady=self.S(6))
        txt = tk.Frame(f, bg=fill)
        txt.pack(side="left", fill="x", expand=True, pady=self.S(8))
        self.label(txt, title, self.f_strong, fg=fg if kind != "tip" else INK, wrap=640).pack(fill="x")
        if text:
            self.label(txt, text, self.f_small, fg=INK, wrap=640).pack(fill="x")
        return f

    def scroll_area(self, parent):
        wrap = tk.Frame(parent, bg=parent["bg"])
        canvas = tk.Canvas(wrap, bg=parent["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=parent["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)

        def refresh(_=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            need = inner.winfo_reqheight() > canvas.winfo_height()
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y", padx=(self.S(6), 0))
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()
                canvas.yview_moveto(0)
        inner.bind("<Configure>", refresh)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), refresh()))

        def wheel(e):
            if sb.winfo_ismapped():
                canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return wrap, inner

    # ---------- keyboard ----------
    def _key_enter(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Spinbox, ttk.Combobox)):
            return
        if self.primary and not self.busy:
            self.primary()

    def _key_escape(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry)) and getattr(self, "search_var", None) \
                and e.widget is getattr(self, "search_entry", None) and self.search_var.get():
            self.search_var.set("")
            return
        if self.back and not self.busy:
            self.back()

    # ---------- frame ----------
    def render(self):
        self.root.title(self.t("app"))
        self.root.unbind_all("<MouseWheel>")
        for w in (*self.header.winfo_children(), *self.body.winfo_children(),
                  *self.footer.winfo_children()):
            w.destroy()
        self.primary = self.back = None
        left = tk.Frame(self.header, bg=BG)
        left.pack(side="left")
        self.icon_tile(left, "folder", size=28, fill=ACCENT, fg="white",
                       font=self.f_icon_s).pack(side="left")
        self.label(left, self.t("app"), self.f_app).pack(side="left", padx=(self.S(10), 0))
        if not self.busy:
            Button(self.header, self, "", self.open_settings, "subtle", icon="settings",
                   tooltip=lambda: self.t("settings_tip")).pack(side="right")
            Button(self.header, self, self.t("lang_btn"), self.toggle_lang,
                   "subtle").pack(side="right", padx=(0, self.S(4)))
        self.page()
        self.draw_stepper()
        self.root.focus_set()

    def draw_stepper(self):
        c = self.stepper
        c.delete("all")
        want = 1 if self.page == self.page_settings else self.S(40)
        if int(float(c["height"])) != want:
            c.config(height=want)
        if self.page == self.page_settings:
            return
        w, h = c.winfo_width(), int(float(c["height"]))
        y, r = h / 2, self.S(10)
        x = r + 2
        labels = self.t("steps")
        for i, lab in enumerate(labels, start=1):
            done, cur = i < self.step, i == self.step
            if done or cur:
                c.create_oval(x - r, y - r, x + r, y + r, fill=ACCENT, outline=ACCENT)
                if done:
                    c.create_line(x - r * .45, y, x - r * .1, y + r * .35, x + r * .45, y - r * .35,
                                  fill="white", width=max(2, self.S(2)), capstyle="round")
                else:
                    c.create_text(x, y, text=str(i), fill="white", font=self.f_small)
            else:
                c.create_oval(x - r, y - r, x + r, y + r, fill=CARD, outline="#b5b5b5")
                c.create_text(x, y, text=str(i), fill=FAINT, font=self.f_small)
            tx = x + r + self.S(8)
            c.create_text(tx, y, text=lab, anchor="w", font=self.f_strong if cur else self.f_body,
                          fill=INK if (cur or done) else FAINT)
            x = tx + self.f_strong.measure(lab) + self.S(14)
            if i < len(labels):
                end = x + self.S(40)
                c.create_line(x, y, end, y, fill=ACCENT if done else "#cfcfcf", width=max(1, self.S(2)))
                x = end + self.S(14) + r
        c.create_text(w - 2, y, text=self.t("step_of", n=self.step), anchor="e",
                      font=self.f_small, fill=MUTED)

    def go(self, page, step):
        self.page, self.step = page, step
        self.render()

    # ---------- step 1: choose ----------
    def page_choose(self):
        b = self.body
        self.label(b, self.t("c_title"), self.f_title).pack(fill="x")
        self.label(b, self.t("c_sub"), fg=MUTED, wrap=760).pack(fill="x", pady=(self.S(4), self.S(18)))

        card = Card(b, self)
        card.pack(fill="x")
        row = card.inner
        self.icon_tile(row, "folder", size=44).pack(side="left")
        Button(row, self, self.t("change"), self.choose_folder, "secondary").pack(side="right")
        txt = tk.Frame(row, bg=CARD)
        txt.pack(side="left", fill="x", expand=True, padx=self.S(14))
        self.label(txt, self.folder.name or str(self.folder), self.f_h2).pack(fill="x")
        p = str(self.folder)
        if len(p) > 70:
            p = p[:26] + "…" + p[-42:]
        self.label(txt, p, self.f_small, fg=MUTED).pack(fill="x")

        self.label(b, self.t("what"), self.f_h2).pack(fill="x", pady=(self.S(24), self.S(8)))
        for item in self.t("what_items"):
            r = tk.Frame(b, bg=BG)
            r.pack(fill="x", pady=self.S(3))
            self.icon_tile(r, "check", size=22, fill=GREEN_SOFT, fg=GREEN,
                           font=self.f_icon_s).pack(side="left")
            self.label(r, item).pack(side="left", padx=(self.S(10), 0))

        try:
            runs = load_runs(self.folder)
        except Exception:  # noqa
            runs = []
        if runs:
            last = runs[-1]
            Button(self.footer, self, self.t("undo_last", n=self.num(len(last["actions"])),
                                             date=self.fmt_date(last["date"])),
                   self.undo, "subtle", icon="undo").pack(side="left")
        Button(self.footer, self, self.t("scan"), self.start_scan, "primary",
               icon="search").pack(side="right")
        self.primary = self.start_scan

    def choose_folder(self):
        picked = filedialog.askdirectory(initialdir=str(self.folder), parent=self.root)
        if picked:
            self.folder = Path(picked)
            self.go(self.page_choose, 1)

    # ---------- step 2a: scanning ----------
    def start_scan(self):
        if not self.folder.is_dir():
            messagebox.showerror(self.t("app"), self.t("not_found"), parent=self.root)
            return
        try:
            load_runs(self.folder)  # finishes an interrupted run first, if any
        except Exception:  # noqa
            pass
        self.busy, self.sel, self.result = True, {}, None
        self.excluded, self.choice = set(), {}
        self.scan_state = {"n": 0, "size": 0, "found": {}, "frac": 0.0, "phase": "list"}
        self.scan_started = time.time()
        self.go(self.page_scanning, 2)
        folder, settings = self.folder, json.loads(json.dumps(self.settings))
        last = [0.0]

        def report(kind, force=False, **info):
            now = time.time()
            if force or now - last[0] > 0.05:
                last[0] = now
                self.q.put(("progress", kind, info))

        def work():
            try:
                self.q.put(("done", scan_folder(folder, settings, report), None))
            except Exception as e:  # noqa
                self.q.put(("error", e, None))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(60, self.poll)

    def page_scanning(self):
        center = tk.Frame(self.body, bg=BG)
        center.place(relx=.5, rely=.42, anchor="center")
        self.label(center, self.t("s_title", name=self.folder.name or str(self.folder)),
                   self.f_h2).pack()
        self.pct_label = tk.Label(center, text="0%", font=self.f_huge, fg=ACCENT, bg=BG)
        self.pct_label.pack(pady=(self.S(10), 0))
        self.label(center, self.t("s_sub"), fg=MUTED).pack()
        self.bar = tk.Canvas(center, width=self.S(380), height=self.S(6), bg=BG, highlightthickness=0)
        self.bar.pack(pady=(self.S(16), self.S(8)))
        self.found_label = self.label(center, "", self.f_small, fg=MUTED)
        self.found_label.pack()
        self.live = tk.Frame(center, bg=BG)
        self.live.pack(pady=(self.S(16), 0))
        self.update_scan_ui()

    def update_scan_ui(self):
        st = self.scan_state
        pct = int(5 + 95 * st["frac"]) if st["phase"] == "hash" else min(5, st["n"] // 50)
        self.pct_label.config(text=f"{pct}%")
        w, h = int(float(self.bar["width"])), int(float(self.bar["height"]))
        self.bar.delete("all")
        round_rect(self.bar, 0, 0, w, h, h / 2, fill="#dedede", outline="#dedede")
        if pct > 0:
            round_rect(self.bar, 0, 0, max(h, w * pct / 100), h, h / 2, fill=ACCENT, outline=ACCENT)
        self.found_label.config(text=self.t("s_found", n=self.num(st["n"]), size=human_size(st["size"])))
        for wdg in self.live.winfo_children():
            wdg.destroy()
        rows = [(f"{self.name(g)}  ·  {self.num(n)}", True)
                for g, n in sorted(st["found"].items(), key=lambda kv: group_order(kv[0]))]
        if self.settings["duplicates"]:
            rows.append((self.t("s_dups"), st["phase"] == "hash" and st["frac"] >= 1))
        for text, ok in rows[:12]:
            r = tk.Frame(self.live, bg=BG)
            r.pack(anchor="w", pady=1)
            tk.Label(r, text=self.glyph("check") if ok else "◌", fg=GREEN if ok else FAINT,
                     bg=BG, font=self.f_icon_s if ok else self.f_body, width=2).pack(side="left")
            self.label(r, text, self.f_small, fg=INK if ok else MUTED).pack(side="left")

    def poll(self):
        latest = None
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    _, kind, info = msg
                    if kind == "list":
                        self.scan_state.update(n=info["n"], size=info["size"], found=info["found"])
                    else:
                        self.scan_state.update(phase="hash", frac=info["frac"])
                else:
                    latest = msg
        except queue.Empty:
            pass
        if self.page == self.page_scanning:
            self.update_scan_ui()
        if latest is None:
            self.root.after(60, self.poll)
            return
        kind, payload, _ = latest
        wait = max(0, int((0.9 - (time.time() - self.scan_started)) * 1000))  # let it be seen
        if kind == "done":
            self.result = payload
            self.sel = {g: tk.BooleanVar(value=True) for g in payload["plan"]}
            default = self.settings["conflicts"]
            self.choice = {it.path: default for items in payload["plan"].values()
                           for it in items if it.conflict}
            self.root.after(wait, self._finish_scan)
        else:
            self.busy = False
            messagebox.showerror(self.t("app"), self.t("error", err=payload), parent=self.root)
            self.go(self.page_choose, 1)

    def _finish_scan(self):
        self.busy = False
        self.go(self.page_review, 2)

    # ---------- plan helpers ----------
    def all_items(self, only_selected=True):
        for g, items in self.result["plan"].items():
            if only_selected and not self.sel[g].get():
                continue
            yield from items

    def will_move(self, it):
        return (self.sel[it.group].get() and it.path not in self.excluded
                and not (it.conflict and self.choice.get(it.path) == "skip"))

    def selection(self):
        plan = self.result["plan"]
        groups = [g for g in plan if self.sel[g].get()]
        items = [it for g in groups for it in plan[g]]
        return groups, len(items), sum(it.size for it in items)

    # ---------- step 2b: review ----------
    def page_review(self):
        b, res = self.body, self.result
        plan = res["plan"]
        self.back = lambda: self.go(self.page_choose, 1)
        if not plan:
            box = tk.Frame(b, bg=BG)
            box.place(relx=.5, rely=.4, anchor="center")
            self.icon_tile(box, "check", size=56, fill=GREEN_SOFT, fg=GREEN).pack()
            self.label(box, self.t("r_none_title"), self.f_title).pack(pady=(self.S(12), self.S(4)))
            tk.Label(box, text=self.t("r_none"), font=self.f_body, fg=MUTED, bg=BG,
                     wraplength=self.S(460), justify="center").pack()
            for s in self.suggestions():
                self.banner(box, "tip", s[0], button=s[1]).pack(fill="x", pady=(self.S(14), 0))
            Button(self.footer, self, self.t("back"), self.back, "secondary", icon="back").pack(side="right")
            self.primary = self.back
            return

        total_n = sum(len(v) for v in plan.values())
        total_size = sum(it.size for v in plan.values() for it in v)
        self.label(b, self.t("r_title"), self.f_title).pack(fill="x")
        self.label(b, self.t("r_sub", n=self.num(total_n), size=human_size(total_size)),
                   fg=MUTED, wrap=860).pack(fill="x", pady=(self.S(4), self.S(10)))

        wrap, inner = self.scroll_area(b)
        wrap.pack(fill="both", expand=True)

        conflicts = sum(1 for it in self.all_items(False) if it.conflict)
        if conflicts:
            self.banner(inner, "warn", self.t("r_attention_title", n=self.num(conflicts)),
                        self.t("r_attention")).pack(fill="x", pady=(0, self.S(8)), padx=(0, self.S(2)))
        else:
            self.banner(inner, "safe", self.t("r_safe_title"), self.t("r_safe")).pack(
                fill="x", pady=(0, self.S(8)), padx=(0, self.S(2)))
        for text, button in self.suggestions():
            self.banner(inner, "tip", text, button=button).pack(fill="x", pady=(0, self.S(8)),
                                                                padx=(0, self.S(2)))
        if res["skipped"]:
            self.label(inner, self.t("r_skipped", n=self.num(len(res["skipped"]))), self.f_small,
                       fg=MUTED).pack(fill="x", pady=(0, self.S(6)))

        card = Card(inner, self, pad=4)
        card.pack(fill="x", padx=(0, self.S(2)))
        biggest = max(sum(it.size for it in v) for v in plan.values()) or 1
        bar_w = self.S(200)
        for i, (g, items) in enumerate(plan.items()):
            if i:
                tk.Frame(card.inner, bg=DIVIDER, height=1).pack(fill="x", padx=self.S(12))
            size = sum(it.size for it in items)
            row = tk.Frame(card.inner, bg=CARD)
            row.pack(fill="x", padx=self.S(12), pady=self.S(10))
            chk = Check(row, self, self.sel[g], command=self.update_review_footer)
            chk.pack(side="left")
            tile = self.icon_tile(row, self.icon_key(g), size=36)
            tile.pack(side="left", padx=(self.S(12), self.S(12)))
            right = tk.Frame(row, bg=CARD)
            right.pack(side="right")
            l1 = self.label(right, self.nfiles(len(items)), self.f_strong)
            l1.pack(anchor="e")
            l2 = self.label(right, human_size(size), self.f_small, fg=MUTED)
            l2.pack(anchor="e")
            mid = tk.Frame(row, bg=CARD)
            mid.pack(side="left", fill="x", expand=True)
            t1 = self.label(mid, self.name(g), self.f_strong)
            t1.pack(fill="x")
            if g == DUPLICATES_DIR:
                desc = self.t("dup_desc")
            elif g not in BUILTIN_GROUPS:
                desc = self.t("rule_desc")
            else:
                exts = Counter(it.path.suffix.upper().lstrip(".") or "—" for it in items)
                desc = ", ".join(e for e, _ in exts.most_common(5)) + (", …" if len(exts) > 5 else "")
            sub = tk.Frame(mid, bg=CARD)
            sub.pack(fill="x")
            t2 = self.label(sub, desc, self.f_small, fg=MUTED)
            t2.pack(side="left")
            t3 = self.label(sub, f"   →  {g}", self.f_small, fg=ACCENT)
            t3.pack(side="left")
            n_conf = sum(1 for it in items if it.conflict)
            parts = [row, tile, mid, t1, sub, t2, t3, right, l1, l2]
            if n_conf:
                t4 = self.label(sub, f"   {self.glyph('warning')} {self.num(n_conf)}",
                                self.f_icon_s if self.icon_family else self.f_small, fg=AMBER)
                t4.pack(side="left")
                parts.append(t4)
            bar = tk.Canvas(mid, width=bar_w, height=self.S(4), bg=CARD, highlightthickness=0)
            bar.pack(anchor="w", pady=(self.S(5), 0))
            round_rect(bar, 0, 0, bar_w, self.S(4), self.S(2), fill="#ececec", outline="#ececec")
            fw = max(self.S(4), bar_w * size / biggest)
            round_rect(bar, 0, 0, fw, self.S(4), self.S(2), fill=ACCENT, outline=ACCENT)
            parts.append(bar)
            bind_toggle(parts, chk)

        self.footer_info = self.label(self.footer, "", self.f_small, fg=MUTED)
        self.footer_info.pack(side="left")
        self.review_btn = Button(self.footer, self, self.t("review"), self.open_preview,
                                 "primary", icon="forward")
        self.review_btn.pack(side="right")
        Button(self.footer, self, self.t("back"), self.back, "secondary", icon="back").pack(
            side="right", padx=(0, self.S(8)))
        self.update_review_footer()

    def suggestions(self):
        res, st, out = self.result, self.settings, []
        if not st["groups"].get(MINECRAFT) and res["mc_seen"] >= 3:
            out.append((self.t("sug_mc", n=self.num(res["mc_seen"])),
                        (self.t("sug_mc_btn"), lambda: self.enable_group(MINECRAFT))))
        n_other = res["disabled"].get(OTHER, 0)
        if not st["groups"].get(OTHER) and n_other:
            out.append((self.t("sug_other", n=self.num(n_other)),
                        (self.t("sug_other_btn"), lambda: self.enable_group(OTHER))))
        return out

    def enable_group(self, g):
        self.settings["groups"][g] = True
        save_settings(self.settings)
        self.start_scan()

    def update_review_footer(self):
        _, n, size = self.selection()
        self.footer_info.config(text=self.t("r_selected", n=self.num(n), size=human_size(size)))
        self.review_btn.set_enabled(n > 0)
        self.primary = self.open_preview if n else None

    def open_preview(self):
        self.go(self.page_preview, 2)

    # ---------- step 2c: preview ----------
    def page_preview(self):
        b = self.body
        self.back = lambda: self.go(self.page_review, 2)
        self.primary = self.do_organize
        self.label(b, self.t("p_title"), self.f_title).pack(fill="x")
        self.label(b, self.t("p_sub"), fg=MUTED, wrap=860).pack(fill="x", pady=(self.S(4), self.S(10)))

        safe = tk.Frame(b, bg=GREEN_SOFT)
        safe.pack(side="bottom", fill="x", pady=(self.S(10), 0))
        self.label(safe, self.glyph("shield"), self.f_icon_s, fg=GREEN).pack(
            side="left", padx=(self.S(12), self.S(8)), pady=self.S(8))
        self.label(safe, self.t("safe"), self.f_strong, fg=GREEN).pack(side="left")

        # toolbar: search + filter
        bar = tk.Frame(b, bg=BG)
        bar.pack(fill="x", pady=(0, self.S(8)))
        box = tk.Frame(bar, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT)
        box.pack(side="left")
        self.label(box, self.glyph("search"), self.f_icon_s, fg=MUTED).pack(side="left", padx=(self.S(8), 0))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(box, textvariable=self.search_var, font=self.f_body, relief="flat",
                                     bg=CARD, fg=INK, width=30, insertbackground=INK)
        self.search_entry.pack(side="left", padx=self.S(6), pady=self.S(6))
        ph = self.label(box, self.t("p_search"), fg=FAINT)

        def placeholder(*_):
            if self.search_var.get() or self.root.focus_get() is self.search_entry:
                ph.place_forget()
            else:
                ph.place(in_=self.search_entry, x=0, rely=.5, anchor="w")
        self.search_entry.bind("<FocusIn>", placeholder)
        self.search_entry.bind("<FocusOut>", placeholder)
        ph.bind("<Button-1>", lambda e: self.search_entry.focus_set())
        placeholder()

        groups = [g for g in self.result["plan"] if self.sel[g].get()]
        self.filters = [("all", self.t("f_all"))]
        if any(it.conflict for it in self.all_items()):
            self.filters.append(("attention", self.t("f_attention")))
        self.filters.append(("staying", self.t("f_staying")))
        self.filters += [(g, f"{self.name(g)}  ({g}/)") for g in groups]
        self.filter_var = tk.StringVar(value=self.filters[0][1])
        combo = ttk.Combobox(bar, textvariable=self.filter_var, values=[f[1] for f in self.filters],
                             state="readonly", width=30, font=self.f_body)
        combo.pack(side="right")
        self.label(bar, self.t("p_show"), fg=MUTED).pack(side="right", padx=(0, self.S(8)))

        area = tk.Frame(b, bg=BG)
        area.pack(fill="both", expand=True)
        vcard = Card(area, self, pad=8).fill_space()
        vcard.config(width=self.S(350))
        vcard.pack(side="right", fill="y", padx=(self.S(12), 0))
        self.viewer = vcard.inner
        tcard = Card(area, self, pad=8).fill_space()
        tcard.pack(side="left", fill="both", expand=True)

        self.tree = tree = ttk.Treeview(tcard.inner, style="Clean.Treeview", show="tree", selectmode="browse")
        sb = ttk.Scrollbar(tcard.inner, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        tree.tag_configure("folder", font=self.f_strong)
        tree.tag_configure("root", font=self.f_strong, foreground=MUTED)
        tree.tag_configure("conflict", foreground=AMBER)
        tree.tag_configure("staying", foreground=FAINT)
        tree.tag_configure("note", foreground=MUTED)

        def on_select(_=None):
            sel = tree.selection()
            if sel and sel[0] in self.tree_items:
                self.show_details(self.tree_items[sel[0]])

        def on_double(_=None):
            sel = tree.selection()
            if sel and sel[0] in self.tree_items:
                self.open_file(self.tree_items[sel[0]].path)
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", on_double)

        self._search_job = None

        def changed(*_):
            placeholder()
            if self._search_job:
                self.root.after_cancel(self._search_job)
            self._search_job = self.root.after(180, self.build_tree)
        self.search_var.trace_add("write", changed)
        combo.bind("<<ComboboxSelected>>", lambda e: (self.build_tree(), self.root.focus_set()))

        self.preview_info = self.label(self.footer, "", self.f_small, fg=MUTED)
        self.preview_info.pack(side="left")
        self.organize_btn = Button(self.footer, self, self.t("organize"), self.do_organize, "primary",
                                   icon="check")
        self.organize_btn.pack(side="right")
        Button(self.footer, self, self.t("back"), self.back, "secondary", icon="back").pack(
            side="right", padx=(0, self.S(8)))

        self.current_item = None
        self.build_tree()
        self.show_details(None)

    def item_label(self, it):
        text = f"  {it.path.name}"
        tags = ()
        if it.group == DUPLICATES_DIR and it.copy_of:
            text += f"    ← {self.t('copy_of', name=it.copy_of.name)}"
            tags = ("note",)
        if not self.will_move(it):
            text += f"    — {self.t('tag_stays')}"
            tags = ("staying",)
        elif it.conflict:
            text += f"    ⚠ {self.t('tag_conflict')}"
            tags = ("conflict",)
        return text, tags

    def build_tree(self):
        tree = self.tree
        self._search_job = None
        tree.delete(*tree.get_children())
        self.tree_items, self.item_iids = {}, {}
        q = self.search_var.get().strip().casefold()
        label = self.filter_var.get()
        key = next((k for k, l in self.filters if l == label), "all")
        plan = self.result["plan"]
        filtering = bool(q) or key != "all"

        def keep(it):
            if q and q not in it.path.name.casefold():
                return False
            if key == "attention":
                return it.conflict
            if key == "staying":
                return not self.will_move(it)
            if key not in ("all",):
                return it.group == key
            return True

        root_id = tree.insert("", "end", text=f"  {self.folder.name or self.folder}/", open=True,
                              tags=("root",))
        shown = 0
        for g in plan:
            if not self.sel[g].get():
                continue
            items = [it for it in plan[g] if keep(it)]
            if not items:
                continue
            node = tree.insert(root_id, "end", open=filtering or len(items) <= 25, tags=("folder",),
                               text=f"  {g}/      {self.nfiles(len(items))}")
            for it in items:
                text, tags = self.item_label(it)
                iid = tree.insert(node, "end", text=text, tags=tags)
                self.tree_items[iid] = it
                self.item_iids[it.path] = iid
            shown += len(items)
        if not shown:
            tree.insert(root_id, "end", text=f"  {self.t('p_nomatch')}", tags=("staying",))
        cur = self.current_item
        if cur is not None and cur.path in self.item_iids:
            iid = self.item_iids[cur.path]
            tree.selection_set(iid)
            tree.see(iid)
        self.update_preview_footer()

    def refresh_item(self, it):
        iid = getattr(self, "item_iids", {}).get(it.path)
        if iid and self.tree.exists(iid):
            text, tags = self.item_label(it)
            self.tree.item(iid, text=text, tags=tags)
        self.update_preview_footer()

    def update_preview_footer(self):
        items = list(self.all_items())
        n = sum(1 for it in items if self.will_move(it))
        self.preview_info.config(text=self.t("p_count", n=self.num(n), m=self.num(len(items) - n)))
        self.organize_btn.set_enabled(n > 0)

    def open_file(self, path):
        try:
            open_in_explorer(path)
        except Exception as e:  # noqa
            messagebox.showerror(self.t("app"), self.t("error", err=e), parent=self.root)

    def show_details(self, it):
        self.current_item = it
        v = self.viewer
        for w in v.winfo_children():
            w.destroy()
        self._preview_img = None
        pad = self.S(10)
        if it is None:
            box = tk.Frame(v, bg=CARD)
            box.place(relx=.5, rely=.45, anchor="center")
            self.icon_tile(box, "search", size=44).pack()
            tk.Label(box, text=self.t("v_pick"), font=self.f_body, fg=MUTED, bg=CARD,
                     wraplength=self.S(240), justify="center").pack(pady=(self.S(10), 0))
            return

        path = it.path
        head = tk.Frame(v, bg=CARD)
        head.pack(fill="x", padx=pad, pady=(pad, self.S(6)))
        self.icon_tile(head, self.icon_key(it.group if it.group == DUPLICATES_DIR
                                           else (builtin_category(path.suffix.lower()) or it.group)),
                       size=36).pack(side="left", anchor="n")
        self.label(head, path.name, self.f_strong, wrap=260).pack(side="left", fill="x",
                                                                  padx=(self.S(10), 0))
        try:
            st = path.stat()
            size = human_size(st.st_size)
            changed = datetime.fromtimestamp(st.st_mtime).strftime("%d-%m-%Y %H:%M")
        except OSError:
            size = changed = "—"

        moving = self.will_move(it)
        info = tk.Frame(v, bg=CARD)
        info.pack(fill="x", padx=pad)
        rows = [(self.t("v_moves"), f"{it.group}/") if moving else (self.t("v_stays"), ""),
                (self.t("v_size"), size), (self.t("v_changed"), changed)]
        if it.copy_of:
            rows.insert(0, (self.t("v_copy_of"), it.copy_of.name))
        for i, (k, val) in enumerate(rows):
            self.label(info, k, self.f_small, fg=MUTED if val else AMBER).grid(row=i, column=0, sticky="nw", pady=1)
            v_lb = self.label(info, val, self.f_small, wrap=190)
            v_lb.grid(row=i, column=1, sticky="w", padx=(self.S(10), 0), pady=1)
            if it.copy_of and i == 0:   # "Copy of <original>" opens the original
                v_lb.config(fg=ACCENT, cursor="hand2",
                            font=tkfont.Font(font=self.f_small, underline=True))
                v_lb.bind("<Button-1>", lambda e: self.open_file(it.copy_of))
                Tooltip(v_lb, lambda: self.t("v_open_orig"))

        # move this file?
        mv = tk.BooleanVar(value=it.path not in self.excluded)

        def toggle_move():
            (self.excluded.discard if mv.get() else self.excluded.add)(it.path)
            self.refresh_item(it)
            self.show_details(it)
        row = tk.Frame(v, bg=CARD)
        row.pack(fill="x", padx=pad, pady=(self.S(8), 0))
        chk = Check(row, self, mv, command=toggle_move)
        chk.pack(side="left")
        lb = self.label(row, self.t("v_move_this"), self.f_strong)
        lb.pack(side="left", padx=(self.S(8), 0))
        bind_toggle([lb], chk)

        if it.copy_of and not it.conflict:
            ok = tk.Frame(v, bg=GREEN_SOFT)
            ok.pack(fill="x", padx=pad, pady=(self.S(8), 0))
            self.label(ok, self.glyph("check"), self.f_icon_s, fg=GREEN).pack(
                side="left", padx=(self.S(8), 4), pady=self.S(6))
            self.label(ok, self.t("v_identical"), self.f_small, fg=GREEN, wrap=260).pack(
                side="left", pady=self.S(6))

        if it.conflict and it.path not in self.excluded:
            self.conflict_box(v, it, pad)

        btns = tk.Frame(v, bg=CARD)
        btns.pack(side="bottom", fill="x", padx=pad, pady=pad)
        Button(btns, self, self.t("v_open"), lambda: self.open_file(path), "secondary",
               icon="open").pack(side="left")
        Button(btns, self, self.t("v_show"), lambda: show_in_folder(path), "subtle",
               icon="folder").pack(side="left", padx=(self.S(6), 0))

        tk.Frame(v, bg=DIVIDER, height=1).pack(fill="x", padx=pad, pady=(self.S(10), 0))
        area = tk.Frame(v, bg=CARD)
        area.pack(fill="both", expand=True, padx=pad, pady=(self.S(8), 0))
        self.fill_preview(area, path, compact=it.conflict or it.copy_of is not None)

    def conflict_box(self, v, it, pad):
        box = tk.Frame(v, bg=AMBER_SOFT)
        box.pack(fill="x", padx=pad, pady=(self.S(8), 0))
        top = tk.Frame(box, bg=AMBER_SOFT)
        top.pack(fill="x", padx=self.S(10), pady=(self.S(8), self.S(4)))
        self.label(top, self.glyph("warning"), self.f_icon_s, fg=AMBER).pack(side="left", anchor="n")
        msg = self.t("v_conflict", name=it.dest.name, folder=it.group)
        if it.identical:
            msg += " " + self.t("v_conflict_same")
        self.label(top, msg, self.f_small, fg=INK, wrap=250).pack(side="left", padx=(self.S(6), 0))
        keep = tk.BooleanVar(value=self.choice.get(it.path, "keep") == "keep")
        skip = tk.BooleanVar(value=not keep.get())

        def pick(val):
            self.choice[it.path] = val
            self.refresh_item(it)
            self.show_details(it)
        for var, val, title, sub in ((keep, "keep", self.t("v_keep"),
                                      self.t("v_keep_sub", name=free_target(it.dest).name)),
                                     (skip, "skip", self.t("v_skip"), self.t("v_skip_sub"))):
            r = tk.Frame(box, bg=AMBER_SOFT)
            r.pack(fill="x", padx=self.S(10), pady=self.S(2))
            c = Check(r, self, var, command=lambda val=val: pick(val), radio=True)
            c.pack(side="left", anchor="n")
            t = tk.Frame(r, bg=AMBER_SOFT)
            t.pack(side="left", padx=(self.S(8), 0))
            l1 = self.label(t, title, self.f_strong)
            l1.pack(fill="x")
            l2 = self.label(t, sub, self.f_small, fg=MUTED, wrap=230)
            l2.pack(fill="x")
            bind_toggle([l1, l2, t], c)
        Button(box, self, self.t("v_compare"), lambda: self.open_file(it.dest), "subtle",
               icon="open").pack(anchor="w", padx=self.S(4), pady=(0, self.S(4)))

    def fill_preview(self, area, path, compact=False):
        ext = path.suffix.lower()
        maxw, maxh = self.S(310), self.S(110 if compact else 180)
        if builtin_category(ext) == "Images":
            img = None
            if Image is not None:
                try:
                    im = Image.open(path)
                    im.draft("RGB", (maxw, maxh))
                    im.thumbnail((maxw, maxh))
                    img = ImageTk.PhotoImage(im)
                except Exception:  # noqa
                    img = None
            elif ext in (".png", ".gif"):
                try:
                    img = tk.PhotoImage(file=str(path))
                    f = max(1, -(-img.width() // maxw), -(-img.height() // maxh))
                    if f > 1:
                        img = img.subsample(f, f)
                except Exception:  # noqa
                    img = None
            if img is not None:
                self._preview_img = img
                tk.Label(area, image=img, bg=CARD).pack(anchor="center")
                return
            if Image is None and ext != ".svg":
                self.label(area, self.t("v_pillow"), self.f_small, fg=MUTED, wrap=290).pack(fill="x")
                if self.installing:
                    self.label(area, self.t("v_installing"), self.f_small, fg=ACCENT, wrap=290).pack(
                        fill="x", pady=(self.S(6), 0))
                else:
                    Button(area, self, self.t("v_install"), self.install_pillow, "primary").pack(
                        anchor="w", pady=(self.S(6), 0))
                return
        if ext in (".zip", ".jar", ".mrpack"):
            listing = zip_listing(path)
            if listing:
                names, total = listing
                self.label(area, self.t("v_zip", n=self.num(total)), self.f_small, fg=MUTED).pack(fill="x")
                more = self.t("v_more", n=self.num(total - len(names))) if total > len(names) else ""
                self.text_box(area, "\n".join(names) + ("\n" + more if more else ""))
                return
        if ext in (".docx", ".pptx", ".xlsx"):
            txt = office_text(path)
            if txt is not None:
                self.text_box(area, txt.strip() or self.t("v_empty"))
                return
        if ext in TEXT_SUFFIXES:
            txt = text_snippet(path)
            if txt is not None:
                self.text_box(area, txt or self.t("v_empty"))
                return
        self.label(area, self.t("v_none"), self.f_small, fg=MUTED, wrap=290).pack(fill="x")

    def text_box(self, parent, text):
        t = tk.Text(parent, font=self.f_small, wrap="word", bg="#fafafa", fg=INK, relief="flat",
                    highlightthickness=1, highlightbackground=DIVIDER, padx=self.S(8), pady=self.S(6),
                    height=6)
        t.insert("1.0", text)
        t.config(state="disabled")
        t.pack(fill="both", expand=True)

    def install_pillow(self):
        self.installing = True
        self.show_details(self.current_item)

        def work():
            try:
                flags = 0x08000000 if sys.platform.startswith("win") else 0  # no console window
                r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pillow"],
                                   capture_output=True, creationflags=flags, timeout=300)
                ok = r.returncode == 0
            except Exception:  # noqa
                ok = False
            self.root.after(0, lambda: self._pillow_done(ok))
        threading.Thread(target=work, daemon=True).start()

    def _pillow_done(self, ok):
        global Image, ImageTk
        self.installing = False
        if ok:
            try:
                import importlib
                importlib.invalidate_caches()
                user_site = site.getusersitepackages()
                if user_site not in sys.path:
                    sys.path.append(user_site)
                from PIL import Image as _I, ImageTk as _T
                Image, ImageTk = _I, _T
            except Exception:  # noqa
                ok = False
        if not ok:
            messagebox.showinfo(self.t("app"), self.t("v_install_fail"), parent=self.root)
        if self.page == self.page_preview and self.viewer.winfo_exists():
            self.show_details(self.current_item)

    def do_organize(self):
        items = list(self.all_items())
        to_move = [it for it in items if self.will_move(it)]
        if not to_move:
            return
        stayed = [(it.path, "excluded" if it.path in self.excluded else "conflict")
                  for it in items if not self.will_move(it)]
        self.organize_btn.set_text(self.t("working"))
        self.organize_btn.set_enabled(False)
        self.busy = True
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            moved, failed = organize(self.folder, to_move)
        except Exception as e:  # noqa
            self.busy = False
            self.root.config(cursor="")
            messagebox.showerror(self.t("app"), self.t("error", err=e), parent=self.root)
            self.go(self.page_choose, 1)
            return
        self.busy = False
        self.root.config(cursor="")
        skipped = stayed + [(it.path, why) for it, why in failed] + list(self.result["skipped"])
        self.done = {"moved": moved, "skipped": skipped}
        self.go(self.page_done, 3)

    # ---------- step 3: done ----------
    def page_done(self):
        b = self.body
        moved, skipped = self.done["moved"], self.done["skipped"]
        counts = Counter(it.group for it, _ in moved)
        counts = {g: counts[g] for g in sorted(counts, key=group_order)}
        total_size = sum(it.size for it, _ in moved)
        self.primary = lambda: self.go(self.page_choose, 1)

        top = tk.Frame(b, bg=BG)
        top.pack(fill="x")
        s = self.S(56)
        badge = tk.Canvas(top, width=s, height=s, bg=BG, highlightthickness=0)
        badge.pack(side="left")
        self.animate_check(badge, s, 0)
        txt = tk.Frame(top, bg=BG)
        txt.pack(side="left", padx=(self.S(14), 0))
        self.label(txt, self.t("d_title"), self.f_title).pack(fill="x")
        self.label(txt, self.t("d_sub", name=self.folder.name or str(self.folder)), fg=MUTED).pack(fill="x")

        tiles = tk.Frame(b, bg=BG)
        tiles.pack(fill="x", pady=(self.S(16), 0))
        for i, (value, lab) in enumerate(((self.num(len(moved)), self.t("d_moved")),
                                          (human_size(total_size), self.t("d_size")),
                                          (self.num(len(counts)), self.t("d_cats")))):
            tiles.grid_columnconfigure(i, weight=1, uniform="t")
            c = Card(tiles, self, pad=12)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else self.S(6), 0 if i == 2 else self.S(6)))
            tk.Label(c.inner, text=value, font=self.f_stat, fg=ACCENT, bg=CARD).pack()
            tk.Label(c.inner, text=lab, font=self.f_small, fg=MUTED, bg=CARD).pack()

        if skipped:
            self.banner(b, "warn", self.t("d_skipped", n=self.num(len(skipped))),
                        button=(self.t("d_view"), self.show_skipped)).pack(fill="x", pady=(self.S(12), 0))
        dup_size = sum(it.size for it, _ in moved if it.group == DUPLICATES_DIR)
        if dup_size:
            self.banner(b, "tip", self.t("d_tip", size=human_size(dup_size))).pack(fill="x", pady=(self.S(8), 0))

        wrap, inner = self.scroll_area(b)
        wrap.pack(fill="both", expand=True, pady=(self.S(12), 0))
        card = Card(inner, self, pad=6)
        card.pack(fill="x", padx=(0, self.S(2)))
        grid = tk.Frame(card.inner, bg=CARD)
        grid.pack(fill="x", padx=self.S(12), pady=self.S(4))
        grid.grid_columnconfigure(0, weight=1, uniform="c")
        grid.grid_columnconfigure(1, weight=1, uniform="c")
        for i, (g, n) in enumerate(counts.items()):
            r = tk.Frame(grid, bg=CARD)
            r.grid(row=i // 2, column=i % 2, sticky="ew",
                   padx=(0, self.S(16)) if i % 2 == 0 else (self.S(16), 0), pady=self.S(4))
            self.icon_tile(r, self.icon_key(g), size=26, font=self.f_icon_s).pack(side="left")
            self.label(r, self.name(g)).pack(side="left", padx=(self.S(10), 0))
            self.label(r, self.num(n), self.f_strong).pack(side="right")

        Button(self.footer, self, self.t("d_undo"), self.undo, "secondary", icon="undo").pack(side="left")
        Button(self.footer, self, self.t("finish"), self.primary, "primary").pack(side="right")
        Button(self.footer, self, self.t("open"), lambda: open_in_explorer(self.folder),
               "secondary", icon="folder").pack(side="right", padx=(0, self.S(8)))

    def reason_text(self, why):
        if why in ("conflict", "excluded", "missing", "recent"):
            return self.t("why_" + why)
        if why == "large":
            return self.t("why_large", n=self.settings["large_gb"])
        return self.t("why_error", err=why)

    def show_skipped(self):
        win = tk.Toplevel(self.root)
        win.title(self.t("sk_title"))
        win.geometry(f"{self.S(640)}x{self.S(420)}")
        win.configure(bg=BG)
        win.transient(self.root)
        self.label(win, self.t("sk_title"), self.f_h2).pack(fill="x", padx=self.S(16), pady=(self.S(14), self.S(8)))
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=self.S(16))
        tree = ttk.Treeview(frame, columns=("reason",), style="Skip.Treeview")
        tree.heading("#0", text=self.t("sk_name"))
        tree.heading("reason", text=self.t("sk_reason"))
        tree.column("#0", width=self.S(300))
        tree.column("reason", width=self.S(280))
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        for path, why in self.done["skipped"]:
            tree.insert("", "end", text=path.name, values=(self.reason_text(why),))
        Button(win, self, self.t("close"), win.destroy, "secondary").pack(pady=self.S(12))
        win.bind("<Escape>", lambda e: win.destroy())

    def animate_check(self, c, s, i):
        if not c.winfo_exists():
            return
        steps = 10
        k = 1 - (1 - min(1.0, i / steps)) ** 3
        c.delete("all")
        r = (s / 2 - 2) * k
        c.create_oval(s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r, fill=GREEN, outline=GREEN)
        if i >= steps:
            c.create_line(s * .30, s * .52, s * .44, s * .66, s * .71, s * .37, fill="white",
                          width=max(3, self.S(4)), capstyle="round", joinstyle="round")
            return
        self.root.after(18, lambda: self.animate_check(c, s, i + 1))

    # ---------- settings ----------
    def open_settings(self):
        if self.busy:
            return
        self.go(self.page_settings, self.step)

    def page_settings(self):
        b, st = self.body, self.settings
        self.primary = self.back = lambda: self.go(self.page_choose, 1)
        self.label(b, self.t("settings"), self.f_title).pack(fill="x")
        self.label(b, self.t("set_sub"), fg=MUTED).pack(fill="x", pady=(self.S(4), self.S(4)))
        wrap, inner = self.scroll_area(b)
        wrap.pack(fill="both", expand=True)

        def section(title, sub=None):
            self.label(inner, title, self.f_h2).pack(fill="x", pady=(self.S(14), self.S(2 if sub else 6)))
            if sub:
                self.label(inner, sub, self.f_small, fg=MUTED).pack(fill="x", pady=(0, self.S(6)))
            card = Card(inner, self, pad=4)
            card.pack(fill="x", padx=(0, self.S(2)))
            return card.inner

        def divider(parent):
            tk.Frame(parent, bg=DIVIDER, height=1).pack(fill="x", padx=self.S(12))

        def row(parent, icon, title, sub=None, var=None, command=None, enabled=True,
                right_text=None):
            r = tk.Frame(parent, bg=CARD)
            r.pack(fill="x", padx=self.S(12), pady=self.S(9))
            parts = [r]
            if icon:
                tile = self.icon_tile(r, icon, size=32, font=self.f_icon_s)
                tile.pack(side="left")
                parts.append(tile)
            mid = tk.Frame(r, bg=CARD)
            mid.pack(side="left", fill="x", expand=True, padx=(self.S(12) if icon else 0, 0))
            t1 = self.label(mid, title, self.f_strong)
            t1.pack(fill="x")
            parts += [mid, t1]
            if sub:
                t2 = self.label(mid, sub, self.f_small, fg=MUTED, wrap=560)
                t2.pack(fill="x")
                parts.append(t2)
            if var is not None:
                chk = Check(r, self, var, command=command, enabled=enabled)
                chk.pack(side="right", before=mid)
                if right_text:
                    self.label(r, right_text, self.f_small, fg=MUTED).pack(side="right", padx=self.S(10))
                bind_toggle(parts, chk)
            return r

        def setter(key, var):
            def f():
                st[key] = var.get()
                save_settings(st)
            return f

        # categories
        cats = section(self.t("set_cats"))
        for i, g in enumerate(BUILTIN_GROUPS):
            if i:
                divider(cats)
            var = tk.BooleanVar(value=st["groups"].get(g, False))
            if g == OTHER:
                sub = self.t("other_desc")
            elif g == MINECRAFT:
                sub = self.t("mc_desc")
            else:
                sub = ", ".join(sorted(e.lstrip(".").upper() for e in CATEGORIES[g])[:8])
                if len(CATEGORIES[g]) > 8:
                    sub += ", …"

            def cmd(g=g, var=var):
                st["groups"][g] = var.get()
                save_settings(st)
            row(cats, self.icon_key(g), self.name(g), sub, var, cmd)

        # custom rules
        rules = section(self.t("set_rules"), self.t("set_rules_sub"))
        if not st["rules"]:
            self.label(rules, self.t("set_rules_none"), self.f_small, fg=MUTED).pack(
                fill="x", padx=self.S(12), pady=self.S(10))
        for i, rule in enumerate(st["rules"]):
            if i:
                divider(rules)
            r = tk.Frame(rules, bg=CARD)
            r.pack(fill="x", padx=self.S(12), pady=self.S(6))
            self.icon_tile(r, "rule", size=28, font=self.f_icon_s).pack(side="left")
            self.label(r, rule["ext"].upper().lstrip("."), self.f_strong).pack(side="left", padx=(self.S(12), 0))
            self.label(r, f"   →   {rule['folder']}/", fg=ACCENT).pack(side="left")

            def remove(rule=rule):
                st["rules"].remove(rule)
                save_settings(st)
                self.render()
            Button(r, self, "", remove, "subtle", icon="close").pack(side="right")
        divider(rules)
        add = tk.Frame(rules, bg=CARD)
        add.pack(fill="x", padx=self.S(12), pady=self.S(10))
        ext_var, folder_var = tk.StringVar(), tk.StringVar()
        self.label(add, self.t("set_rule_ext"), self.f_small, fg=MUTED).pack(side="left")
        e1 = ttk.Entry(add, textvariable=ext_var, width=9, font=self.f_body)
        e1.pack(side="left", padx=(self.S(6), self.S(10)))
        self.label(add, "→", fg=MUTED).pack(side="left")
        self.label(add, self.t("set_rule_folder"), self.f_small, fg=MUTED).pack(side="left", padx=(self.S(10), 0))
        e2 = ttk.Entry(add, textvariable=folder_var, width=18, font=self.f_body)
        e2.pack(side="left", padx=self.S(6))

        def add_rule():
            ext = ext_var.get().strip().lower()
            if ext and not ext.startswith("."):
                ext = "." + ext
            folder = folder_var.get().strip()
            if not valid_ext(ext):
                messagebox.showinfo(self.t("settings"), self.t("set_rule_bad_ext"), parent=self.root)
                return
            if not valid_folder(folder):
                messagebox.showinfo(self.t("settings"), self.t("set_rule_bad_folder"), parent=self.root)
                return
            st["rules"] = [r for r in st["rules"] if r["ext"] != ext] + [{"ext": ext, "folder": folder}]
            save_settings(st)
            self.render()
        Button(add, self, self.t("set_rule_add"), add_rule, "secondary", icon="add").pack(side="right")
        e2.bind("<Return>", lambda e: add_rule())

        # duplicates & clashes
        dup = section(self.t("set_dups_title"))
        dv = tk.BooleanVar(value=st["duplicates"])
        row(dup, DUPLICATES_DIR, self.t("set_dups"), self.t("set_dups_sub"), dv, setter("duplicates", dv))
        divider(dup)
        clash = tk.Frame(dup, bg=CARD)
        clash.pack(fill="x", padx=self.S(12), pady=self.S(9))
        hdr = tk.Frame(clash, bg=CARD)
        hdr.pack(fill="x")
        self.icon_tile(hdr, "warning", size=32, font=self.f_icon_s).pack(side="left")
        self.label(hdr, self.t("set_clash"), self.f_strong).pack(side="left", padx=(self.S(12), 0))
        kv = tk.BooleanVar(value=st["conflicts"] == "keep")
        sv = tk.BooleanVar(value=st["conflicts"] == "skip")

        def set_clash(val):
            st["conflicts"] = val
            kv.set(val == "keep")
            sv.set(val == "skip")
            for c in radios:
                c.draw()
            save_settings(st)
        radios = []
        for var, val, text in ((kv, "keep", self.t("set_clash_keep")), (sv, "skip", self.t("set_clash_skip"))):
            r = tk.Frame(clash, bg=CARD)
            r.pack(fill="x", padx=(self.S(44), 0), pady=(self.S(6), 0))
            c = Check(r, self, var, command=lambda val=val: set_clash(val), radio=True)
            c.pack(side="left")
            radios.append(c)
            lb = self.label(r, text)
            lb.pack(side="left", padx=(self.S(8), 0))
            bind_toggle([lb, r], c)

        # exclusions
        ex = section(self.t("set_excl"))
        rv = tk.BooleanVar(value=st["skip_recent"])
        row(ex, "clock", self.t("set_recent"), self.t("set_recent_sub"), rv, setter("skip_recent", rv))
        divider(ex)
        lv = tk.BooleanVar(value=st["skip_large"])
        lr = row(ex, "size", self.t("set_large"), None, lv, setter("skip_large", lv))
        size_var = tk.StringVar(value=str(st["large_gb"]))
        spin_box = tk.Frame(lr, bg=CARD)
        spin_box.pack(side="right", padx=self.S(10))
        self.label(spin_box, self.t("set_large_unit"), self.f_small, fg=MUTED).pack(side="right", padx=(self.S(6), 0))
        spin = ttk.Spinbox(spin_box, from_=1, to=1000, width=5, textvariable=size_var, font=self.f_body)
        spin.pack(side="right")

        def save_size(*_):
            try:
                st["large_gb"] = max(1, min(1000, int(size_var.get())))
                save_settings(st)
            except ValueError:
                pass
        size_var.trace_add("write", save_size)
        divider(ex)
        on = tk.BooleanVar(value=True)
        row(ex, "folder", self.t("set_folders"), None, on, None, enabled=False, right_text=self.t("always_on"))
        divider(ex)
        row(ex, "shield", self.t("set_hidden"), None, on, None, enabled=False, right_text=self.t("always_on"))

        # safety
        sf = section(self.t("set_safety"))
        for i, key in enumerate(("set_never", "set_preview", "set_history")):
            if i:
                divider(sf)
            row(sf, ("shield", "search", "undo")[i], self.t(key), None, on, None, enabled=False,
                right_text=self.t("always_on"))

        Button(self.footer, self, self.t("finish"), self.primary, "primary").pack(side="right")

    # ---------- shared actions ----------
    def toggle_lang(self):
        self.lang = "en" if self.lang == "nl" else "nl"
        self.settings["lang"] = self.lang
        save_settings(self.settings)
        self.render()

    def undo(self):
        try:
            runs = load_runs(self.folder)
        except Exception:  # noqa
            runs = []
        if not runs:
            messagebox.showinfo(self.t("app"), self.t("no_undo"), parent=self.root)
            return
        n = len(runs[-1]["actions"])
        if not messagebox.askyesno(self.t("undo_title"), self.t("undo_q", n=self.num(n)), parent=self.root):
            return
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            res = undo_last(self.folder)
        except Exception as e:  # noqa
            self.root.config(cursor="")
            messagebox.showerror(self.t("app"), self.t("error", err=e), parent=self.root)
            self.go(self.page_choose, 1)
            return
        self.root.config(cursor="")
        msg = self.t("undo_done")
        if res and res["missing"]:
            msg += "\n\n" + self.t("undo_missing", n=self.num(res["missing"]))
        if res and res["renamed"]:
            msg += "\n\n" + self.t("undo_renamed", n=self.num(res["renamed"]))
        messagebox.showinfo(self.t("app"), msg, parent=self.root)
        self.go(self.page_choose, 1)


def main():
    if sys.platform.startswith("win"):
        try:  # crisp text on high-resolution screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
