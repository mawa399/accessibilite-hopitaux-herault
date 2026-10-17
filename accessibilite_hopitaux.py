# -*- coding: utf-8 -*-
"""
Accessibilité des communes de l'Hérault aux établissements hospitaliers
=======================================================================
Auteur : Mawa DIAW
Données : FINESS géolocalisé (DREES, data.gouv.fr, extraction du 04/05/2026),
          ADMIN EXPRESS (IGN) pour les contours communaux,
          API Découpage administratif (geo.api.gouv.fr) pour la population et la position des mairies,
          temps de trajet en voiture calculés avec OSRM (réseau OpenStreetMap).

Ce que fait le script (ArcPy, ArcGIS Pro 3.x) :
  1. crée une géodatabase de sortie et y importe les communes de l'Hérault ;
  2. crée les couches de points des hôpitaux (Lambert 93) et des mairies (WGS84 -> Lambert 93) ;
  3. lit la matrice de temps OSRM (341 communes x 50 établissements, Hérault + départements voisins)
     et retient, pour chaque commune, l'établissement le plus rapide à rejoindre pour 3 niveaux de soins :
       - PROX : tout établissement hospitalier (y compris hôpitaux de proximité) ;
       - MCO  : établissement avec plateau médecine-chirurgie-obstétrique (CH, CHU, cliniques MCO) ;
       - CHU  : centre hospitalier universitaire (recours) ;
  4. calcule aussi la distance à vol d'oiseau (GenerateNearTable) pour la comparer au temps routier ;
  5. classe les communes (< 15, 15-30, 30-45, >= 45 min) et calcule la population concernée ;
  6. exporte une table de synthèse CSV (population par classe de temps et par niveau de soins).

Utilisation :
  - dans ArcGIS Pro : via la boîte à outils Accessibilite_hopitaux.pyt (paramètres pré-remplis) ;
  - en ligne de commande : "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\Scripts\\propy.bat" accessibilite_hopitaux.py
"""

import csv
import os
import sys
from collections import defaultdict

import arcpy

# ---------------------------------------------------------------------------
# Paramètres (chemins relatifs au dossier du script)
# ---------------------------------------------------------------------------
DOSSIER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DOSSIER, "data")

def parametre(i, defaut):
    """Paramètre i : argument de ligne de commande (ou passé par la boîte à outils), sinon valeur par défaut."""
    if len(sys.argv) > i + 1 and sys.argv[i + 1].strip():
        return sys.argv[i + 1]
    return defaut


COMMUNES_SHP = parametre(0, os.path.join(DATA, "communes_herault.shp"))
HOPITAUX_CSV = parametre(1, os.path.join(DATA, "hopitaux_herault_finess.csv"))
MAIRIES_CSV = parametre(2, os.path.join(DATA, "communes_herault_mairies.csv"))
MATRICE_CSV = parametre(3, os.path.join(DATA, "matrice_temps_osrm_herault.csv"))
SORTIES = parametre(4, os.path.join(DOSSIER, "resultats"))

SR_L93 = arcpy.SpatialReference(2154)
SR_WGS84 = arcpy.SpatialReference(4326)

# Niveaux de soins : un niveau accepte les établissements de son rang ou d'un rang supérieur
NIVEAUX = {
    "PROX": ("PROX", "MCO", "CHU"),
    "MCO": ("MCO", "CHU"),
    "CHU": ("CHU",),
}
CLASSES = [(15, "1 - Moins de 15 min"), (30, "2 - 15 à 30 min"),
           (45, "3 - 30 à 45 min"), (float("inf"), "4 - 45 min et plus")]


def log(msg):
    arcpy.AddMessage(msg)
    print(msg)


def classe_temps(minutes):
    for seuil, libelle in CLASSES:
        if minutes < seuil:
            return libelle


def preparer(chemin):
    """Supprime une sortie existante : dans une boîte à outils, overwriteOutput dépend des options
    d'ArcGIS Pro, on ne s'y fie donc pas."""
    if arcpy.Exists(chemin):
        arcpy.management.Delete(chemin)
    return chemin


def classe_detour(ratio):
    """Classe de l'indice de détour (distance parcourue / distance à vol d'oiseau)."""
    if ratio is None:
        return "0 - Hôpital à moins de 2 km"
    for seuil, libelle in [(1.25, "1 - Moins de 1,25"), (1.5, "2 - 1,25 à 1,5"), (2, "3 - 1,5 à 2")]:
        if ratio < seuil:
            return libelle
    return "4 - 2 et plus"


def lire_csv(chemin):
    """Lit un CSV séparé par des ';' encodé en UTF-8 (avec ou sans BOM)."""
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ---------------------------------------------------------------------------
# 1. Espace de travail
# ---------------------------------------------------------------------------
arcpy.env.overwriteOutput = True
os.makedirs(SORTIES, exist_ok=True)
GDB = os.path.join(SORTIES, "accessibilite_hopitaux.gdb")
if not arcpy.Exists(GDB):
    arcpy.management.CreateFileGDB(SORTIES, "accessibilite_hopitaux.gdb")
arcpy.env.workspace = GDB

communes = os.path.join(GDB, "communes_herault")
hopitaux = os.path.join(GDB, "hopitaux")
mairies = os.path.join(GDB, "mairies")

log("1/6 Import des communes")
arcpy.conversion.ExportFeatures(COMMUNES_SHP, preparer(communes))
nb_communes = int(arcpy.management.GetCount(communes)[0])
log(f"    {nb_communes} communes")

# ---------------------------------------------------------------------------
# 2. Couches de points
# ---------------------------------------------------------------------------
log("2/6 Création des points hôpitaux et mairies")
# Les CSV sont séparés par des ';' (format français) : on les lit avec le module csv et on crée
# les points avec un InsertCursor, ce qui garde les codes FINESS / INSEE en texte (zéros initiaux).
champ_finess, champ_insee = "finess", "INSEE_COM"

preparer(hopitaux)
arcpy.management.CreateFeatureclass(GDB, "hopitaux", "POINT", spatial_reference=SR_L93)
champs_hop = [("finess", 9), ("nom", 80), ("raison_sociale", 120), ("libelle_categorie", 80),
              ("statut", 80), ("niveau", 4), ("dep", 2), ("commune_cp", 60)]
for nom, longueur in champs_hop:
    arcpy.management.AddField(hopitaux, nom, "TEXT", field_length=longueur)
with arcpy.da.InsertCursor(hopitaux, ["SHAPE@XY"] + [c for c, _ in champs_hop]) as cur:
    for h in lire_csv(HOPITAUX_CSV):
        cur.insertRow([(float(h["X_L93"]), float(h["Y_L93"]))] + [h[c] for c, _ in champs_hop])

preparer(mairies)
arcpy.management.CreateFeatureclass(GDB, "mairies", "POINT", spatial_reference=SR_L93)
for nom, longueur in [("INSEE_COM", 5), ("NOM", 60)]:
    arcpy.management.AddField(mairies, nom, "TEXT", field_length=longueur)
arcpy.management.AddField(mairies, "POPULATION", "LONG")
with arcpy.da.InsertCursor(mairies, ["SHAPE@", "INSEE_COM", "NOM", "POPULATION"]) as cur:
    for m in lire_csv(MAIRIES_CSV):
        pt = arcpy.PointGeometry(arcpy.Point(float(m["LON_MAIRIE"]), float(m["LAT_MAIRIE"])), SR_WGS84)
        cur.insertRow([pt.projectAs(SR_L93), m["INSEE_COM"], m["NOM"], int(m["POPULATION"])])
log(f"    {int(arcpy.management.GetCount(hopitaux)[0])} établissements, "
    f"{int(arcpy.management.GetCount(mairies)[0])} mairies")

# ---------------------------------------------------------------------------
# 3. Temps routiers OSRM : établissement le plus rapide par niveau
# ---------------------------------------------------------------------------
log("3/6 Lecture de la matrice de temps routiers (OSRM)")
infos_hop = {}
for h in lire_csv(HOPITAUX_CSV):
    infos_hop[h["finess"].zfill(9)] = h

meilleur = defaultdict(dict)  # meilleur[insee][niveau] = (minutes, km, finess)
for ligne in lire_csv(MATRICE_CSV):
    insee, fin = ligne["INSEE_COM"].zfill(5), ligne["FINESS"].zfill(9)
    duree, dist = float(ligne["DUREE_MIN"]), float(ligne["DIST_ROUTE_KM"])
    niveau_hop = infos_hop[fin]["niveau"]
    for niveau, acceptes in NIVEAUX.items():
        if niveau_hop in acceptes:
            actuel = meilleur[insee].get(niveau)
            if actuel is None or duree < actuel[0]:
                meilleur[insee][niveau] = (duree, dist, fin)

# ---------------------------------------------------------------------------
# 4. Distance à vol d'oiseau (pour comparaison avec le temps routier)
# ---------------------------------------------------------------------------
log("4/6 Distances à vol d'oiseau (GenerateNearTable)")
vol_oiseau = defaultdict(dict)  # vol_oiseau[insee][niveau] = (km, finess)
oid_insee = {r[0]: r[1] for r in arcpy.da.SearchCursor(mairies, ["OID@", champ_insee])}
for niveau, acceptes in NIVEAUX.items():
    lyr = arcpy.management.MakeFeatureLayer(
        hopitaux, preparer(f"hop_{niveau}"),
        "niveau IN ({})".format(", ".join(f"'{a}'" for a in acceptes)))[0]
    oid_finess = {r[0]: r[1] for r in arcpy.da.SearchCursor(lyr, ["OID@", champ_finess])}
    table = preparer(os.path.join("memory", f"near_{niveau}"))
    arcpy.analysis.GenerateNearTable(mairies, lyr, table, closest="CLOSEST", method="PLANAR")
    for in_fid, near_fid, near_dist in arcpy.da.SearchCursor(table, ["IN_FID", "NEAR_FID", "NEAR_DIST"]):
        vol_oiseau[oid_insee[in_fid]][niveau] = (near_dist / 1000.0, oid_finess[near_fid])

# ---------------------------------------------------------------------------
# 5. Champs d'accessibilité sur les communes
# ---------------------------------------------------------------------------
log("5/6 Calcul des indicateurs par commune")
pop_api = {m["INSEE_COM"].zfill(5): int(m["POPULATION"]) for m in lire_csv(MAIRIES_CSV)}

nouveaux_champs = [("POP", "LONG"), ("SURF_KM2", "DOUBLE"), ("DENS_POP", "DOUBLE")]
for niveau in NIVEAUX:
    nouveaux_champs += [
        (f"T_{niveau}_MIN", "DOUBLE"), (f"D_{niveau}_KM", "DOUBLE"),
        (f"VO_{niveau}_KM", "DOUBLE"), (f"HOP_{niveau}", "TEXT"), (f"CL_{niveau}", "TEXT"),
    ]
nouveaux_champs += [("DETOUR_MCO", "DOUBLE"), ("CL_DETOUR", "TEXT"), ("HOP_DIFF", "SHORT")]
for nom, type_ in nouveaux_champs:
    arcpy.management.AddField(communes, nom, type_, field_length=80 if type_ == "TEXT" else None)

arcpy.management.CalculateGeometryAttributes(communes, [["SURF_KM2", "AREA"]], area_unit="SQUARE_KILOMETERS")

champs = ["INSEE_COM", "SURF_KM2"] + [c for c, _ in nouveaux_champs]
manquantes = []
with arcpy.da.UpdateCursor(communes, champs) as cur:
    for row in cur:
        rec = dict(zip(champs, row))
        insee = rec["INSEE_COM"]
        if insee not in meilleur:
            manquantes.append(insee)
            continue
        rec["POP"] = pop_api.get(insee, 0)
        rec["DENS_POP"] = rec["POP"] / rec["SURF_KM2"] if rec["SURF_KM2"] else None
        for niveau in NIVEAUX:
            duree, dist, fin = meilleur[insee][niveau]
            vo_km, _ = vol_oiseau[insee][niveau]
            rec[f"T_{niveau}_MIN"] = round(duree, 1)
            rec[f"D_{niveau}_KM"] = round(dist, 1)
            rec[f"VO_{niveau}_KM"] = round(vo_km, 1)
            rec[f"HOP_{niveau}"] = infos_hop[fin]["nom"][:80]
            rec[f"CL_{niveau}"] = classe_temps(duree)
        # Détour : distance réellement parcourue jusqu'à l'établissement le plus rapide, rapportée à la
        # distance à vol d'oiseau de l'établissement le plus proche (ce qu'annoncerait l'ancienne méthode)
        rec["DETOUR_MCO"] = round(rec["D_MCO_KM"] / rec["VO_MCO_KM"], 2) if rec["VO_MCO_KM"] > 2 else None
        rec["CL_DETOUR"] = classe_detour(rec["DETOUR_MCO"])
        # 1 si l'établissement le plus proche à vol d'oiseau n'est pas le plus rapide par la route
        rec["HOP_DIFF"] = int(vol_oiseau[insee]["MCO"][1] != meilleur[insee]["MCO"][2])
        cur.updateRow([rec[c] for c in champs])
if manquantes:
    arcpy.AddWarning(f"Communes sans temps de trajet : {manquantes}")

# ---------------------------------------------------------------------------
# 6. Synthèse et affichage
# ---------------------------------------------------------------------------
log("6/6 Synthèse")
synthese = os.path.join(SORTIES, "synthese_population_par_classe.csv")
pop_totale = sum(r[0] for r in arcpy.da.SearchCursor(communes, ["POP"]))
with open(synthese, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["niveau", "classe", "nb_communes", "population", "part_population_pct"])
    for niveau in NIVEAUX:
        agg = defaultdict(lambda: [0, 0])
        tps_pondere = 0.0
        for cl, pop, t in arcpy.da.SearchCursor(communes, [f"CL_{niveau}", "POP", f"T_{niveau}_MIN"]):
            agg[cl][0] += 1
            agg[cl][1] += pop
            tps_pondere += pop * t
        log(f"  {niveau} : temps moyen pondéré par la population = {tps_pondere / pop_totale:.1f} min")
        for _, cl in CLASSES:
            nb, pop = agg.get(cl, [0, 0])
            w.writerow([niveau, cl, nb, pop, round(100 * pop / pop_totale, 1)])
            log(f"    {cl:<22} {nb:>4} communes  {pop:>9} hab.  {100 * pop / pop_totale:5.1f} %")
log(f"Synthèse écrite : {synthese}")

# Couches d'habillage utilisées par la mise en page (contour, départements voisins, villes repères)
arcpy.management.Dissolve(communes, preparer(os.path.join(GDB, "contour_herault")))
voisins = os.path.join(DATA, "departements_voisins.shp")
if arcpy.Exists(voisins):
    arcpy.conversion.ExportFeatures(voisins, preparer(os.path.join(GDB, "departements_contexte")))
VILLES = ("34172", "34032", "34301", "34003", "34145", "34142", "34028", "34284", "34111",
          "34079", "34199", "34129", "34293", "34064")
arcpy.conversion.ExportFeatures(mairies, preparer(os.path.join(GDB, "villes_reperes")),
                                "INSEE_COM IN ({})".format(", ".join(f"'{v}'" for v in VILLES)))

# L'affichage (couches symbolisées) est géré par la boîte à outils Accessibilite_hopitaux.pyt,
# qui renvoie les couches de sortie avec les symbologies du dossier "symbologie".
log("Terminé.")
