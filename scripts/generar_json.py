"""
Genera docs/datos_mapa.json amb les ubicacions dels datasets publicats
d'una col·lecció de CORA.RDR (Dataverse, CSUC).

- Només fa peticions de lectura (GET) i NO necessita token: només veu els
  datasets publicats, que és el que ha d'aparèixer en un mapa públic.
- Ubicacions: coordenades (bounding box), coordenades escrites en text lliure
  i ciutats (taula pròpia + geocodificació de reserva amb Nominatim).
- El que no es pot situar queda a docs/pendientes.json per revisar-ho.

També genera docs/colaboraciones.json amb les institucions de les afiliacions
dels autors (excloent la UPC), situades amb les coordenades del registre ROR
(https://ror.org). Si una afiliació no porta ROR, es prova de trobar-lo amb la
cerca per afiliació de l'API de ROR. Les afiliacions no resoltes queden a
docs/pendientes_colaboraciones.json.

Ús:  python scripts/generar_json.py
"""
import datetime
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

# ---------- CONFIGURACIÓ ----------
BASE_URL = "https://dataverse.csuc.cat"
COLECCION = "upc"
PAUSA = 0.2                      # segons entre peticions a CORA
USAR_NOMINATIM = True            # geocodificar ciutats que no siguin a la taula
ROR_API = "https://api.ror.org/v2/organizations"
UPC_ROR = "03mb6wj31"            # ROR de la UPC (s'exclou de les col·laboracions)
ORIGEN_RESERVA = (41.3884, 2.1129, "Universitat Politècnica de Catalunya")  # si ROR falla
USAR_ROR_POR_TEXTO = True        # buscar el ROR d'afiliacions escrites només com a text
PAUSA_ROR = 0.15                 # segons entre peticions a ROR
MAX_ERRORES_ROR = 0.2            # si falla més d'aquesta fracció, no se sobreescriu el JSON
# ------------------------------------

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "datos_mapa.json"
PENDIENTES = RAIZ / "docs" / "pendientes.json"
CACHE = RAIZ / "scripts" / "geocache.json"
SALIDA_COLAB = RAIZ / "docs" / "colaboraciones.json"
PENDIENTES_COLAB = RAIZ / "docs" / "pendientes_colaboraciones.json"
CACHE_ROR = RAIZ / "scripts" / "rorcache.json"
UA = "cora-upc-mapa/1.0 (biblioteca UPC; datos abiertos)"

RE_ROR = re.compile(r"ror\.org/(0[0-9a-z]{8})", re.I)
RE_UPC = re.compile(r"politecnica de catalunya|politecnica de cataluna|"
                    r"polytechnic university of catalonia|\bupc\b")


def norm(s):
    """Minúscules, sense accents i amb espais normalitzats."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# Taula de ciutats: nom normalitzat -> (latitud, longitud, país)
CIUDADES = {
    "barcelona": (41.3874, 2.1686, "Spain"),
    "barcelonaa": (41.3874, 2.1686, "Spain"),          # errada freqüent
    "terrassa": (41.5633, 2.0083, "Spain"),
    "castelldefels": (41.2800, 1.9753, "Spain"),
    "lleida": (41.6176, 0.6200, "Spain"),
    "manresa": (41.7289, 1.8260, "Spain"),
    "sant cugat": (41.4722, 2.0864, "Spain"),
    "sant cugat del valles": (41.4722, 2.0864, "Spain"),
    "cerdanyola del valles": (41.4914, 2.1405, "Spain"),
    "sant vicenc dels horts": (41.3925, 2.0083, "Spain"),
    "sant joan despi": (41.3683, 2.0603, "Spain"),
    "madrid": (40.4168, -3.7038, "Spain"),
    "malaga": (36.7213, -4.4214, "Spain"),
    "a coruna": (43.3623, -8.4115, "Spain"),
    "paterna": (39.5033, -0.4400, "Spain"),
    "belgrade": (44.7866, 20.4489, "Serbia"),
    "belgrado": (44.7866, 20.4489, "Serbia"),
    "beograd": (44.7866, 20.4489, "Serbia"),
    "berlin": (52.5200, 13.4050, "Germany"),
    "boras": (57.7210, 12.9401, "Sweden"),
    "sortland": (68.6933, 15.4167, "Norway"),
    "narvik": (68.4385, 17.4272, "Norway"),
    "panama": (8.9824, -79.5199, "Panama"),
    "san juan del rio": (20.3883, -99.9958, "Mexico"),
}

# Memòria de ciutats ja geocodificades per Nominatim en execucions anteriors
def cargar_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def guardar_cache(cache):
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True),
                     encoding="utf-8")


def es_espana(pais):
    return norm(pais) in {"spain", "espana", "es"}


def valor(d, clave):
    v = d.get(clave, {})
    return (v.get("value", "") if isinstance(v, dict) else "").strip()


def geocodificar(ciudad, pais, cache):
    """Retorna (lat, lon, país) o None si no es pot situar."""
    k = norm(ciudad)
    if k in CIUDADES:
        return CIUDADES[k]
    ck = f"{k}|{norm(pais)}"
    if ck in cache:
        return tuple(cache[ck])
    if not USAR_NOMINATIM:
        return None
    try:
        q = f"{ciudad}, {pais}" if pais else ciudad
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": q, "format": "json", "limit": 1,
                                 "addressdetails": 1},
                         headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        res = r.json()
        time.sleep(1.1)                       # política d'ús: màx. 1 petició/s
        if res:
            pais_res = res[0].get("address", {}).get("country", pais or "")
            out = (float(res[0]["lat"]), float(res[0]["lon"]), pais_res)
            cache[ck] = list(out)
            return out
    except Exception as e:
        print(f"  Nominatim ha fallat per a '{ciudad}': {e}")
    return None


def a_float(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def extraer_ubicaciones(version, cache, pendientes):
    """Retorna la llista d'ubicacions d'un dataset (versió més recent)."""
    campos = version.get("metadataBlocks", {}).get("geospatial", {}).get("fields", [])
    cajas, puntos_texto, ciudades = [], [], []

    for c in campos:
        if c["typeName"] == "geographicBoundingBox":
            for b in c["value"]:
                w, e = a_float(valor(b, "westLongitude")), a_float(valor(b, "eastLongitude"))
                n, s = a_float(valor(b, "northLatitude")), a_float(valor(b, "southLatitude"))
                if None in (w, e, n, s):
                    continue
                if not (-90 <= n <= 90 and -90 <= s <= 90 and -180 <= w <= 180 and -180 <= e <= 180):
                    continue
                cajas.append((w, e, n, s))
        elif c["typeName"] == "geographicCoverage":
            for g in c["value"]:
                pais, ciudad = valor(g, "country"), valor(g, "city")
                otra = valor(g, "otherGeographicCoverage")
                if ciudad:
                    ciudades.append((ciudad, pais))
                m = re.search(r"(-?\d{1,2}\.\d+)[\s,;]+(-?\d{1,3}\.\d+)", otra)
                if m:
                    lat, lon = float(m.group(1)), float(m.group(2))
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        puntos_texto.append((lat, lon, pais))

    ubic = []

    def cerca(lat, lon):
        return any(abs(u["lat"] - lat) < 0.15 and abs(u["lon"] - lon) < 0.15 for u in ubic)

    # 1) Coordenades (el més precís)
    for w, e, n, s in cajas:
        lat, lon = (n + s) / 2, (w + e) / 2
        es_punto = abs(w - e) < 1e-5 and abs(n - s) < 1e-5
        if cerca(lat, lon) and any(u["lat"] == lat and u["lon"] == lon for u in ubic):
            continue
        ubic.append({"lat": round(lat, 6), "lon": round(lon, 6),
                     "tipo": "punto" if es_punto else "area",
                     "bbox": None if es_punto else [round(s, 6), round(w, 6), round(n, 6), round(e, 6)],
                     "lugar": "", "pais": ""})
    # 2) Coordenades escrites com a text
    for lat, lon, pais in puntos_texto:
        if not cerca(lat, lon):
            ubic.append({"lat": lat, "lon": lon, "tipo": "punto", "bbox": None,
                         "lugar": "", "pais": pais})
    # 3) Ciutats (només si no hi ha ja res a menys de ~15 km)
    for ciudad, pais in ciudades:
        res = geocodificar(ciudad, pais, cache)
        if not res:
            pendientes.add((ciudad, pais))
            continue
        lat, lon, pais_res = res
        if cerca(lat, lon):
            continue
        ubic.append({"lat": lat, "lon": lon, "tipo": "ciudad", "bbox": None,
                     "lugar": ciudad, "pais": pais or pais_res})
    return ubic


def clasificar_internacional(u):
    """True si la ubicació és fora d'Espanya."""
    if u["pais"]:
        return not es_espana(u["pais"])
    lat, lon = u["lat"], u["lon"]
    peninsula = 35.8 <= lat <= 43.9 and -9.6 <= lon <= 4.5
    canarias = 27.5 <= lat <= 29.5 and -18.5 <= lon <= -13.0
    return not (peninsula or canarias)


# ---------- COL·LABORACIONS (afiliacions + ROR) ----------

class EstatROR:
    """Memòria de ROR (fitxes i cerques per text) i comptadors d'errors."""

    def __init__(self):
        self.cache = {"org": {}, "texto": {}}
        if CACHE_ROR.exists():
            try:
                self.cache.update(json.loads(CACHE_ROR.read_text(encoding="utf-8")))
            except Exception:
                pass
        self.consultas = 0
        self.errores = 0

    def guardar(self):
        CACHE_ROR.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1,
                                        sort_keys=True), encoding="utf-8")


def _get_ror(url, params=None):
    """GET a l'API de ROR amb un reintent si ens frenen (429)."""
    for intent in range(2):
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 429 and intent == 0:
            time.sleep(float(r.headers.get("Retry-After", 5)))
            continue
        return r
    return r


def _ficha_desde_registro(d):
    """Converteix un registre ROR v2 en la fitxa que desem."""
    nombre = ""
    for n in d.get("names", []):
        if "ror_display" in n.get("types", []):
            nombre = n.get("value", "")
            break
    if not nombre and d.get("names"):
        nombre = d["names"][0].get("value", "")
    det = ((d.get("locations") or [{}])[0]).get("geonames_details", {}) or {}
    return {"nombre": nombre, "lat": det.get("lat"), "lon": det.get("lng"),
            "ciudad": det.get("name", ""), "pais_codigo": det.get("country_code", ""),
            "pais": det.get("country_name", "")}


def ficha_ror(ror_id, est):
    """Fitxa d'una institució a partir del seu ROR (None si no existeix o falla)."""
    ror_id = ror_id.lower()
    if ror_id in est.cache["org"]:
        return est.cache["org"][ror_id]
    est.consultas += 1
    try:
        r = _get_ror(f"{ROR_API}/{ror_id}")
        time.sleep(PAUSA_ROR)
        if r.status_code == 404:
            est.cache["org"][ror_id] = None          # ROR inexistent: no cal insistir
            return None
        r.raise_for_status()
        ficha = _ficha_desde_registro(r.json())
    except Exception as e:
        est.errores += 1
        print(f"  ROR ha fallat per a {ror_id}: {e}")
        return None                                   # error temporal: no es desa
    est.cache["org"][ror_id] = ficha
    return ficha


def ror_por_texto(texto, est):
    """Busca el ROR d'una afiliació escrita com a text (només si ROR la 'tria')."""
    k = norm(texto)
    if k in est.cache["texto"]:
        return est.cache["texto"][k] or None
    est.consultas += 1
    try:
        r = _get_ror(ROR_API, params={"affiliation": texto})
        time.sleep(PAUSA_ROR)
        r.raise_for_status()
        elegido = next((it for it in r.json().get("items", []) if it.get("chosen")), None)
    except Exception as e:
        est.errores += 1
        print(f"  Cerca ROR ha fallat per a '{texto}': {e}")
        return None
    if not elegido:
        est.cache["texto"][k] = ""
        return None
    org = elegido["organization"]
    ror_id = org["id"].rsplit("/", 1)[-1].lower()
    est.cache["org"].setdefault(ror_id, _ficha_desde_registro(org))
    est.cache["texto"][k] = ror_id
    return ror_id


def extraer_afiliaciones(version):
    """Llista d'afiliacions dels autors: ('ror', id) o ('texto', afiliació).

    Mira tots els subcamps de cada autor, de manera que funciona tant si el ROR
    és el valor de l'afiliació com si és un subcamp d'identificador propi.
    """
    campos = version.get("metadataBlocks", {}).get("citation", {}).get("fields", [])
    out = []
    for c in campos:
        if c.get("typeName") != "author":
            continue
        for autor in c.get("value", []):
            rors, texto = set(), ""
            for sub in autor.values():
                v = sub.get("value", "") if isinstance(sub, dict) else ""
                if not isinstance(v, str):
                    continue
                rors.update(m.lower() for m in RE_ROR.findall(v))
                if sub.get("typeName") == "authorAffiliation":
                    texto = v.strip()
            if rors:
                out += [("ror", x) for x in sorted(rors)]
            elif texto:
                out.append(("texto", texto))
    return out


def es_upc_texto(texto):
    return bool(RE_UPC.search(norm(texto)))


def resolver_afiliacion(tipo, valor, est, pendientes):
    """Retorna (ror, fitxa) d'una institució externa, o None."""
    if tipo == "ror":
        ror_id = valor
    else:
        if es_upc_texto(valor):
            return None
        ror_id = ror_por_texto(valor, est) if USAR_ROR_POR_TEXTO else None
        if not ror_id:
            pendientes.add(valor)
            return None
    if ror_id == UPC_ROR:
        return None
    ficha = ficha_ror(ror_id, est)
    if not ficha:
        pendientes.add(f"ROR {ror_id}")
        return None
    if ficha["lat"] is None or ficha["lon"] is None:
        pendientes.add(f"{ficha['nombre']} (ROR {ror_id}, sense coordenades)")
        return None
    return ror_id, ficha


def listar_datasets():
    resultados, inicio, por_pagina = [], 0, 100
    while True:
        r = requests.get(f"{BASE_URL}/api/search",
                         params={"q": "*", "type": "dataset", "subtree": COLECCION,
                                 "start": inicio, "per_page": por_pagina},
                         timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        resultados += data["items"]
        inicio += por_pagina
        if inicio >= data["total_count"]:
            break
        time.sleep(PAUSA)
    return resultados


def escribir_colaboraciones(colab, est, pendientes):
    """Desa docs/colaboraciones.json (si ROR ha respost prou bé) i els pendents."""
    est.guardar()
    if est.consultas and est.errores / est.consultas > MAX_ERRORES_ROR:
        print(f"AVÍS: ROR ha fallat en {est.errores} de {est.consultas} consultes; "
              f"no se sobreescriu {SALIDA_COLAB.name} (es conserva la versió anterior).")
        return
    ficha_upc = ficha_ror(UPC_ROR, est)
    if ficha_upc and ficha_upc["lat"] is not None:
        origen = {"nombre": ficha_upc["nombre"], "lat": ficha_upc["lat"],
                  "lon": ficha_upc["lon"]}
    else:
        origen = {"nombre": ORIGEN_RESERVA[2], "lat": ORIGEN_RESERVA[0],
                  "lon": ORIGEN_RESERVA[1]}
    est.guardar()

    instituciones, datasets = [], set()
    for ror_id, reg in colab.items():
        f = reg["ficha"]
        datasets.update(reg["datasets"])
        instituciones.append({
            "ror": f"https://ror.org/{ror_id}", "nombre": f["nombre"],
            "ciudad": f["ciudad"], "pais": f["pais"], "pais_codigo": f["pais_codigo"],
            "lat": round(f["lat"], 5), "lon": round(f["lon"], 5),
            "internacional": f["pais_codigo"] != "ES",
            "n_datasets": len(reg["datasets"]),
            "datasets": sorted(reg["datasets"].values(), key=lambda d: d["doi"]),
        })
    instituciones.sort(key=lambda x: (-x["n_datasets"], x["nombre"]))

    SALIDA_COLAB.write_text(json.dumps({
        "generado": datetime.date.today().isoformat(),
        "coleccion": COLECCION,
        "origen": origen,
        "datasets_con_colaboracion": len(datasets),
        "instituciones": instituciones,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    PENDIENTES_COLAB.write_text(json.dumps(sorted(pendientes), ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(f"Col·laboracions: {len(instituciones)} institucions externes, "
          f"{len(datasets)} datasets, {len(pendientes)} afiliacions pendents, "
          f"{est.errores} errors de ROR.")


def main():
    items = listar_datasets()
    print(f"{len(items)} datasets publicats a '{COLECCION}'")
    cache, pendientes = cargar_cache(), set()
    ubicaciones, con_ubic, no_leidos = [], set(), 0
    est_ror, pend_colab = EstatROR(), set()
    colab = {}                  # ror -> {"ficha": {...}, "datasets": {doi: {...}}}

    for i, item in enumerate(items, 1):
        pid = item["global_id"]
        try:
            r = requests.get(f"{BASE_URL}/api/datasets/:persistentId/",
                             params={"persistentId": pid}, timeout=60)
            r.raise_for_status()
            version = r.json()["data"]["latestVersion"]
        except Exception:
            no_leidos += 1          # p. ex. registres recol·lectats d'un altre repositori
            continue
        if version.get("versionState") != "RELEASED":
            continue
        ubic = extraer_ubicaciones(version, cache, pendientes)
        anio = str(item.get("published_at", ""))[:4]
        for u in ubic:
            u.update({"doi": pid, "titulo": item.get("name", ""),
                      "url": item.get("url", ""), "anio": anio,
                      "internacional": clasificar_internacional(u)})
            ubicaciones.append(u)
        if ubic:
            con_ubic.add(pid)
        try:                        # les col·laboracions no han de trencar el mapa principal
            for tipo, valor in extraer_afiliaciones(version):
                res = resolver_afiliacion(tipo, valor, est_ror, pend_colab)
                if res:
                    ror_id, ficha = res
                    reg = colab.setdefault(ror_id, {"ficha": ficha, "datasets": {}})
                    reg["datasets"][pid] = {"doi": pid, "titulo": item.get("name", ""),
                                            "url": item.get("url", ""), "anio": anio}
        except Exception as e:
            print(f"  Afiliacions de {pid}: error inesperat ({e})")
        if i % 25 == 0:
            print(f"  {i}/{len(items)}")
        time.sleep(PAUSA)

    guardar_cache(cache)
    salida = {
        "generado": datetime.date.today().isoformat(),
        "coleccion": COLECCION,
        "total_publicados": len(items),
        "no_leidos": no_leidos,
        "con_ubicacion": len(con_ubic),
        "ubicaciones": ubicaciones,
    }
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    PENDIENTES.write_text(json.dumps(sorted(f"{c} ({p})" if p else c for c, p in pendientes),
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Fet: {len(con_ubic)} datasets amb ubicació, {len(ubicaciones)} punts, "
          f"{len(pendientes)} ciutats pendents, {no_leidos} no llegits.")
    try:
        escribir_colaboraciones(colab, est_ror, pend_colab)
    except Exception as e:
        print(f"AVÍS: no s'ha pogut generar {SALIDA_COLAB.name}: {e}")


if __name__ == "__main__":
    main()
