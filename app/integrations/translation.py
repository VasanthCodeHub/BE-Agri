"""Tamil translation via MyMemory's free API.

Used only to fill in `vehicle_types.name_ta`, which is a static lookup value —
we translate once, store the result, and never call this at request time.

MyMemory is free and needs no key (limits: ~5000 chars/day anonymous). Good
enough for a handful of short names; swap the URL for a paid provider when the
taxonomy grows.

    app -> "translate 'Tractor' to Tamil" -> MyMemory
    app <- "டிராக்டர்"                    <- MyMemory
"""

from __future__ import annotations

import httpx

_MYMEMORY_URL = "https://api.mymemory.translated.net/get"
_SOURCE = "en"
_TARGET = "ta"
_TIMEOUT_SECONDS = 10.0


class TranslationError(RuntimeError):
    """Raised when the free API is unreachable or returns no translation."""


async def translate_to_tamil(
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Translate one English string to Tamil.

    `client` is injectable so callers can share one httpx pool and tests can
    hand in a fake. Returns the raw translated text.
    """
    params = {"q": text, "langpair": f"{_SOURCE}|{_TARGET}"}
    if client is not None:
        response = await client.get(_MYMEMORY_URL, params=params)
    else:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
            response = await http.get(_MYMEMORY_URL, params=params)
    if response.status_code != 200:
        raise TranslationError(f"MyMemory returned HTTP {response.status_code}")
    body = response.json()
    translated = (body.get("responseData") or {}).get("translatedText")
    if not translated:
        raise TranslationError(f"MyMemory returned no translation for {text!r}")
    return translated
