"""
The languages a reader can choose, and what each one needs to render.

One table, on the server, served to the extension through /config. The picker in
the panel is built from it, so adding a language is a one-line change here rather
than an edit in two places that drift apart.

`script` is not decoration. Arabic and Hebrew need the panel flipped to
right-to-left; Chinese, Japanese and Korean need their own font stacks and break
differently at the end of a line; Thai has no spaces at all. Rendering all of them
with the Latin defaults produces text that is technically correct and unreadable.
"""
from __future__ import annotations

# code, English name, endonym, script
_ROWS = [
    ("en",    "English",              "English",          "latn"),
    ("es",    "Spanish",              "Espanol",          "latn"),
    ("fr",    "French",               "Francais",         "latn"),
    ("de",    "German",               "Deutsch",          "latn"),
    ("it",    "Italian",              "Italiano",         "latn"),
    ("pt",    "Portuguese",           "Portugues",        "latn"),
    ("nl",    "Dutch",                "Nederlands",       "latn"),
    ("pl",    "Polish",               "Polski",           "latn"),
    ("ro",    "Romanian",             "Romana",           "latn"),
    ("cs",    "Czech",                "Cestina",          "latn"),
    ("hu",    "Hungarian",            "Magyar",           "latn"),
    ("sv",    "Swedish",              "Svenska",          "latn"),
    ("nb",    "Norwegian",            "Norsk",            "latn"),
    ("da",    "Danish",               "Dansk",            "latn"),
    ("fi",    "Finnish",              "Suomi",            "latn"),
    ("tr",    "Turkish",              "Turkce",           "latn"),
    ("id",    "Indonesian",           "Bahasa Indonesia", "latn"),
    ("ms",    "Malay",                "Bahasa Melayu",    "latn"),
    ("vi",    "Vietnamese",           "Tieng Viet",       "latn"),
    ("tl",    "Filipino",             "Filipino",         "latn"),
    ("sw",    "Swahili",              "Kiswahili",        "latn"),
    ("hr",    "Croatian",             "Hrvatski",         "latn"),
    ("az",    "Azerbaijani",          "Azerbaycanca",     "latn"),

    ("ru",    "Russian",              "Russkiy",          "cyrl"),
    ("uk",    "Ukrainian",            "Ukrainska",        "cyrl"),
    ("bg",    "Bulgarian",            "Balgarski",        "cyrl"),
    ("sr",    "Serbian",              "Srpski",           "cyrl"),

    ("el",    "Greek",                "Ellinika",         "grek"),
    ("hy",    "Armenian",             "Hayeren",          "armn"),
    ("ka",    "Georgian",             "Kartuli",          "geor"),

    ("fa",    "Persian (Farsi)",      "Farsi",            "arab"),
    ("ar",    "Arabic",               "al-Arabiyya",      "arab"),
    ("ur",    "Urdu",                 "Urdu",             "arab"),
    ("ps",    "Pashto",               "Pashto",           "arab"),
    ("ckb",   "Kurdish (Sorani)",     "Kurdi",            "arab"),
    ("he",    "Hebrew",               "Ivrit",            "hebr"),

    ("hi",    "Hindi",                "Hindi",            "deva"),
    ("mr",    "Marathi",              "Marathi",          "deva"),
    ("ne",    "Nepali",               "Nepali",           "deva"),
    ("bn",    "Bengali",              "Bangla",           "beng"),
    ("pa",    "Punjabi",              "Panjabi",          "guru"),
    ("ta",    "Tamil",                "Tamil",            "taml"),
    ("te",    "Telugu",               "Telugu",           "telu"),

    ("th",    "Thai",                 "Thai",             "thai"),
    ("zh",    "Chinese (Simplified)", "Zhongwen",         "hans"),
    ("zh-TW", "Chinese (Traditional)","Zhongwen",         "hant"),
    ("ja",    "Japanese",             "Nihongo",          "jpan"),
    ("ko",    "Korean",               "Hangugeo",         "kore"),
]

# Scripts written right to left. The panel flips direction, alignment and the
# translation lane's border for these.
RTL_SCRIPTS = {"arab", "hebr", "thaa"}

LANGUAGES = [
    {"code": c, "name": n, "native": e, "script": s, "rtl": s in RTL_SCRIPTS}
    for c, n, e, s in _ROWS
]

BY_CODE = {row["code"]: row for row in LANGUAGES}


def get(code: str) -> dict | None:
    """Look up a language, tolerating 'fa-IR', 'en-US' and 'ZH-tw'."""
    if not code or not isinstance(code, str):
        return None
    code = code.strip()
    if code in BY_CODE:
        return BY_CODE[code]
    parts = code.replace("_", "-").split("-")
    if len(parts) > 1:
        # zh-TW is a distinct entry; fa-IR is just fa.
        regioned = f"{parts[0].lower()}-{parts[-1].upper()}"
        if regioned in BY_CODE:
            return BY_CODE[regioned]
    return BY_CODE.get(parts[0].lower())


def name_of(code: str, fallback: str = "") -> str:
    row = get(code)
    return row["name"] if row else fallback


def is_rtl(code: str) -> bool:
    row = get(code)
    return bool(row and row["rtl"])


def script_of(code: str) -> str:
    row = get(code)
    return row["script"] if row else "latn"
