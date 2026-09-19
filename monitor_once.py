"""
Cyberpunk-Monitor fuer GitHub Actions.

Anders als das Laptop-Skript hat dieses hier KEINE Dauerschleife.
GitHub startet es per Cron alle 5 Minuten neu, von null. Damit sich
das Skript trotzdem an den letzten bekannten Zustand erinnert, liegt
der in state.json, die am Ende jedes Laufs vom Workflow zurueck ins
Repo committet wird.
"""

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path

import requests

URL = "https://www.spielraum.co.at/de/andere-tcg/cyberpunk"

START_MARKE = "Im Cyberpunk Trading Card Game"
END_MARKE = "Verkaufe deine Karten"

PRODUKTE = [
    "Welcome to Night City Boosterdisplay (ENG)",
    "Welcome to Night City Booster (ENG)",
    "Welcome to Night City PreRelease Kit (ENG)",
    "Welcome to Night City Beta Boosterdisplay (ENG)",
]

STATE_PATH = Path("state.json")
KONTAKT = os.environ.get("MONITOR_CONTACT", "privater Einzelnutzer, kein Kontakt hinterlegt")
HEADERS = {
    "User-Agent": f"SpielRaum-Restock-Monitor/1.0 ({KONTAKT})",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-AT,de;q=0.9",
}


def push(titel, text, prio="default", tags="bell"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC fehlt (Repository-Secret pruefen), kein Push moeglich.")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=text.encode("utf-8"),
            headers={"Title": titel, "Priority": prio, "Tags": tags},
            timeout=15,
        )
    except Exception as e:
        print(f"Push fehlgeschlagen: {e}")


def nur_text(roh):
    roh = re.sub(r"(?is)<(script|style).*?</\1>", " ", roh)
    roh = re.sub(r"(?s)<[^>]+>", " ", roh)
    roh = html.unescape(roh)
    return re.sub(r"\s+", " ", roh).strip()


def raster_ausschneiden(text):
    a = text.find(START_MARKE)
    if a == -1:
        return None
    b = text.find(END_MARKE, a + 1)
    if b == -1:
        return None
    return text[a:b].strip()


def zustaende_lesen(raster):
    ergebnis = {}
    for name in PRODUKTE:
        i = raster.find(name)
        ergebnis[name] = raster[i:i + 220] if i != -1 else "NICHT GEFUNDEN"
    return ergebnis


def sieht_bestellbar_aus(segment):
    s = segment.lower()
    hat_preis = "€" in segment
    hat_info = "info" in s
    hat_korb = "warenkorb" in s or "kaufen" in s or "bestellen" in s
    return hat_korb or (hat_preis and not hat_info)


def state_laden():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def state_speichern(daten):
    STATE_PATH.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    state = state_laden()
    alt = state.get("zustaende") or {}
    etag = state.get("etag")
    blocks = state.get("consecutive_blocks", 0)

    kopf = dict(HEADERS)
    if etag:
        kopf["If-None-Match"] = etag

    try:
        antwort = requests.get(URL, headers=kopf, timeout=20)
    except Exception as e:
        print(f"Netzwerkfehler: {e}")
        sys.exit(0)

    if antwort.status_code in (403, 429, 503):
        blocks += 1
        print(f"Blockiert, HTTP {antwort.status_code}, Serie {blocks}")
        if blocks in (1, 12, 60):
            push(
                "Monitor blockiert",
                f"HTTP {antwort.status_code} von SpielRaum. Seite selbst pruefen: {URL}",
                "high",
                "warning",
            )
        state["consecutive_blocks"] = blocks
        state_speichern(state)
        return

    if antwort.status_code == 304:
        print("304, unveraendert.")
        state["consecutive_blocks"] = 0
        state_speichern(state)
        return

    if antwort.status_code != 200:
        print(f"Unerwarteter Status {antwort.status_code}")
        return

    state["consecutive_blocks"] = 0
    state["etag"] = antwort.headers.get("ETag")

    raster = raster_ausschneiden(nur_text(antwort.text))
    if raster is None:
        print("Textmarken nicht gefunden, Seitenaufbau hat sich geaendert.")
        push(
            "Seitenaufbau geaendert",
            f"Produktbereich nicht mehr auffindbar, moeglicherweise Umbau zum "
            f"Verkaufsstart. Seite selbst ansehen: {URL}",
            "high",
            "warning",
        )
        state_speichern(state)
        return

    neu = zustaende_lesen(raster)

    if not alt:
        print("Erster Lauf, Ausgangszustand gespeichert.")
        push("Monitor gestartet", f"Ueberwache {len(PRODUKTE)} Artikel.\n{URL}")
        state["zustaende"] = neu
        state_speichern(state)
        return

    aenderungen = [n for n in PRODUKTE if neu.get(n) != alt.get(n)]

    if aenderungen:
        dringend = any(sieht_bestellbar_aus(neu[n]) for n in aenderungen)
        zeilen = []
        for n in aenderungen:
            zeilen.append(
                f"{n}\nvorher: {(alt.get(n) or '')[:120]}\njetzt:  {neu[n][:120]}"
            )
        meldung = "\n\n".join(zeilen) + "\n\n" + URL
        print("AENDERUNG: " + ", ".join(aenderungen))
        push(
            "BESTELLBAR?" if dringend else "Aenderung auf der Seite",
            meldung,
            "urgent" if dringend else "high",
            "rotating_light" if dringend else "eyes",
        )
        state["zustaende"] = neu
        state_speichern(state)
    else:
        print("Keine Aenderung.")
        state_speichern(state)


if __name__ == "__main__":
    main()
