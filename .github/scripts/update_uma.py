#!/usr/bin/env python3
"""
Actualiza uma-data.json con el valor más reciente de la UMA publicado por la CSJN.

La CSJN no publica la UMA en un solo lugar, ni al mismo tiempo:
  - https://www.csjn.gov.ar/transparencia/uma — la lista de resoluciones, que
    a veces se actualiza semanas después.
  - https://www.csjn.gov.ar/novedades — la noticia "Actualización del valor de
    la UMA", que suele salir el mismo día de la resolución.
Por eso se revisan las dos y se toma la resolución más nueva (por año y
número). Nunca se retrocede a una resolución anterior a la ya guardada.

Todo se descarga vía r.jina.ai, que renderiza el JavaScript de la CSJN.
Además del JSON, se actualiza el "último valor conocido" de index.html, que es
lo que se ve si la consulta en vivo falla.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests

JINA_BASE = "https://r.jina.ai/"
CSJN = "https://www.csjn.gov.ar"
CSJN_UMA_PAGE = CSJN + "/transparencia/uma"
CSJN_NOVEDADES = CSJN + "/novedades"
CSJN_DOC_URL = CSJN + "/documentos/descargar?ID="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UMA-updater/1.0; +https://github.com/victor-olivari/cotizador-honorarios-abogacia-chaco)",
    "Accept": "text/plain, text/html, */*",
}
TIMEOUT = 40
ROOT = Path(__file__).parent.parent.parent
OUTPUT_FILE = ROOT / "uma-data.json"
INDEX_FILE = ROOT / "index.html"

VALOR_MIN, VALOR_MAX = 50_000, 5_000_000


def fetch_jina(url: str) -> str:
    """Descarga una URL usando r.jina.ai y devuelve el texto plano."""
    resp = requests.get(JINA_BASE + url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def a_pesos(s: str) -> int:
    return int(re.sub(r"[\s\.]", "", s))


def orden(res_num: int, anio: int) -> tuple[int, int]:
    return (anio, res_num)


def res_de_json(d: dict) -> tuple[int, int] | None:
    """(anio, número) de la resolución guardada: 'Res. SGA N° 1930/2026'."""
    m = re.search(r"(\d+)\s*/\s*(20\d\d)", d.get("res", ""))
    return orden(int(m.group(1)), int(m.group(2))) if m else None


# ── Fuente 1: Transparencia / UMA ──────────────────────────────────────────
def candidato_transparencia() -> dict | None:
    """Primera fila de la lista: la resolución más reciente que allí figura."""
    txt = fetch_jina(CSJN_UMA_PAGE)
    m = re.search(
        r"(\d{1,2} de \w+ de (20\d\d)).{0,200}?Resoluci[oó]n\s+(\d+).{0,120}?descargar\?ID=(\d+)",
        txt,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise ValueError("No se encontró ninguna resolución en Transparencia/UMA.")
    return {
        "fuente": "transparencia",
        "res_num": int(m.group(3)),
        "anio": int(m.group(2)),
        "docId": int(m.group(4)),
    }


# ── Fuente 2: Novedades ────────────────────────────────────────────────────
def candidato_novedades() -> dict | None:
    """La noticia más reciente sobre la UMA, con su resolución, PDF y valor."""
    listado = fetch_jina(CSJN_NOVEDADES)
    m = re.search(r"\[[^\]]*\bUMA\b[^\]]*\]\((https://www\.csjn\.gov\.ar/novedades/detalle/\d+)\)", listado)
    if not m:
        print("  Novedades: no hay noticias recientes sobre la UMA.")
        return None
    detalle = re.sub(r"\s+", " ", fetch_jina(m.group(1)))
    rm = re.search(r"resoluci[oó]n[^\[\d]{0,80}\[?(\d+)\s*/\s*(20\d\d)\]?\(?[^)]*?descargar\?ID=(\d+)", detalle, re.IGNORECASE)
    if not rm:
        raise ValueError(f"La noticia {m.group(1)} no trae número de resolución y PDF.")
    cand = {
        "fuente": "novedades",
        "res_num": int(rm.group(1)),
        "anio": int(rm.group(2)),
        "docId": int(rm.group(3)),
    }
    vm = re.search(r"suma de \$\s*([\d\.]+)\s+a partir del?\s+(.+?\d{4})", detalle, re.IGNORECASE)
    if vm:
        cand["valor"] = a_pesos(vm.group(1))
        cand["vig"] = vm.group(2).strip()
    return cand


# ── PDF de la resolución ───────────────────────────────────────────────────
def parse_pdf(pdf_text: str) -> tuple[int, str]:
    """Valor y vigencia de la parte resolutiva: '($105.991) a partir del primero de agosto de 2026'."""
    text = re.sub(r"\s+", " ", pdf_text)
    idx = text.upper().find("SE RESUELVE")
    if idx < 0:
        idx = text.upper().find("RESUELVE")
    if idx < 0:
        raise ValueError("No se encontró la sección 'RESUELVE' en el PDF.")
    section = text[idx : idx + 800]
    m = re.search(r"\(\s*\$\s*([\d\.\s]+?)\s*\)", section) or re.search(
        r"\$\s*([\d\.\s]+?)(?:\s*[\)\\]|\s+a partir)", section
    )
    if not m:
        raise ValueError(f"No se encontró el valor $ en la parte resolutiva:\n{section[:300]}")
    valor = a_pesos(m.group(1))
    vm = re.search(r"a partir del?\s+(.+?\d{4})", section, re.IGNORECASE)
    return valor, (vm.group(1).strip() if vm else "?")


def completar(cand: dict) -> dict:
    """Lee el valor del PDF; si el PDF no se puede leer, usa el de la noticia."""
    try:
        valor, vig = parse_pdf(fetch_jina(CSJN_DOC_URL + str(cand["docId"])))
        if "valor" in cand and cand["valor"] != valor:
            print(f"  ⚠ La noticia dice ${cand['valor']:,} y el PDF ${valor:,}: manda el PDF.")
        cand["valor"], cand["vig"] = valor, vig
    except Exception as e:
        if "valor" not in cand:
            raise
        print(f"  ⚠ No se pudo leer el PDF ({e}); se usa el valor de la noticia.")
    if not (VALOR_MIN < cand["valor"] < VALOR_MAX):
        raise ValueError(f"Valor fuera de rango: {cand['valor']}")
    return cand


def load_current() -> dict:
    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data: dict) -> None:
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def vig_legible(vig: str) -> str:
    return re.sub(r"^primero\s+de\s+", "1° de ", vig, flags=re.IGNORECASE)


def sync_index(data: dict) -> None:
    """Pone el valor nuevo como 'último valor conocido' de index.html."""
    if not INDEX_FILE.exists():
        return
    html = INDEX_FILE.read_text(encoding="utf-8")
    nuevo = html
    nuevo = re.sub(r"(var UMA_FALLBACK\s*=\s*)\d+;", rf"\g<1>{data['valor']};", nuevo)
    nuevo = re.sub(r'(var UMA_RES\s*=\s*)"[^"]*";', lambda m: f'{m.group(1)}"{data["res"]}";', nuevo)
    nuevo = re.sub(r'(var UMA_VIG\s*=\s*)"[^"]*";', lambda m: f'{m.group(1)}"{vig_legible(data["vig"])}";', nuevo)
    nuevo = re.sub(r'(id="valorUMA" value=")\d+(")', rf"\g<1>{data['valor']}\g<2>", nuevo)
    if nuevo != html:
        INDEX_FILE.write_text(nuevo, encoding="utf-8")
        print("✓ index.html: último valor conocido actualizado.")


def main() -> int:
    print("=== Actualizador de UMA ===")
    current = load_current()
    actual = res_de_json(current)
    print(f"Guardado: {current.get('res', '—')} — ${current.get('valor', 0):,}")

    candidatos, errores = [], []
    for nombre, fn in (("Transparencia/UMA", candidato_transparencia), ("Novedades", candidato_novedades)):
        try:
            c = fn()
            if c:
                print(f"  {nombre}: Resolución {c['res_num']}/{c['anio']} (PDF ID {c['docId']})")
                candidatos.append(c)
        except Exception as e:
            print(f"  ERROR en {nombre}: {e}", file=sys.stderr)
            errores.append(nombre)

    if not candidatos:
        print("ERROR: ninguna fuente de la CSJN respondió.", file=sys.stderr)
        return 1

    mejor = max(candidatos, key=lambda c: orden(c["res_num"], c["anio"]))
    if actual and orden(mejor["res_num"], mejor["anio"]) <= actual:
        print(f"Sin cambios: la más nueva publicada es {mejor['res_num']}/{mejor['anio']}.")
        sync_index(current)
        # Si una fuente falló no lo tapamos: puede estar ahí la resolución nueva
        return 1 if errores else 0

    print(f"Nueva resolución {mejor['res_num']}/{mejor['anio']} (vía {mejor['fuente']}).")
    try:
        mejor = completar(mejor)
    except Exception as e:
        print(f"ERROR al obtener el valor: {e}", file=sys.stderr)
        return 1

    data = {
        "valor": mejor["valor"],
        "res": f"Res. SGA N° {mejor['res_num']}/{mejor['anio']}",
        "vig": mejor["vig"],
        "docId": mejor["docId"],
        "actualizado": str(date.today()),
    }
    save(data)
    sync_index(data)
    print(f"✓ uma-data.json actualizado: {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
