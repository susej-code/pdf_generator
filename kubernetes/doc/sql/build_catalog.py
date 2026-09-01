# -*- coding: utf-8 -*-
"""Genera el seed de `countries` + `country_aliases` y clasifica los valores reales de prod.

Nombre canonico = endonimo en alfabeto latino cuando existe (Mexico, Brasil, Deutschland,
Haiti...); para el resto, el nombre ISO en ingles — misma regla que ya seguian
United States / United Kingdom en CALLING_CODE_TO_COUNTRY, que se respeta al pie de la letra
para no romper derive_country_from_phone ni la variable {{country}} de las plantillas.
"""
import unicodedata, re, sys

def norm(s):
    s = unicodedata.normalize("NFKD", s.strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()

# (iso2, endonimo, calling_codes, alias extra)
CATALOG = [
    # --- LatAm: los nombres vienen literales de CALLING_CODE_TO_COUNTRY ---
    ("MX", "México", ["52"], ["mexico", "mx.", "mejico", "estados unidos mexicanos"]),
    ("AR", "Argentina", ["54"], []),
    ("CO", "Colombia", ["57"], []),
    ("PE", "Perú", ["51"], ["peru"]),
    ("CL", "Chile", ["56"], []),
    ("BR", "Brasil", ["55"], ["brazil"]),
    ("VE", "Venezuela", ["58"], []),
    ("CU", "Cuba", ["53"], []),
    ("BO", "Bolivia", ["591"], []),
    ("EC", "Ecuador", ["593"], []),
    ("PY", "Paraguay", ["595"], []),
    ("UY", "Uruguay", ["598"], []),
    ("GY", "Guyana", ["592"], []),
    ("SR", "Suriname", ["597"], []),
    ("GT", "Guatemala", ["502"], []),
    ("SV", "El Salvador", ["503"], []),
    ("HN", "Honduras", ["504"], []),
    ("NI", "Nicaragua", ["505"], []),
    ("CR", "Costa Rica", ["506"], []),
    ("PA", "Panamá", ["507"], ["panama"]),
    ("HT", "Haïti", ["509"], ["haiti"]),
    ("DO", "República Dominicana", ["1809", "1829", "1849"], ["republica dominicana", "dominican republic"]),
    ("PR", "Puerto Rico", ["1787", "1939"], []),
    ("US", "United States", ["1"], ["estados unidos", "eeuu", "ee.uu.", "ee uu", "usa", "u.s.a.", "united states of america", "norteamerica"]),
    ("BZ", "Belize", ["501"], ["belice"]),
    # --- Europa (los 6 del mapa + los vistos en datos) ---
    ("ES", "España", ["34"], ["espana", "spain"]),
    ("PT", "Portugal", ["351"], []),
    ("FR", "France", ["33"], ["francia"]),
    ("IT", "Italia", ["39"], ["italy"]),
    ("DE", "Deutschland", ["49"], ["alemania", "germany"]),
    ("GB", "United Kingdom", ["44"], ["reino unido", "inglaterra", "england"]),
    ("NL", "Nederland", ["31"], ["paises bajos", "holanda", "olanda", "netherlands"]),
    ("CH", "Schweiz", ["41"], ["suiza", "switzerland"]),
    ("AT", "Österreich", ["43"], ["austria"]),
    ("BE", "Belgique", ["32"], ["belgica"]),
    ("DK", "Danmark", ["45"], ["dinamarca", "denmark"]),
    ("NO", "Norge", ["47"], ["noruega", "norway"]),
    ("SE", "Sverige", ["46"], ["suecia", "sweden"]),
    ("PL", "Polska", ["48"], ["polonia", "poland"]),
    ("HU", "Magyarország", ["36"], ["hungria", "hungary"]),
    ("GR", "Ελλάδα", ["30"], ["grecia", "greece", "ellada"]),
    ("HR", "Hrvatska", ["385"], ["croacia", "croatia"]),
    ("LU", "Luxembourg", ["352"], ["luxemburgo"]),
    ("AD", "Andorra", ["376"], []),
    ("AL", "Shqipëri", ["355"], ["albania"]),
    ("CY", "Cyprus", ["357"], ["chipre"]),
    ("MT", "Malta", ["356"], []),
    ("GI", "Gibraltar", ["350"], []),
    # --- Resto del mundo visto en los datos (nombre ISO en ingles) ---
    ("CA", "Canada", [], ["canada", "republica de canada"]),
    ("AU", "Australia", ["61"], []),
    ("NZ", "New Zealand", ["64"], ["nueva zelanda", "nueva zelandia", "new zeland"]),
    ("CN", "China", ["86"], []),
    ("JP", "日本", ["81"], ["japon", "japan", "nihon"]),
    ("KR", "대한민국", ["82"], ["corea del sur", "south korea", "korea"]),
    ("IN", "India", ["91"], []),
    ("PK", "Pakistan", ["92"], []),
    ("BD", "Bangladesh", ["880"], []),
    ("VN", "Việt Nam", ["84"], ["vietnam"]),
    ("KH", "Cambodia", ["855"], ["camboya"]),
    ("MM", "Myanmar", ["95"], []),
    ("BT", "Bhutan", ["975"], []),
    ("TL", "Timor-Leste", ["670"], ["timor leste"]),
    ("IL", "ישראל", ["972"], ["israel"]),
    ("SA", "السعودية", ["966"], ["arabia saudita", "saudi arabia", "arabia", "arabia saudi"]),
    ("AE", "الإمارات", ["971"], ["emiratos arabes unidos", "united arab emirates"]),
    ("QA", "قطر", ["974"], ["qatar"]),
    ("IQ", "العراق", ["964"], ["irak", "iraq"]),
    ("YE", "اليمن", ["967"], ["yemen"]),
    ("TR", "Türkiye", ["90"], ["turquia", "turkey"]),
    ("AM", "Հայաստան", ["374"], ["armenia"]),
    ("UZ", "Oʻzbekiston", ["998"], ["uzbekistan"]),
    ("AF", "افغانستان", ["93"], ["afghanistan", "afganistan"]),
    ("MA", "المغرب", ["212"], ["marruecos", "morocco"]),
    ("DZ", "الجزائر", ["213"], ["argelia", "algeria"]),
    ("ZA", "South Africa", ["27"], ["sudafrica"]),
    ("NG", "Nigeria", ["234"], []),
    ("AO", "Angola", ["244"], []),
    ("MZ", "Moçambique", ["258"], ["mozambique"]),
    ("ET", "Ethiopia", ["251"], ["etiopia"]),
    ("UG", "Uganda", ["256"], []),
    ("RW", "Rwanda", ["250"], ["ruanda"]),
    ("SO", "Soomaaliya", ["252"], ["somalia"]),
    ("TD", "Tchad", ["235"], ["chad"]),
    ("BF", "Burkina Faso", ["226"], []),
    ("BJ", "Bénin", ["229"], ["benin"]),
    ("MR", "موريتانيا", ["222"], ["mauritania"]),
    ("KM", "Comores", ["269"], ["comoras"]),
    ("GQ", "Guinea Ecuatorial", ["240"], ["equatorial guinea"]),
    ("GW", "Guiné-Bissau", ["245"], ["guine bissau", "guinea bissau"]),
    ("CV", "Cabo Verde", ["238"], ["cape verde"]),
    ("SL", "Sierra Leone", ["232"], ["sierra leona"]),
    # --- Caribe / territorios vistos ---
    ("AW", "Aruba", ["297"], []),
    ("CW", "Curaçao", ["599"], ["curazao", "curacao"]),
    ("BB", "Barbados", ["1246"], []),
    ("LC", "Saint Lucia", ["1758"], ["santa lucia"]),
    ("AG", "Antigua and Barbuda", ["1268"], ["antigua"]),
    ("AI", "Anguilla", ["1264"], []),
    ("KY", "Cayman Islands", ["1345"], ["islas caiman"]),
    ("VG", "British Virgin Islands", ["1284"], []),
    ("VI", "U.S. Virgin Islands", ["1340"], []),
    ("AS", "American Samoa", ["1684"], []),
    ("CX", "Christmas Island", [], []),
    ("IO", "British Indian Ocean Territory", ["246"], []),
    ("AQ", "Antarctica", [], ["antartida"]),
]

# Valores reales que NO son pais: ciudades/regiones (decision: fuera del catalogo)
NOT_A_COUNTRY_PLACES = {
    "cartagena", "veracruz", "barranquilla", "bogota", "cali", "pasto", "santander",
    "santa fe", "santafe", "santo domingo", "villavicencio", "sucre", "guajira",
    "manabi", "hidalgo", "araucania", "bosa", "ciudad bolivar", "marsella",
    "vancuver", "galapagos", "nacional", "colombiano", "alta-catamarca",
}

def build_sql():
    seen_iso, seen_alias, out = set(), {}, []
    for iso, name, codes, extra in CATALOG:
        assert iso not in seen_iso, f"iso duplicado: {iso}"
        seen_iso.add(iso)
        for a in [iso.lower(), norm(name)] + [norm(x) for x in extra]:
            if a in seen_alias and seen_alias[a] != iso:
                raise AssertionError(f"alias '{a}' choca: {seen_alias[a]} vs {iso}")
            seen_alias[a] = iso
    return seen_iso, seen_alias

if __name__ == "__main__":
    iso_set, alias_map = build_sql()
    print(f"paises: {len(iso_set)} | alias: {len(alias_map)}", file=sys.stderr)
