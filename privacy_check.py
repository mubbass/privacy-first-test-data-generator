"""Privacy check between Phase 2 (local concealment) and Phase 3 (generation).

Rule-based and deterministic: it does not trust the local LLM, it verifies its output.
If any original value is still present in the concealed record, nothing is passed on.
"""

import re
import unicodedata

# Generic address words that may also appear in fictitious data
GENERIC_TOKENS = {"str", "strasse", "weg", "platz", "allee", "gasse", "ring", "damm", "ufer", "chaussee"}

# Shorter parts (e.g. house number "13") are not checked on their own
MIN_TOKEN_LENGTH = 4


def _transliterate(text):
    # ü -> ue, case-insensitive
    text = text.casefold()
    for umlaut, replacement in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}.items():
        text = text.replace(umlaut, replacement)
    return text


def _stripDiacritics(text):
    # ü -> u, é -> e, case-insensitive
    text = text.casefold().replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _collectValues(data):
    # collect all values (not the field names) from the JSON
    if isinstance(data, dict):
        return [v for value in data.values() for v in _collectValues(value)]
    if isinstance(data, list):
        return [v for item in data for v in _collectValues(item)]
    return [str(data)]


def _parseAllowed(allowedValues):
    # "Mitglied, Kunde" -> {"mitglied", "kunde"} (normalized)
    return {_transliterate(v.strip()) for v in (allowedValues or "").split(",") if v.strip()}


def findLeakedValues(originalText, anonymizedData, allowedValues=""):
    """Return all original values that are still present in the concealed record.

    Values listed in allowedValues (e.g. categories such as "Mitglied") are deliberately not checked.
    """
    fields = [f.strip() for f in re.split(r"[,;\t\n]", originalText.strip().strip("()[]{}")) if f.strip()]
    candidates = set(fields)
    for field in fields:
        for token in re.findall(r"\w+", field):
            if len(token) >= MIN_TOKEN_LENGTH and _transliterate(token) not in GENERIC_TOKENS:
                candidates.add(token)

    allowed = _parseAllowed(allowedValues)
    candidates = {c for c in candidates if _transliterate(c) not in allowed}

    anonymizedText = " ".join(_collectValues(anonymizedData))
    leaked = []
    for candidate in candidates:
        for normalize in (_transliterate, _stripDiacritics):
            pattern = r"(?<!\w)" + re.escape(normalize(candidate)) + r"(?!\w)"
            if re.search(pattern, normalize(anonymizedText)):
                leaked.append(candidate)
                break
    return sorted(leaked)
