#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Vinted : surveille les nouvelles annonces, écarte les arnaques évidentes,
note la fiabilité des autres et envoie une notification (appli ntfy) avec le lien.
Il n'achète rien et ne se connecte à aucun compte.

  python3 vinted_bot.py                 tourne en continu
  python3 vinted_bot.py --notif-test    envoie une notification d'essai sur ton téléphone
  python3 vinted_bot.py --test          analyse quelques annonces actuelles, sans notifier
  python3 vinted_bot.py --test --notif  pareil, en envoyant les notifications
  python3 vinted_bot.py --une-fois      un seul passage puis arrêt (mode GitHub)
"""
import gzip
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DOSSIER = os.path.dirname(os.path.abspath(__file__))
F_CONFIG = os.path.join(DOSSIER, "config.json")
F_ETAT = os.path.join(DOSSIER, "etat.json")
F_LOG = os.path.join(DOSSIER, "bot.log")
F_ECARTEES = os.path.join(DOSSIER, "arnaques_ecartees.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Les identifiants des comptes Vinted sont croissants : on s'en sert pour estimer
# l'âge d'un compte. Repère mesuré le 9 novembre 2026 (~530 000 comptes par jour).
REPERE_ID = 3211761543
REPERE_TS = datetime(2026, 11, 9, 10, 22, 42, tzinfo=timezone.utc).timestamp()
IDS_PAR_JOUR = 530000

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------ utilitaires

def log(msg):
    ligne = time.strftime("%Y-%m-%d %H:%M:%S  ") + msg
    print(ligne, flush=True)
    try:
        if os.path.exists(F_LOG) and os.path.getsize(F_LOG) > 2_000_000:
            os.replace(F_LOG, F_LOG + ".1")
        with open(F_LOG, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except OSError:
        pass


def lire_json(chemin, defaut):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defaut


def ecrire_json(chemin, donnees):
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False)
    os.replace(tmp, chemin)


def norm(texte):
    """Minuscules sans accents, pour chercher des mots dans toutes les langues."""
    t = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def jours_depuis(iso):
    try:
        return (time.time() - datetime.fromisoformat(iso).timestamp()) / 86400
    except (TypeError, ValueError):
        return None


def drapeau(code):
    code = (code or "").upper()
    if len(code) == 2 and code.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code)
    return ""


# ------------------------------------------------------------------ accès Vinted

class Vinted:
    def __init__(self, domaine):
        self.base = "https://" + domaine
        self.nouvelle_session()

    def nouvelle_session(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.prete = False

    def _get(self, chemin, json_attendu):
        accept = ("application/json, text/plain, */*" if json_attendu else
                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req = urllib.request.Request(self.base + chemin, headers={
            "User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
        try:
            with self.opener.open(req, timeout=40) as r:
                brut = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    brut = gzip.decompress(brut)
        finally:
            time.sleep(random.uniform(2.0, 4.0))  # rythme d'un humain, pour rester discret
        return brut.decode("utf-8", "replace")

    def get(self, chemin, json_attendu=False):
        if not self.prete:
            self._get("/", False)  # la page d'accueil donne les cookies de session
            self.prete = True
        try:
            return self._get(chemin, json_attendu)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            self.nouvelle_session()  # session expirée : on repasse par l'accueil
            self._get("/", False)
            self.prete = True
            return self._get(chemin, json_attendu)


RE_MORCEAU = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
RE_LDJSON = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
DEC = json.JSONDecoder()


def donnees_page(html):
    """Vinted range ses données en JSON, découpées en morceaux dans la page."""
    morceaux = []
    for m in RE_MORCEAU.findall(html):
        try:
            morceaux.append(json.loads(m))
        except ValueError:
            pass
    return "".join(morceaux)


def json_a(texte, position):
    try:
        return DEC.raw_decode(texte, position)[0]
    except (ValueError, IndexError):
        return None


def lire_catalogue(html, base):
    p = donnees_page(html)
    k = p.find('"items":{"items":')
    if k < 0:
        raise ValueError("page de recherche illisible (Vinted a peut-être changé sa page)")
    annonces = []
    for a in json_a(p, k + len('"items":{"items":')) or []:
        it = a.get("productItem") or {}
        try:
            boite = it.get("itemBox") or {}
            infos = [x.strip() for x in (boite.get("secondLine") or "").replace("\xa0", " ").split("·")
                     if x.strip()]
            photos = it.get("photos") or []
            chemin = it["url"].split("?")[0]
            vendeur = it.get("user") or {}
            annonces.append({
                "id": int(it["id"]),
                "titre": it.get("title") or "",
                "chemin": chemin,
                "url": base + chemin,
                "prix": float(it["price"]["amount"]),
                "total": float((it.get("totalItemPrice") or it["price"])["amount"]),
                "marque": boite.get("firstLine") or "",
                "taille": infos[0] if len(infos) >= 2 else "",
                "etat": infos[-1] if infos else "",
                "vendeur_id": int(vendeur.get("id") or 0),
                "pro": bool(vendeur.get("isBusiness")),
                "photo": (photos[0].get("url") if photos else None) or it.get("thumbnailUrl"),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return annonces


def lire_annonce(html, item_id):
    d = {"lu": True, "description": "", "nb_photos": None, "categorie": "", "dispo": True, "attributs": {}}
    m = RE_LDJSON.search(html)
    if m:
        ld = json_a(m.group(1).strip(), 0) or {}
        d["description"] = ld.get("description") or ""
        d["categorie"] = ld.get("category") or ""
        dispo = (ld.get("offers") or {}).get("availability") or ""
        if dispo and "instock" not in dispo.lower():
            d["dispo"] = False
    p = donnees_page(html)
    k = p.find('"value":{"id":"%s"' % item_id)
    if k >= 0:
        v = json_a(p, k + len('"value":')) or {}
        if isinstance(v.get("photos"), list):
            d["nb_photos"] = len(v["photos"])
    k = p.find('"attributes":[{"code":')
    if k >= 0:
        for attr in json_a(p, k + len('"attributes":')) or []:
            try:
                d["attributs"][attr["code"]] = str(attr["data"].get("value", ""))
            except (KeyError, TypeError, AttributeError):
                pass
    for cle, mauvais in (("is_reserved", "true"), ("can_buy", "false")):
        m = re.search(r'"%s":(true|false)' % cle, p)
        if m and m.group(1) == mauvais:
            d["dispo"] = False
    return d


def lire_vendeur(v, uid):
    try:
        return (json.loads(v.get("/api/v2/users/%d" % uid, json_attendu=True)) or {}).get("user") or {}
    except (urllib.error.URLError, ValueError, OSError) as e:
        log("    profil vendeur %d illisible (%s)" % (uid, e))
        return {}


# ------------------------------------------------------------------ détection

def age_compte_jours(uid, etat):
    """Âge estimé du compte en jours, ou None s'il a plus d'un an."""
    maintenant = time.time()
    actuel = REPERE_ID + IDS_PAR_JOUR * (maintenant - REPERE_TS) / 86400
    if etat.get("max_uid"):
        actuel = max(actuel, etat["max_uid"] + IDS_PAR_JOUR * (maintenant - etat["max_uid_ts"]) / 86400)
    jours = (actuel - uid) / IDS_PAR_JOUR
    return None if jours > 365 else max(0.0, jours)


def texte_age(j):
    if j is None:
        return "de plus d'un an"
    if j < 1:
        return "de moins d'un jour"
    if j < 60:
        return "de ~%d j" % j
    return "de ~%d mois" % (j / 30)


NEGATION = re.compile(r"\b(?:pas|non|no|not|aucun|aucune|sans|jamais|nicht|kein|keine|nessun|nessuna|"
                      r"niente|nada|ningun|ninguna|nunca|never|zero|nie|geen)\b")


def nie(texte, debut):
    """Vrai si le mot est précédé d'une négation (« pas de fake », « no whatsapp »...)."""
    return bool(NEGATION.search(texte[max(0, debut - 25):debut]))


# (motif, raison, vérifier la négation) : trouvé => arnaque sûre, pas de notification
ARNAQUE_SURE = [
    (r"\brepli(?:ca|cas|que|ques|k|ken|che)\b", "se présente comme une réplique", True),
    (r"\bfakes?\b", "se dit « fake »", True),
    (r"\bdupes?\b", "se dit « dupe »", True),
    (r"\b1:1\b", "mentionne « 1:1 » (copie)", True),
    (r"\baaa\b", "mentionne « AAA » (qualité copie)", True),
    (r"\bcontrefacons?\b|\bcounterfeit\b|\bcontraffa", "parle de contrefaçon", True),
    (r"\bimitat(?:ion|ions|ione|ioni)\b|\bimitacion\b|\bnachbau\b|\bnachgemacht\b", "se dit « imitation »", True),
    (r"\b(?:pas|non|not|nicht|nao|no es|non e)\s+(?:\w+\s+)?(?:authentique|authentic|autentic[oa]|echt)\b",
     "dit lui-même ne pas être authentique", False),
    (r"whats\s?app|\bwa\.me\b|\btelegram\b", "veut discuter hors Vinted (WhatsApp/Telegram)", True),
    (r"hors (?:de )?vinted|en dehors de vinted|outside (?:of )?vinted|ausserhalb von vinted|"
     r"fuori (?:da )?vinted|fuera de vinted", "propose une vente hors Vinted", True),
    (r"paypal\s*(?:entre\s+)?(?:amis?|proches?|famille|friends?|family|freunde)|\bvirement\b|"
     r"western union|\blydia\b|\bwero\b|\bbizum\b|\bsatispay\b|\brevolut\b|\bpaysafe",
     "demande un paiement hors Vinted", True),
]
TELEPHONE = re.compile(r"(?<!\d)(?:0[67](?:[\s.\-]?\d{2}){4}|(?:\+|00)\s?\d{2,3}[\s.\-]?\d(?:[\s.\-]?\d{2,3}){3,4})(?!\d)")
EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}")

# (motif, malus, raison, vérifier la négation) : trouvé => soupçon, la note baisse
SOUPCONS = [
    (r"\binspir", 20, "« inspiré » écrit dans l'annonce (souvent une copie)", True),
    (r"\b(?:style|type|facon|look)\s+(?:moncler|stone|c\.?p\.?\s|parajumpers?|pyrenex|golden goose|ggdb|sacai)",
     25, "« style/type marque » (souvent une copie)", True),
    (r"\b(?:insta(?:gram)?|snap(?:chat)?|tiktok)\b", 15, "renvoie vers Instagram ou Snap", True),
    (r"\b(?:pas|non|not|nicht|no)\s+(?:\w+\s+)?original", 10, "« pas original » écrit dans l'annonce", False),
    (r"\b(?:sans|no|without|ohne|senza|sin)\s+(?:le\s+|il\s+|el\s+|the\s+)?(?:badge|patch|certilogo)", 12,
     "badge ou Certilogo absent", False),
]
MARQUES_CONNUES = {"stone island": "Stone Island", "cp company": "C.P. Company", "moncler": "Moncler",
                   "parajumpers": "Parajumpers", "pyrenex": "Pyrenex", "golden goose": "Golden Goose",
                   "sacai": "sacai", "canada goose": "Canada Goose"}


def marque_contradictoire(a):
    """Marque citée dans le titre alors qu'une autre est indiquée (ex. « Veste Stone Island » rangée en C.P.)."""
    titre = norm(a["titre"]).replace(".", "")
    propre = norm(a["marque"]).replace(".", "")
    if not propre or propre in titre:
        return None
    for cle, nom in MARQUES_CONNUES.items():
        if cle in titre and cle not in propre:
            return nom
    return None


RE_ENFANT_TAILLE = re.compile(r"\b(?:ans?|mois|years?|jahre|anni|anos|cm)\b")
RE_ENFANT_TITRE = re.compile(r"\b\d{1,2}\s?(?:ans|years|jahre|anni|anos)\b|\benfants?\b|\bkids?\b|\bjunior\b|"
                             r"\bkinder\b|\bbambin[oa]\b|\bnin[oa]s?\b")


def enfant(a):
    return bool(RE_ENFANT_TAILLE.search(norm(a["taille"])) or RE_ENFANT_TITRE.search(norm(a["titre"])))


PREUVES = re.compile(r"factur|fattur|ticket de caisse|preuve d.achat|receipt|rechnung|kassenbon|scontrino|"
                     r"ricevuta|\brecibo\b|certilogo|qr ?code|code qr|certificat|certificate|zertifikat")


def analyser(a, d, u, regle, etat):
    """Renvoie (raison d'arnaque sûre ou None, note sur 100, alertes, atouts)."""
    texte = norm(a["titre"] + "\n" + d.get("description", ""))
    for motif, raison, verif in ARNAQUE_SURE:
        for m in re.finditer(motif, texte):
            if not (verif and nie(texte, m.start())):
                return raison, 0, [], []
    if TELEPHONE.search(texte):
        return "donne un numéro de téléphone (arnaque classique hors Vinted)", 0, [], []
    if EMAIL.search(texte):
        return "donne une adresse e-mail (arnaque classique hors Vinted)", 0, [], []
    if u.get("is_account_banned") or u.get("is_deleted"):
        return "compte vendeur banni ou supprimé", 0, [], []

    avis = int(u.get("feedback_count") or 0)
    ratio = a["prix"] / regle["prix_marche"] if regle.get("prix_marche") else 1.0
    etat_art = norm(a["etat"])
    neuf_etiquette = "etiquette" in etat_art and "sans" not in etat_art
    if u and ratio < 0.15 and neuf_etiquette and avis == 0:
        return ("neuf avec étiquette à %d %% du prix habituel, par un compte sans avis" % (ratio * 100)), 0, [], []

    alertes, atouts = [], []
    score = 100

    def moins(points, raison):
        nonlocal score
        score -= points
        alertes.append(raison)

    if ratio < 0.12:
        moins(45, "prix irréaliste : %d %% du prix habituel" % (ratio * 100))
    elif ratio < 0.20:
        moins(30, "prix très bas : %d %% du prix habituel" % (ratio * 100))
    elif ratio < 0.35:
        moins(15, "prix bas : %d %% du prix habituel" % (ratio * 100))
    elif ratio < 0.50:
        moins(6, "prix à %d %% du prix habituel" % (ratio * 100))
    if neuf_etiquette and ratio < 0.5:
        moins(12, "neuf avec étiquette à prix cassé")
    if a.get("lot_prix_casse", 0) >= 2:
        moins(15, "le vendeur poste %d articles de cette marque à prix cassé en même temps" % a["lot_prix_casse"])
    autre = marque_contradictoire(a)
    if autre:
        moins(15, "le titre parle de %s mais la marque indiquée est %s" % (autre, a["marque"]))

    if u:
        note = float(u.get("feedback_reputation") or 0) * 5
        ventes = int(u.get("given_item_count") or 0)
        if avis == 0:
            moins(20, "aucun avis")
        elif avis < 5:
            moins(10, "seulement %d avis" % avis)
        if avis >= 3 and note < 4.0:
            moins(20, "mauvaise note ★%.1f" % note)
        elif avis >= 3 and note < 4.5:
            moins(8, "note moyenne ★%.1f" % note)
        if ventes == 0:
            moins(8, "aucune vente")
        age = age_compte_jours(a["vendeur_id"], etat)
        if age is not None:
            if age < 7:
                moins(20, "compte %s" % texte_age(age))
            elif age < 30:
                moins(10, "compte récent (%s)" % texte_age(age)[3:])
            elif age < 90:
                moins(4, "compte %s" % texte_age(age))
            nb = int(u.get("item_count") or 0)
            if age < 30 and nb >= 20:
                moins(10, "%d annonces en ligne pour un compte récent" % nb)
        elif avis == 0 and ventes == 0:
            moins(10, "compte ancien jamais utilisé (possible compte réactivé ou piraté)")
        inactif = jours_depuis(u.get("last_loged_on_ts"))
        if inactif is not None and inactif > 30:
            moins(5, "vendeur pas connecté depuis %d j" % inactif)
        if avis >= 30 and note >= 4.8:
            score += 5
            atouts.append("vendeur très bien noté")
    else:
        moins(10, "profil vendeur illisible")

    n = d.get("nb_photos")
    if n == 1:
        moins(20, "une seule photo")
    elif n == 2:
        moins(10, "seulement 2 photos")
    elif n == 3:
        moins(4, "seulement 3 photos")
    if d.get("lu") and len(d.get("description", "").strip()) < 25:
        moins(8, "description quasi vide")

    for motif, malus, raison, verif in SOUPCONS:
        m = re.search(motif, texte)
        if m and not (verif and nie(texte, m.start())):
            moins(malus, raison)
    if PREUVES.search(texte):
        score += 8
        atouts.append("preuve d'achat ou Certilogo mentionné")
    return None, max(0, min(100, score)), alertes, atouts


# ------------------------------------------------------------------ notifications

def ntfy(cfg, titre, message, clic=None, image=None, priorite=3):
    corps = {"topic": cfg["ntfy_sujet"], "title": titre, "message": message, "priority": priorite}
    if clic:
        corps["click"] = clic
        corps["actions"] = [{"action": "view", "label": "Ouvrir l'annonce", "url": clic, "clear": True}]
    if image:
        corps["attach"] = image
    req = urllib.request.Request(cfg.get("ntfy_serveur", "https://ntfy.sh").rstrip("/"),
                                 data=json.dumps(corps).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        return True
    except Exception as e:
        log("    notification non envoyée : %s" % e)
        return False


def notifier_annonce(cfg, etat, a, u, regle, score, alertes, atouts):
    pastille, niveau = (("🟢", "fiable") if score >= 75 else
                        ("🟡", "à vérifier") if score >= 50 else ("🔴", "suspect"))
    titre = "%s %d%% %s · %s · %.0f €" % (pastille, score, niveau, regle["nom"], a["prix"])
    lignes = [a["titre"]]
    details = " · ".join(x for x in (a["taille"], a["etat"]) if x)
    if details:
        lignes.append(details)
    lignes.append("💶 %.0f € (%.0f € avec protection) · prix habituel ~%d €"
                  % (a["prix"], a["total"], regle["prix_marche"]))
    if u:
        avis = int(u.get("feedback_count") or 0)
        note = " ★%.1f" % (float(u.get("feedback_reputation") or 0) * 5) if avis else ""
        lignes.append("👤 %d avis%s · %d ventes · compte %s · %s %s" % (
            avis, note, int(u.get("given_item_count") or 0),
            texte_age(age_compte_jours(a["vendeur_id"], etat)),
            drapeau(u.get("country_code")), u.get("city") or ""))
    if a.get("pro"):
        lignes.append("🏪 vendeur pro")
    lignes += ["⚠️ " + x for x in alertes[:6]]
    lignes += ["✅ " + x for x in atouts]
    priorite = 4 if score >= 75 else (3 if score >= 50 else 2)
    return ntfy(cfg, titre, "\n".join(lignes), clic=a["url"], image=a.get("photo"), priorite=priorite)


# ------------------------------------------------------------------ recherches

def url_recherche(g, page=1):
    params = [("catalog[]", c) for c in g.get("categories", [])]
    ids = []
    for art in g["articles"]:
        for i in art["marques"].values():
            if i not in ids:
                ids.append(i)
    params += [("brand_ids[]", i) for i in ids]
    params.append(("price_to", max(art["prix_max"] for art in g["articles"])))
    params += [("currency", "EUR"), ("order", "newest_first")]
    if g.get("texte"):
        params.append(("search_text", g["texte"]))
    if page > 1:
        params.append(("page", page))
    return "/catalog?" + "&".join("%s=%s" % (k, urllib.parse.quote(str(v))) for k, v in params)


def regle_pour(g, a):
    marque = norm(a["marque"])
    for art in g["articles"]:
        if marque in [norm(m) for m in art["marques"]]:
            return art
    titre = norm(a["titre"])
    for art in g["articles"]:
        if any(norm(m) in titre for m in art["marques"]):
            return art
    return None


RE_POINTURE = re.compile(r"(\d{2}(?:[.,]5)?)")


def taille_ok(art, taille):
    if "pointure_min_exclue" in art:
        m = RE_POINTURE.search(taille or "")
        return bool(m) and float(m.group(1).replace(",", ".")) > art["pointure_min_exclue"]
    voulues = [norm(t) for t in art.get("tailles") or []]
    return not voulues or norm(taille) in voulues


def stats_du_jour(etat):
    jour = time.strftime("%Y-%m-%d")
    s = etat.get("stats")
    if not s or s.get("jour") != jour:
        s = etat["stats"] = {"jour": jour, "analysees": 0, "notifs": 0, "ecartees": 0}
    return s


def traiter(cfg, etat, v, a, art, notifs, stats):
    d = {"lu": False, "description": "", "nb_photos": None, "dispo": True}
    try:
        d = lire_annonce(v.get(a["chemin"]), a["id"])
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise
        log("    annonce %d illisible (HTTP %d)" % (a["id"], e.code))
    except Exception as e:
        log("    annonce %d illisible (%s)" % (a["id"], e))
    if not d.get("dispo", True):
        return
    u = lire_vendeur(v, a["vendeur_id"]) if a["vendeur_id"] else {}
    stats["analysees"] += 1
    arnaque, score, alertes, atouts = analyser(a, d, u, art, etat)
    if arnaque:
        stats["ecartees"] += 1
        log("  ÉCARTÉE  %s · %.0f € · %s · %s" % (art["nom"], a["prix"], arnaque, a["url"]))
        try:
            with open(F_ECARTEES, "a", encoding="utf-8") as f:
                f.write("%s  %s · %.0f € · %s\n    %s\n    %s\n" % (
                    time.strftime("%Y-%m-%d %H:%M"), art["nom"], a["prix"], arnaque, a["titre"], a["url"]))
        except OSError:
            pass
        return
    log("  %3d %%  %s · %.0f € · %s · %s" % (score, art["nom"], a["prix"],
                                            " / ".join(alertes) or "rien à signaler", a["url"]))
    if score < int(cfg.get("score_minimum", 0)):
        return
    if notifs and notifier_annonce(cfg, etat, a, u, art, score, alertes, atouts):
        stats["notifs"] += 1


def cycle(cfg, etat, v, test=False, notifs=True, budget_s=None):
    """Lit toutes les recherches, puis analyse les nouvelles annonces, les plus récentes d'abord.

    Une annonce n'est marquée « vue » qu'une fois analysée (ou écartée par les filtres) : si le temps
    manque, le reste est repris au passage suivant, sans rien perdre ni rien envoyer deux fois.
    """
    debut = time.time()
    vus = etat.setdefault("vus", {})
    pretes = etat.setdefault("recherches_pretes", [])
    stats = stats_du_jour(etat)
    nb_test = int(cfg.get("annonces_par_recherche_en_test", 3))
    plafond = int(cfg.get("annonces_max_par_passage", 12))
    erreurs = 0
    a_faire, deja = [], set()
    for g in cfg["recherches"]:
        try:
            annonces = lire_catalogue(v.get(url_recherche(g)), v.base)
            nouvelles = [a for a in annonces if str(a["id"]) not in vus]
            lots = {}  # vendeurs qui postent plusieurs articles d'une marque à prix cassé
            for a in annonces:
                art = regle_pour(g, a)
                if art and art.get("prix_marche") and a["prix"] < 0.35 * art["prix_marche"]:
                    cle = (a["vendeur_id"], art["nom"])
                    lots[cle] = lots.get(cle, 0) + 1
            uids = [a["vendeur_id"] for a in annonces]
            if uids and max(uids) > etat.get("max_uid", 0):
                etat["max_uid"], etat["max_uid_ts"] = max(uids), time.time()
            if test:
                a_traiter = annonces
                log("%s : %d annonces lues, analyse des %d premières qui collent" % (g["nom"], len(annonces), nb_test))
            elif g["nom"] not in pretes:
                for a in annonces:
                    vus[str(a["id"])] = time.time()
                pretes.append(g["nom"])
                log("%s : %d annonces actuelles mémorisées, seules les nouvelles seront signalées"
                    % (g["nom"], len(annonces)))
                continue
            else:
                a_traiter = nouvelles
            retenues = 0
            for a in a_traiter:
                art = regle_pour(g, a)
                garder = bool(art) and a["prix"] <= art["prix_max"] and taille_ok(art, a["taille"])
                if garder and art.get("titre_contient") and norm(art["titre_contient"]) not in norm(a["titre"]):
                    garder = False
                if garder and cfg.get("exclure_enfants", True) and enfant(a):
                    garder = False
                if not garder:
                    if not test:
                        vus[str(a["id"])] = time.time()  # hors critères : inutile de la relire
                    continue
                if test and retenues >= nb_test:
                    break
                if a["id"] in deja:
                    continue
                deja.add(a["id"])
                retenues += 1
                a["lot_prix_casse"] = lots.get((a["vendeur_id"], art["nom"]), 0)
                a_faire.append((a, art))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise
            erreurs += 1
            log("%s : erreur HTTP %d" % (g["nom"], e.code))
        except Exception as e:
            erreurs += 1
            log("%s : erreur %r" % (g["nom"], e))
    if erreurs and erreurs == len(cfg["recherches"]):
        raise RuntimeError("aucune recherche n'a pu être lue")

    a_faire.sort(key=lambda x: x[0]["id"], reverse=True)  # les plus récentes d'abord
    if not test and len(a_faire) > plafond:
        # gros retard (bot arrêté un moment) : on garde les plus récentes, pas de rafale de vieilles annonces
        for a, _ in a_faire[plafond:]:
            vus[str(a["id"])] = time.time()
        log("%d annonces en retard ignorées, seules les %d plus récentes sont analysées"
            % (len(a_faire) - plafond, plafond))
        a_faire = a_faire[:plafond]
    if not test:
        ecrire_json(F_ETAT, etat)
    for a, art in a_faire:
        if budget_s and time.time() - debut > budget_s:
            log("Temps du passage écoulé : %d annonces reprises au prochain passage" % (len(a_faire) - a_faire.index((a, art))))
            break
        traiter(cfg, etat, v, a, art, notifs, stats)
        if not test:
            vus[str(a["id"])] = time.time()
            ecrire_json(F_ETAT, etat)  # sauvegarde au fil de l'eau : une coupure ne fait rien perdre


def resume_quotidien(cfg, etat):
    s = etat.get("stats")
    if s and s.get("jour") != time.strftime("%Y-%m-%d"):
        if cfg.get("resume_quotidien", True):
            ntfy(cfg, "📊 Bot Vinted : bilan du %s" % s["jour"],
                 "%d bonnes affaires analysées · %d notifications · %d arnaques écartées.\n"
                 "Le bot tourne normalement." % (s["analysees"], s["notifs"], s["ecartees"]), priorite=1)
        stats_du_jour(etat)


def nettoyer(etat):
    limite = time.time() - 7 * 86400
    etat["vus"] = {k: t for k, t in etat.get("vus", {}).items() if t > limite}


def charger_config(defaut=None):
    cfg = lire_json(F_CONFIG, defaut)
    if cfg is not None:
        # sur GitHub, le nom du sujet ntfy est rangé dans un secret plutôt que dans config.json
        cfg["ntfy_sujet"] = os.environ.get("NTFY_SUJET") or cfg.get("ntfy_sujet", "")
    return cfg


def passage_unique(cfg, etat, v):
    """Un seul passage puis arrêt : c'est le mode utilisé par GitHub toutes les 5 minutes."""
    try:
        resume_quotidien(cfg, etat)
        cycle(cfg, etat, v, budget_s=float(cfg.get("duree_max_passage_s", 300)))
        if etat.get("alerte_envoyee"):
            ntfy(cfg, "✅ Bot Vinted reparti", "Il lit de nouveau Vinted normalement.", priorite=2)
        etat["echecs"], etat["alerte_envoyee"] = 0, False
    except Exception as e:
        etat["echecs"] = etat.get("echecs", 0) + 1
        log("Passage raté (%d d'affilée) : %r" % (etat["echecs"], e))
        if etat["echecs"] >= 6 and not etat.get("alerte_envoyee"):
            ntfy(cfg, "⚠️ Bot Vinted bloqué",
                 "Ça fait %d passages qu'il n'arrive pas à lire Vinted. Il réessaie tout seul." % etat["echecs"],
                 priorite=3)
            etat["alerte_envoyee"] = True
    nettoyer(etat)
    ecrire_json(F_ETAT, etat)


def main():
    args = sys.argv[1:]
    cfg = charger_config()
    if not cfg:
        print("config.json introuvable ou mal écrit (une virgule en trop ou en moins ?)")
        sys.exit(1)
    if not cfg["ntfy_sujet"]:
        print("Aucun sujet ntfy : remplis ntfy_sujet dans config.json (ou le secret NTFY_SUJET sur GitHub).")
        sys.exit(1)
    if "--notif-test" in args:
        ok = ntfy(cfg, "✅ Test du bot Vinted",
                  "Si tu lis ça, les notifications arrivent bien sur ton téléphone.",
                  clic="https://www.vinted.fr", priorite=4)
        print("Notification envoyée." if ok else "Échec de l'envoi de la notification.")
        return
    etat = lire_json(F_ETAT, {})
    v = Vinted(cfg.get("domaine", "www.vinted.fr"))
    if "--test" in args:
        cycle(cfg, etat, v, test=True, notifs="--notif" in args)
        ecrire_json(F_ETAT, etat)
        return
    if "--une-fois" in args:
        passage_unique(cfg, etat, v)
        return

    minutes = cfg.get("intervalle_minutes", 5)
    log("Bot démarré : %d recherches, un passage toutes les %s min" % (len(cfg["recherches"]), minutes))
    ntfy(cfg, "✅ Bot Vinted démarré", "Je surveille tes recherches toutes les %s minutes." % minutes, priorite=2)
    echecs, alerte = 0, False
    try:
        while True:
            pause_en_plus = 0
            try:
                cfg = charger_config(cfg)  # tes modifs de config.json sont prises sans redémarrer
                resume_quotidien(cfg, etat)
                cycle(cfg, etat, v)
                if alerte:
                    ntfy(cfg, "✅ Bot Vinted reparti", "Il lit de nouveau Vinted normalement.", priorite=2)
                echecs, alerte = 0, False
            except urllib.error.HTTPError as e:
                echecs += 1
                log("Vinted répond %d, nouvelle tentative plus tard" % e.code)
                v.nouvelle_session()
                if e.code in (403, 429):
                    pause_en_plus = 10 * 60  # on se fait oublier un moment
            except Exception as e:
                echecs += 1
                log("Erreur pendant le passage : %r" % e)
                v.nouvelle_session()
            if echecs >= 6 and not alerte:
                ntfy(cfg, "⚠️ Bot Vinted bloqué",
                     "Ça fait %d passages qu'il n'arrive pas à lire Vinted. Il réessaie tout seul ; "
                     "regarde bot.log si ça dure." % echecs, priorite=3)
                alerte = True
            nettoyer(etat)
            try:
                ecrire_json(F_ETAT, etat)
            except OSError as e:
                log("État non sauvegardé : %s" % e)
            minutes = float(cfg.get("intervalle_minutes", 5))
            time.sleep(minutes * 60 * random.uniform(0.8, 1.2) + pause_en_plus)
    except KeyboardInterrupt:
        ecrire_json(F_ETAT, etat)
        log("Bot arrêté.")


if __name__ == "__main__":
    main()
