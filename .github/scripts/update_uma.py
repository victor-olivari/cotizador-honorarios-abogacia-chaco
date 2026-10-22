#!/usr/bin/env python3
"""
Actualiza uma-data.json con el valor más reciente de la UMA (Ley 27.423, art. 19).

La CSJN no publica la UMA en un solo lugar ni al mismo tiempo, y cada sección
escribe la resolución a su manera ("Resolución 2622", "N° 2622/2026", …). Por
eso se revisan TODAS las secciones donde puede aparecer y se toma la
resolución más nueva (por año y número):

  CSJN
    - Inicio                     https://www.csjn.gov.ar/
    - Novedades / prensa         https://www.csjn.gov.ar/novedades
    - Decisiones / Resoluciones  https://www.csjn.gov.ar/decisiones/resoluciones
    - Transparencia / UMA        https://www.csjn.gov.ar/transparencia/uma
    - PDF de la resolución       https://www.csjn.gov.ar/documentos/descargar?ID=…
  Control externo
    - CPACF, tabla "Valores UMA PJN / Ley 27.423", que además trae el
      historial completo con la fecha de vigencia de cada valor.

Basta con que UNA fuente vea la resolución nueva para tomarla. El valor sale
del PDF de la CSJN; si no se puede leer, de la noticia o de la tabla del
CPACF. Si las fuentes no coinciden en el importe, se avisa y manda el PDF.
Nunca se retrocede a una resolución anterior a la ya guardada.

Todo se descarga vía r.jina.ai, que renderiza el JavaScript de esos sitios.
Además del JSON, se actualiza el "último valor conocido" de index.html, que es
lo que se ve si la consulta en vivo falla.

Formato de uma-data.json (los tres primeros campos son los de siempre):
  valor, res, vig     importe, "Res. SGA N° 2622/2026", "primero de septiembre de 2026"
  desde               fecha desde la que se aplica, AAAA-MM-DD
  fecha               fecha de la resolución, AAAA-MM-DD
  acordada, ley       normativa que la respalda
  docId, pdf          PDF oficial de la resolución
  fuentes             secciones donde se encontró la resolución
  historial           [{res, fecha, valor, desde}], la más nueva primero
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

JINA_BASE = "https://r.jina.ai/"
CSJN = "https://www.csjn.gov.ar"
CSJN_INICIO = CSJN + "/"
CSJN_NOVEDADES = CSJN + "/novedades"
CSJN_RESOLUCIONES = CSJN + "/decisiones/resoluciones"
CSJN_UMA_PAGE = CSJN + "/transparencia/uma"
CSJN_DOC_URL = CSJN + "/documentos/descargar?ID="
CPACF_UMA = "https://www.cpacf.org.ar/noticia/5201/valores-uma-pjn-ley-27423"
LEY = "Ley 27.423, art. 19"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UMA-updater/1.0; +https://github.com/victor-olivari/cotizador-honorarios-abogacia-chaco)",
    "Accept": "text/plain, text/html, */*",
}
TIMEOUT = 40
ROOT = Path(__file__).parent.parent.parent
OUTPUT_FILE = ROOT / "uma-data.json"
INDEX_FILE = ROOT / "index.html"

VALOR_MIN, VALOR_MAX = 50_000, 5_000_000
MESES = {
    m: i + 1
    for i, m in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
    )
}
MESES["setiembre"] = 9

# "Resolución 2622", "Resolución N° 2622/2026", "Res. SGA N° 2622/26", "[N° 2622/2026]"
RE_RES = r"\bRes(?:oluci[oó]n|\.)?\s*(?:SGA\s*)?(?:N\s*(?:ro\.?|[°º.])?\s*)?\[?\s*(\d{1,5})(?:\s*/\s*(\d{2,4}))?"
RE_FECHA = r"(\d{1,2})\s*[°º]?\s+de\s+([a-záéíóú]+)\s+de\s+(20\d\d)"
RE_UMA = re.compile(r"\bUMA\b|Unidad de Medida Arancelaria", re.IGNORECASE)


def fetch_jina(url: str) -> str:
    """Descarga una URL usando r.jina.ai y devuelve el texto plano."""
    resp = requests.get(JINA_BASE + url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


# ── Utilidades ─────────────────────────────────────────────────────────────
def a_pesos(s: str) -> int:
    """'108.005', '108.005,00', '52.510.00', '23.067, 00' → importe entero."""
    s = re.sub(r"[,\.]\s*\d{2}$", "", s.strip())
    return int(re.sub(r"[^\d]", "", s))


def anio4(a: str | None, por_defecto: int) -> int:
    if not a:
        return por_defecto
    a = int(a)
    return a + 2000 if a < 100 else a


def orden(c: dict) -> tuple[int, int]:
    return (c["anio"], c["res_num"])


def res_de_json(d: dict) -> tuple[int, int] | None:
    """(año, número) de la resolución guardada: 'Res. SGA N° 1930/2026'."""
    m = re.search(r"(\d+)\s*/\s*(20\d\d)", d.get("res", ""))
    return (int(m.group(2)), int(m.group(1))) if m else None


def fecha_iso(texto: str | None) -> str | None:
    """'primero de septiembre de 2026', '1° de septiembre de 2026', '01/09/2026' → '2026-09-01'."""
    t = re.sub(r"\bprimero\b", "1", texto or "", flags=re.IGNORECASE)
    m = re.search(RE_FECHA, t, re.IGNORECASE)
    if m and m.group(2).lower() in MESES:
        return f"{m.group(3)}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/((?:20)?\d\d)\b", t)
    if m:
        return f"{anio4(m.group(3), 0)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def periodo_iso(mes: str, anio: str) -> str | None:
    """Columna "período" del CPACF: 'SEP-2026', 'AGOS-2023' → '2026-09-01'."""
    n = next((v for k, v in MESES.items() if k.startswith(mes.lower()[:3])), None)
    return f"{anio}-{n:02d}-01" if n else None


def vig_legible(vig: str) -> str:
    return re.sub(r"^primero\s+de\s+", "1° de ", vig, flags=re.IGNORECASE)


def vig_de_iso(iso: str) -> str:
    a, m, d = (int(x) for x in iso.split("-"))
    nombre = next(k for k, v in MESES.items() if v == m)
    return f"{'primero' if d == 1 else d} de {nombre} de {a}"


def valor_y_vig(texto: str) -> tuple[int | None, str | None]:
    """'… la suma de $108.005 a partir del 1° de septiembre de 2026' → (108005, '1° de septiembre de 2026').
    Una misma resolución puede fijar varios meses a la vez: manda el de vigencia más nueva."""
    t = re.sub(r"\s+", " ", texto)
    pares = [
        (a_pesos(m.group(1)), m.group(2).strip())
        for m in re.finditer(
            r"\$\s*([\d\.]+(?:,\d{1,2})?)\s*\)?\s*,?\s*(?:a partir|con vigencia|desde)\s+(?:del?\s+)?(?:d[ií]a\s+)?(.+?20\d\d)",
            t,
            re.IGNORECASE,
        )
        if fecha_iso(m.group(2))
    ]
    if pares:
        return max(pares, key=lambda p: fecha_iso(p[1]))
    m = re.search(r"suma de \$\s*([\d\.]+)", t, re.IGNORECASE)
    return (a_pesos(m.group(1)), None) if m else (None, None)


def filas(txt: str) -> list[str]:
    """Separa un listado en ítems. Cada ítem empieza con su fecha, que va al
    principio de una fila de tabla, de un enlace o tras un separador '* * *'."""
    t = re.sub(r"\s+", " ", txt)
    cortes = [m.start(1) for m in re.finditer(r"(?:^|\||\[|\*)\s*(" + RE_FECHA + ")", t, re.IGNORECASE)]
    return [t[a:b] for a, b in zip(cortes, cortes[1:] + [len(t)])]


def acordada_de(texto: str) -> str | None:
    m = re.search(r"Acordada[^\d\[]{0,30}\[?\s*(\d{1,3})\s*/\s*(\d{2,4})", texto, re.IGNORECASE)
    return f"Acordada CSJN N° {int(m.group(1))}/{anio4(m.group(2), 0)}" if m else None


def fila_resolucion(fila: str, fuente: str) -> dict | None:
    """Fila de un listado de resoluciones de la CSJN, si es de la UMA."""
    if not RE_UMA.search(fila):
        return None
    rm = re.search(RE_RES, fila, re.IGNORECASE)
    dm = re.search(r"descargar\?ID=(\d+)", fila)
    if not (rm and dm):
        return None
    f = fecha_iso(fila)
    return {
        "fuente": fuente,
        "res_num": int(rm.group(1)),
        "anio": anio4(rm.group(2), int(f[:4]) if f else date.today().year),
        "docId": int(dm.group(1)),
        "fecha": f,
        "acordada": acordada_de(fila),
    }


# ── Fuentes de la CSJN ─────────────────────────────────────────────────────
def candidato_listado(url: str, fuente: str) -> dict | None:
    """Transparencia/UMA y Decisiones/Resoluciones: la resolución de la UMA más nueva del listado."""
    txt = fetch_jina(url)
    if "descargar?ID=" not in txt:
        raise ValueError("el listado vino vacío (página sin renderizar)")
    cands = [c for c in (fila_resolucion(f, fuente) for f in filas(txt)) if c]
    if not cands:
        print(f"  {fuente}: ninguna resolución de la UMA en la primera página.")
        return None
    return max(cands, key=orden)


def candidato_transparencia() -> dict | None:
    return candidato_listado(CSJN_UMA_PAGE, "transparencia")


def candidato_resoluciones() -> dict | None:
    return candidato_listado(CSJN_RESOLUCIONES, "resoluciones")


def candidato_noticias(url: str, fuente: str) -> dict | None:
    """Inicio y Novedades: la noticia más nueva sobre la UMA. El resumen ya trae
    importe y vigencia; el número de resolución y el PDF salen del detalle."""
    txt = fetch_jina(url)
    if "/novedades/detalle/" not in txt:
        raise ValueError("el listado vino vacío (página sin renderizar)")
    for fila in filas(txt):
        lm = re.search(r"https://www\.csjn\.gov\.ar/novedades/detalle/\d+", fila)
        if not (lm and RE_UMA.search(fila)):
            continue
        cand = {"fuente": fuente, "fecha": fecha_iso(fila), "noticia": lm.group(0)}
        valor, vig = valor_y_vig(fila)
        if valor:
            cand["valor"] = valor
        if vig:
            cand["vig"] = vig
        # El detalle a veces llega sólo con el menú del sitio: se reintenta
        for intento in range(3):
            detalle = re.sub(r"\s+", " ", fetch_jina(lm.group(0)))
            if "descargar?ID=" in detalle:
                break
            time.sleep(5)
        # "resolución de la Secretaría General de Administración [N° 2622/2026](…descargar?ID=182918)"
        rm = re.search(
            r"resoluci[oó]n[^\[\d]{0,120}\[?\s*(?:N\s*[°º.]?\s*)?(\d{1,5})\s*/\s*(\d{2,4})\s*\]?\s*\(?[^)]*?descargar\?ID=(\d+)",
            detalle,
            re.IGNORECASE,
        )
        if not rm:
            raise ValueError(f"la noticia {lm.group(0)} no trae número de resolución y PDF")
        cand.update(res_num=int(rm.group(1)), anio=anio4(rm.group(2), 0), docId=int(rm.group(3)))
        if "valor" not in cand:
            v, g = valor_y_vig(detalle)
            if v:
                cand["valor"] = v
            if g:
                cand["vig"] = g
        cand["acordada"] = acordada_de(detalle)
        return cand
    print(f"  {fuente}: no hay noticias recientes sobre la UMA.")
    return None


def candidato_inicio() -> dict | None:
    return candidato_noticias(CSJN_INICIO, "inicio")


def candidato_novedades() -> dict | None:
    return candidato_noticias(CSJN_NOVEDADES, "novedades")


# ── Control externo: CPACF ─────────────────────────────────────────────────
HISTORIAL_CPACF: list[dict] = []


def candidato_cpacf() -> dict | None:
    """'[Res 2622/26](…pdf)21/10/2026$ 108.005,00 SEP-2026 01/09/2026'. Guarda el historial completo."""
    txt = re.sub(r"\s+", " ", fetch_jina(CPACF_UMA))
    hist = []
    for m in re.finditer(
        r"\bRes\.?\s*(?:N\s*[°º.]?\s*)?(\d{1,5})\s*/\s*(\d{2,4})\b(?:(?!\bRes\b).){0,200}?(\d{1,2}/\d{1,2}/(?:20)?\d\d)\s*\|?\s*\$\s*([\d\.]+(?:,\d{2})?)"
        r"\s*\|?\s*([A-Za-z]{3,5})\s*-\s*(20\d\d)\s*\|?\s*(\d{1,2}/\d{1,2}/20\d\d)?",
        txt,
        re.IGNORECASE,
    ):
        valor = a_pesos(m.group(4))
        # El piso es más bajo que VALOR_MIN para conservar los valores de 2023-2024
        if not (10_000 < valor < VALOR_MAX):
            continue
        hist.append({
            "res_num": int(m.group(1)),
            "anio": anio4(m.group(2), 0),
            "fecha": fecha_iso(m.group(3)),
            "valor": valor,
            "desde": periodo_iso(m.group(5), m.group(6)) or fecha_iso(m.group(7)),
        })
    if not hist:
        raise ValueError("no se encontró la tabla de valores")
    HISTORIAL_CPACF[:] = sorted(hist, key=lambda h: (orden(h), h["desde"] or ""), reverse=True)
    c = dict(HISTORIAL_CPACF[0], fuente="cpacf")
    if c.get("desde"):
        c["vig"] = vig_de_iso(c["desde"])
    return c


FUENTES = (
    ("Transparencia/UMA", candidato_transparencia),
    ("Resoluciones", candidato_resoluciones),
    ("Novedades", candidato_novedades),
    ("Inicio", candidato_inicio),
    ("CPACF", candidato_cpacf),
)


# ── PDF de la resolución ───────────────────────────────────────────────────
def parse_pdf(pdf_text: str) -> tuple[int, str]:
    """Valor y vigencia de la parte resolutiva: '($105.991) a partir del primero de agosto de 2026'."""
    text = re.sub(r"\s+", " ", pdf_text)
    idx = text.upper().find("SE RESUELVE")
    if idx < 0:
        idx = text.upper().find("RESUELVE")
    if idx < 0:
        raise ValueError("No se encontró la sección 'RESUELVE' en el PDF.")
    section = text[idx : idx + 2500]
    valor, vig = valor_y_vig(section)
    if valor and vig:
        return valor, vig
    section = section[:800]
    m = re.search(r"\(\s*\$\s*([\d\.\s]+?)\s*\)", section) or re.search(
        r"\$\s*([\d\.\s]+?)(?:\s*[\)\\]|\s+a partir)", section
    )
    if not m:
        raise ValueError(f"No se encontró el valor $ en la parte resolutiva:\n{section[:300]}")
    valor = a_pesos(m.group(1))
    vm = re.search(r"a partir del?\s+(.+?\d{4})", section, re.IGNORECASE)
    return valor, (vm.group(1).strip() if vm else "?")


def completar(mejor: dict, todos: list[dict]) -> dict:
    """Junta lo que cada fuente sabe de la resolución elegida y lee el valor
    del PDF; si el PDF no se puede leer, usa el de la noticia o el del CPACF."""
    misma = [c for c in todos if orden(c) == orden(mejor)]
    r = {"fuentes": sorted({c["fuente"] for c in misma})}
    for campo in ("docId", "fecha", "acordada"):
        r[campo] = next((c[campo] for c in misma if c.get(campo)), None)
    informados = {c["fuente"]: c["valor"] for c in misma if c.get("valor")}
    vig_inf = next((c["vig"] for c in misma if c.get("vig")), None)

    valor = vig = None
    if r["docId"]:
        for intento in range(3):
            try:
                valor, vig = parse_pdf(fetch_jina(CSJN_DOC_URL + str(r["docId"])))
                r["fuentes"].append("pdf")
                break
            except Exception as e:
                print(f"  PDF: intento {intento + 1} fallido ({e})")
                time.sleep(10)
    if valor is None:
        if not informados:
            raise ValueError("ninguna fuente informa el importe y el PDF no se pudo leer")
        # Se prefiere la noticia de la propia CSJN a la tabla del CPACF
        valor = informados.get("novedades") or informados.get("inicio") or next(iter(informados.values()))
        vig = vig_inf
        print(f"  ⚠ No se pudo leer el PDF; se usa el importe informado: ${valor:,}.")
    for fuente, v in informados.items():
        if v != valor:
            print(f"  ⚠ {fuente} dice ${v:,} y se toma ${valor:,} (manda el PDF de la CSJN).")
    if not (VALOR_MIN < valor < VALOR_MAX):
        raise ValueError(f"Valor fuera de rango: {valor}")
    if not vig or vig == "?":
        vig = vig_inf or "?"
    r.update(valor=valor, vig=vig, desde=fecha_iso(vig))
    return r


# ── Archivo de salida ──────────────────────────────────────────────────────
def load_current() -> dict:
    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data: dict) -> None:
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def armar_historial(data: dict, previo: list[dict]) -> list[dict]:
    """Une el historial del CPACF, el que ya estaba y la resolución vigente.
    Una entrada por mes de vigencia: una misma resolución puede fijar varios."""
    clave = lambda h: h.get("desde") or h["res"]
    por_mes = {clave(h): h for h in previo}
    for h in HISTORIAL_CPACF:
        e = {"res": f"Res. SGA N° {h['res_num']}/{h['anio']}", "fecha": h["fecha"], "valor": h["valor"], "desde": h["desde"]}
        por_mes.setdefault(clave(e), e)
    e = {k: data.get(k) for k in ("res", "fecha", "valor", "desde")}
    por_mes[clave(e)] = e
    return sorted(por_mes.values(), key=lambda h: (h.get("desde") or "", res_de_json(h) or (0, 0)), reverse=True)


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


def consultar(nombre: str, fn) -> dict | None:
    """r.jina.ai a veces devuelve la página sin renderizar: se reintenta."""
    for intento in range(3):
        try:
            return fn()
        except Exception as e:
            if intento == 2:
                raise
            print(f"  {nombre}: reintento ({e})")
            time.sleep(10)


def main() -> int:
    print("=== Actualizador de UMA ===")
    current = load_current()
    actual = res_de_json(current)
    print(f"Guardado: {current.get('res', '—')} — ${current.get('valor', 0):,}")

    candidatos, errores = [], []
    for nombre, fn in FUENTES:
        try:
            c = consultar(nombre, fn)
            if c:
                print(f"  {nombre}: Resolución {c['res_num']}/{c['anio']}"
                      + (f" (PDF ID {c['docId']})" if c.get("docId") else "")
                      + (f" — ${c['valor']:,}" if c.get("valor") else ""))
                candidatos.append(c)
        except Exception as e:
            print(f"  ERROR en {nombre}: {e}", file=sys.stderr)
            errores.append(nombre)

    if not candidatos:
        print("ERROR: ninguna fuente respondió.", file=sys.stderr)
        return 1

    mejor = max(candidatos, key=orden)
    completo = bool(current.get("desde") and current.get("historial"))
    if actual and (orden(mejor) < actual or (orden(mejor) == actual and completo)):
        print(f"Sin cambios: la más nueva publicada es {mejor['res_num']}/{mejor['anio']}.")
        sync_index(current)
        # Si una fuente falló no lo tapamos: puede estar ahí la resolución nueva
        return 1 if errores else 0

    print(f"Resolución {mejor['res_num']}/{mejor['anio']} (vía {mejor['fuente']}).")
    try:
        info = completar(mejor, candidatos)
    except Exception as e:
        print(f"ERROR al obtener el valor: {e}", file=sys.stderr)
        return 1

    data = {
        "valor": info["valor"],
        "res": f"Res. SGA N° {mejor['res_num']}/{mejor['anio']}",
        "vig": info["vig"],
        "desde": info["desde"],
        "fecha": info["fecha"],
        "acordada": info["acordada"],
        "ley": LEY,
        "docId": info["docId"],
        "pdf": CSJN_DOC_URL + str(info["docId"]) if info["docId"] else None,
        "fuentes": info["fuentes"],
        "actualizado": str(date.today()),
    }
    # Misma resolución que la ya publicada (sólo faltaban los campos nuevos):
    # el importe y la fecha de actualización no se tocan.
    if actual and orden(mejor) == actual:
        if data["valor"] != current["valor"]:
            print(f"  ⚠ El PDF dice ${data['valor']:,} y estaba publicado ${current['valor']:,}: se mantiene.")
        data["valor"] = current["valor"]
        data["actualizado"] = current.get("actualizado", data["actualizado"])
    data["historial"] = armar_historial(data, current.get("historial", []))
    save(data)
    sync_index(data)
    print("✓ uma-data.json actualizado: "
          + json.dumps({k: v for k, v in data.items() if k != "historial"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
