import re
import unicodedata

WINDOW = 40

_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_SYMBOLS = "%‰°€$£¥₹¢+#&=<>±×÷*§©®™≥≤≠"
_ALNUM = rf"(?:(?![{_CJK}])[^\W_])+"
_JOIN = r"(?:[-./:@_]|(?<=\d),(?=\d))"

_TOKEN = re.compile(
    rf"(?P<cjk>[{_CJK}]+)"
    rf"|(?P<word>(?:(?<!\w)-(?=\d))?{_ALNUM}(?:{_JOIN}{_ALNUM})*)"
    rf"|(?P<sym>[{_SYMBOLS}])"
)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_APOSTROPHES = re.compile("['‘’ʼ`´]")
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_THOUSANDS = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_ALPHA_COMPOUND = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")
_FIELD_BREAK = re.compile(r"[\r\n\t|]+")
_TAB_BREAK = re.compile(r"[\t|]+")


def normalise(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = text.translate(_DASHES).translate(_INVISIBLE)
    text = _APOSTROPHES.sub(" ", text)
    return _CJK_GAP.sub("", text)


def tokenize(text):
    tokens = []
    for match in _TOKEN.finditer(normalise(text)):
        kind, value, start = match.lastgroup, match.group(), match.start()
        if kind == "word" and _ALPHA_COMPOUND.fullmatch(value):
            for part in value.split("-"):
                tokens.append(("word", part, start, start + len(part)))
                start += len(part) + 1
            continue
        if kind == "word" and _THOUSANDS.fullmatch(value):
            value = value.replace(",", "")
        tokens.append((kind, value, match.start(), match.end()))
    return tokens


def _fields(text, join_lines):
    if join_lines:
        return _TAB_BREAK.split(re.sub(r"[\r\n]+", " ", str(text)))
    return _FIELD_BREAK.split(str(text))


def _same(needle, token):
    if needle[0] != token[0]:
        return False
    return needle[1] in token[1] if needle[0] == "cjk" else needle[1] == token[1]


def _ordered(needle, tokens, window):
    reach = [i for i, token in enumerate(tokens) if _same(needle[0], token)]
    for part in needle[1:]:
        reach = [
            j for j, token in enumerate(tokens)
            if _same(part, token)
            and any(i < j and token[2] - tokens[i][3] <= window for i in reach)
        ]
        if not reach:
            return False
    return bool(reach)


def contains(text, keyword, join_lines=False, window=WINDOW):
    needle = tokenize(re.sub(r"\s+", " ", str(keyword)))
    if not needle:
        raise ValueError(f"keyword has no matchable content: {keyword!r}")
    return any(
        _ordered(needle, tokenize(field), window)
        for field in _fields(text, join_lines)
    )


def contains_all(text, keywords, join_lines=False, window=WINDOW):
    return all(contains(text, keyword, join_lines, window) for keyword in keywords)
