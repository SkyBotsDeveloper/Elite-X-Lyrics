from __future__ import annotations

import re

from elite_x_lyrics.utils import contains_devanagari


INDEPENDENT_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऋ": "ri",
}

VOWEL_SIGNS = {
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ृ": "ri",
}

CONSONANTS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "क़": "q",
    "ख़": "kh",
    "ग़": "gh",
    "ज़": "z",
    "ड़": "r",
    "ढ़": "rh",
    "फ़": "f",
    "ळ": "l",
}

MARKS = {
    "ं": "n",
    "ँ": "n",
    "ः": "h",
    "़": "",
    "ऽ": "'",
}

VIRAMA = "्"


def _transliterate_word(word: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(word):
        char = word[index]

        if char in INDEPENDENT_VOWELS:
            result.append(INDEPENDENT_VOWELS[char])
            index += 1
            continue

        if char in CONSONANTS:
            base = CONSONANTS[char]
            next_char = word[index + 1] if index + 1 < len(word) else ""
            if next_char == VIRAMA:
                result.append(base)
                index += 2
                continue
            if next_char in VOWEL_SIGNS:
                result.append(base + VOWEL_SIGNS[next_char])
                index += 2
                continue
            result.append(base + "a")
            index += 1
            continue

        if char in VOWEL_SIGNS:
            result.append(VOWEL_SIGNS[char])
        elif char in MARKS:
            result.append(MARKS[char])
        else:
            result.append(char)
        index += 1

    text = "".join(result)
    text = re.sub(r"([bcdfghjklmnpqrstvwxyz])a(?=$)", r"\1", text)
    text = re.sub(r"([bcdfghjklmnpqrstvwxyz])a(?=[^a-z])", r"\1", text)
    text = text.replace("va", "wa")
    text = text.replace("v ", "w ")
    text = text.replace("chha", "chha")
    return text


def to_hinglish(value: str) -> str:
    if not value or not contains_devanagari(value):
        return value

    parts = re.split(r"(\s+)", value)
    converted = []
    for part in parts:
        if not part or part.isspace():
            converted.append(part)
            continue
        converted.append(_transliterate_word(part))
    return "".join(converted).lower()
