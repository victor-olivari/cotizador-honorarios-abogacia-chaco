#!/usr/bin/env python3
"""
Actualiza uma-data.json con el valor más reciente de la UMA publicado por la CSJN.

Flujo:
  1. Descarga la página https://www.csjn.gov.ar/transparencia/uma via r.jina.ai
     (jina.ai renderiza el JavaScript y devuelve el contenido completo).
  2. Extrae el ID del primer documento PDF (el más reciente).
  3. Descarga ese PDF via r.jina.ai para obtener el texto.
  4. Parsea la sección "SE RESUELVE" y extrae el valor en pesos.
  5. Actualiza uma-data.json si el valor cambió.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests

JINA_BASE = "https://r.jina.ai/"
CSJN_UMA_PAGE = "https://www.csjn.gov.ar/transparencia/uma"
CSJN_DOC_URL = "https://www.csjn.gov.ar/documentos/descargar?ID="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UMA-updater/1.0; +https://github.com/victor-olivari/cotizador-honorarios-abogacia-chaco)",
    "Accept": "text/plain, text/html, */*",
}
TIMEOUT = 40
OUTPUT_FILE = Path(__file__).parent.parent.parent / "uma-data.json"


def fetch_jina(url: str) -> str:
    """Descarga una URL usando r.jina.ai y devuelve el texto plano."""
    jina_url = JINA_BASE + url
    resp = requests.get(jina_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def get_latest_doc_id(page_text: str) -> int:
    """Extrae el ID del primer PDF de resolución de la página UMA."""
    m = re.search(r"descargar\?ID=(\d+)", page_text)
    if not m:
        raise ValueError("No se encontró ningún ID de documento en la página de la CSJN.")
    return int(m.group(1))


def get_resolution_info(page_text: str) -> tuple[str, str]:
    """Extrae número de resolución y año del primer resultado."""
    res_m = re.search(r"Resolución\s+(\d+)", page_text)
    year_m = re.search(r"\bde\s+(\d{4})\b", page_text)
    res_num = res_m.group(1) if res_m else "?"
    year = year_m.group(1) if year_m else str(date.today().year)
    return res_num, year


def parse_uma_value(pdf_text: str) -> tuple[int, str]:
    """
    Extrae el valor en pesos y la fecha de vigencia del texto del PDF.
    Busca el patrón '$ 95.626' en la sección SE RESUELVE.
    """
    # Normalizar: el PDF tiene espacios y saltos de línea variables
    text = re.sub(r"\s+", " ", pdf_text)

    # Encontrar el valor numérico después del símbolo $
    # Patrón: "$ 95.626" o "$ 95 626" o similar
    m = re.search(
        r"SE\s+RESUELVE.{0,500}?\(\s*\$\s*([\d\s\.]+?)\s*\)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        # Fallback: buscar cualquier $ seguido de número en el contexto de SE RESUELVE
        idx = text.upper().find("SE RESUELVE")
        if idx < 0:
            raise ValueError("No se encontró la sección 'SE RESUELVE' en el PDF.")
        section = text[idx : idx + 800]
        m2 = re.search(r"\$\s*([\d\.\s]+?)(?:\s*[\)\\]|\s+a partir)", section)
        if not m2:
            raise ValueError(f"No se encontró el valor $ en la sección SE RESUELVE:\n{section[:300]}")
        valor_str = m2.group(1)
    else:
        valor_str = m.group(1)

    # Limpiar: quitar puntos de miles y espacios
    valor_str = re.sub(r"[\s\.]", "", valor_str.strip())
    if not valor_str.isdigit():
        raise ValueError(f"Valor extraído no es numérico: '{valor_str}'")
    valor = int(valor_str)

    # Extraer fecha de vigencia "a partir del primero de marzo de 2026"
    vig_m = re.search(
        r"a partir de[l]?\s+(.+?)\s*(?:\(conf|\.|\n)",
        text[idx if 'idx' in dir() else text.upper().find("SE RESUELVE"):],
        re.IGNORECASE,
    )
    vig = vig_m.group(1).strip() if vig_m else "?"

    return valor, vig


def load_current() -> dict:
    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data: dict) -> None:
    OUTPUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    print("=== Actualizador de UMA ===")

    # 1. Obtener la página de la CSJN
    print(f"Descargando página UMA via jina.ai...")
    try:
        page_text = fetch_jina(CSJN_UMA_PAGE)
    except Exception as e:
        print(f"ERROR al obtener la página: {e}", file=sys.stderr)
        return 1

    # 2. Extraer el ID del documento más reciente
    try:
        doc_id = get_latest_doc_id(page_text)
        res_num, year = get_resolution_info(page_text)
        print(f"Resolución {res_num}/{year} — ID documento: {doc_id}")
    except Exception as e:
        print(f"ERROR al parsear la página: {e}", file=sys.stderr)
        return 1

    # 3. Comparar con el valor actual
    current = load_current()
    if current.get("docId") == doc_id:
        print(f"Sin cambios: el documento ID {doc_id} ya está guardado.")
        return 0

    # 4. Descargar el PDF del documento
    print(f"Descargando PDF ID={doc_id} via jina.ai...")
    try:
        pdf_text = fetch_jina(CSJN_DOC_URL + str(doc_id))
    except Exception as e:
        print(f"ERROR al obtener el PDF: {e}", file=sys.stderr)
        return 1

    # 5. Parsear el valor en pesos
    try:
        valor, vig = parse_uma_value(pdf_text)
        print(f"Valor UMA: ${valor:,} — vigente desde: {vig}")
    except Exception as e:
        print(f"ERROR al parsear el valor del PDF: {e}", file=sys.stderr)
        print("Texto PDF (primeros 1000 chars):")
        print(pdf_text[:1000])
        return 1

    # 6. Guardar
    data = {
        "valor": valor,
        "res": f"Res. SGA N° {res_num}/{year}",
        "vig": vig,
        "docId": doc_id,
        "actualizado": str(date.today()),
    }
    save(data)
    print(f"✓ uma-data.json actualizado: {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
