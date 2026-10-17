# -*- coding: utf-8 -*-
"""Boîte à outils Python (ArcGIS Pro) : accessibilité des communes aux hôpitaux.

Ajouter dans ArcGIS Pro : Catalogue > Boîtes à outils > clic droit > Ajouter une boîte à outils,
puis lancer « Temps d'accès aux hôpitaux par commune ». Les paramètres sont pré-remplis avec les
données du dossier data ; les couches résultats sont ajoutées à la carte avec leur symbologie.
"""
import os
import runpy
import sys

import arcpy

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DOSSIER, "data")
SYMBO = os.path.join(DOSSIER, "symbologie")


class Toolbox:
    def __init__(self):
        self.label = "Accessibilité hôpitaux"
        self.alias = "accessibilite_hopitaux"
        self.tools = [AccessibiliteHopitaux]


class AccessibiliteHopitaux:
    def __init__(self):
        self.label = "Temps d'accès aux hôpitaux par commune"
        self.description = ("Calcule pour chaque commune le temps de trajet en voiture vers l'établissement "
                            "hospitalier le plus rapide (proximité, MCO, CHU), le compare à la distance à vol "
                            "d'oiseau et synthétise la population concernée par classe de temps.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        def entree(nom, label, type_, defaut):
            par = arcpy.Parameter(name=nom, displayName=label, datatype=type_,
                                  parameterType="Required", direction="Input")
            par.value = defaut
            return par

        def sortie(nom, label, lyrx):
            par = arcpy.Parameter(name=nom, displayName=label, datatype="DEFeatureClass",
                                  parameterType="Derived", direction="Output")
            if os.path.exists(lyrx):
                par.symbology = lyrx
            return par

        sorties = arcpy.Parameter(name="sorties", displayName="Dossier de sortie", datatype="DEFolder",
                                  parameterType="Required", direction="Input")
        sorties.value = os.path.join(DOSSIER, "resultats")
        return [
            entree("communes", "Communes (polygones)", "DEFeatureClass", os.path.join(DATA, "communes_herault.shp")),
            entree("hopitaux", "Établissements FINESS (CSV)", "DEFile", os.path.join(DATA, "hopitaux_herault_finess.csv")),
            entree("mairies", "Communes / mairies (CSV)", "DEFile", os.path.join(DATA, "communes_herault_mairies.csv")),
            entree("matrice", "Matrice de temps OSRM (CSV)", "DEFile", os.path.join(DATA, "matrice_temps_osrm_herault.csv")),
            sorties,
            sortie("communes_sortie", "Communes avec temps d'accès", os.path.join(SYMBO, "temps_acces_mco.lyrx")),
            sortie("hopitaux_sortie", "Établissements hospitaliers", os.path.join(SYMBO, "etablissements.lyrx")),
        ]

    def execute(self, parameters, messages):
        script = os.path.join(DOSSIER, "accessibilite_hopitaux.py")
        argv_avant = sys.argv
        sys.argv = [script] + [par.valueAsText for par in parameters[:5]]
        try:
            resultat = runpy.run_path(script, run_name="__main__")
        finally:
            sys.argv = argv_avant
        parameters[5].value = resultat["communes"]
        parameters[6].value = resultat["hopitaux"]
