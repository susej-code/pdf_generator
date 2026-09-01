# -*- coding: utf-8 -*-
"""Clasifica los valores reales de contacts.country contra el catalogo.

Necesita countries_prod.txt, que se genera con (solo lectura):

    psql -At -F'|' -c "SELECT country, COUNT(id) FROM contacts \
      WHERE country IS NOT NULL GROUP BY country ORDER BY 2 DESC;" > countries_prod.txt

Medicion del 2026-09-01 sobre prod: 1.682.730 contactos con pais, 253 valores
distintos; 99,92% mapea, 0,08% (83 valores) es basura y ninguno es un pais.
"""
from build_catalog import build_sql, norm, NOT_A_COUNTRY_PLACES

iso_set, alias_map = build_sql()
rows = []
for line in open("countries_prod.txt", encoding="utf-8"):
    line = line.rstrip("\n")
    if "|" not in line:
        continue
    val, n = line.rsplit("|", 1)
    rows.append((val, int(n)))

hit, place, junk = [], [], []
for val, n in rows:
    k = norm(val)
    if k in alias_map:
        hit.append((val, n, alias_map[k]))
    elif k in NOT_A_COUNTRY_PLACES:
        place.append((val, n))
    else:
        junk.append((val, n))

tot = sum(n for _, n in rows)
def pct(x): return f"{100*x/tot:5.2f}%"
print(f"TOTAL filas con country: {tot:,}  ({len(rows)} valores distintos)\n")
print(f"MAPEAN al catalogo : {sum(n for _,n,_ in hit):>9,} {pct(sum(n for _,n,_ in hit))}  ({len(hit)} valores)")
print(f"CIUDAD/REGION      : {sum(n for _,n in place):>9,} {pct(sum(n for _,n in place))}  ({len(place)} valores)")
print(f"BASURA / sin mapear: {sum(n for _,n in junk):>9,} {pct(sum(n for _,n in junk))}  ({len(junk)} valores)")
print("\n--- SIN MAPEAR (revisar: si alguno es pais, falta en el catalogo) ---")
for val, n in junk:
    print(f"  {n:>6}  {val!r}")
