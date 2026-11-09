#!/usr/bin/env python3
"""Vérifie si Vinted répond depuis ce serveur : accueil, recherche, annonce, fiche vendeur."""
import gzip
import http.cookiejar
import json
import re
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
RECHERCHE = "/catalog?catalog[]=266&brand_ids[]=6539&price_to=180&currency=EUR&order=newest_first"


def get(op, chemin, json_attendu=False):
    req = urllib.request.Request("https://www.vinted.fr" + chemin, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "fr-FR,fr;q=0.9",
        "Accept": "application/json" if json_attendu else "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        with op.open(req, timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            b = gzip.decompress(b)
        except OSError:
            pass
        return e.code, b.decode("utf-8", "replace")
    except Exception as e:
        return 0, repr(e)
    finally:
        time.sleep(2.5)


def annonces(html):
    morceaux = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html)
    p = "".join(json.loads(m) for m in morceaux)
    k = p.find('"items":{"items":')
    if k < 0:
        return []
    return json.JSONDecoder().raw_decode(p, k + len('"items":{"items":'))[0]


def resume(code, texte):
    if code == 200:
        return "OK"
    indice = "captcha DataDome" if "captcha-delivery" in texte or "datadome" in texte.lower() else texte[:120].replace("\n", " ")
    return "BLOQUÉ (%s)" % indice


try:
    ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
except Exception:
    ip = "?"
print("Adresse IP de ce serveur GitHub :", ip)

bilan = []
for essai in (1, 2):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    print("\n--- Essai %d (nouvelle session) ---" % essai)
    code, t = get(op, "/")
    print("Accueil   :", code, resume(code, t))
    code, t = get(op, RECHERCHE)
    liste = annonces(t) if code == 200 else []
    print("Recherche :", code, resume(code, t), "-", len(liste), "annonces lues")
    ok = code == 200 and len(liste) > 0
    if liste:
        it = liste[0]["productItem"]
        code, t = get(op, it["url"].split("?")[0])
        print("Annonce   :", code, resume(code, t), "-", it["title"][:50])
        code, t = get(op, "/api/v2/users/%d" % it["user"]["id"], json_attendu=True)
        print("Vendeur   :", code, resume(code, t))
        ok = ok and code == 200
    bilan.append(ok)

print("\nRÉSULTAT :", "Vinted répond depuis GitHub" if all(bilan) else
      ("Vinted répond parfois" if any(bilan) else "Vinted bloque ce serveur"))
