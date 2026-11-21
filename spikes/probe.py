# ============================================================================
# DA pipeline: stage 0 source probe + layer discovery.  Paste into one cell.
# Self-contained, stdlib only, Python 3.9+.  Nothing to install or save.
#
# Produces:
#   REPORT              dict, in the notebook, for poking at
#   probe.json          full report written to the working directory
#   a PASTE BLOCK       printed at the end: copy that into chat if finding
#                       the json file is annoying
# ============================================================================

import json, math, ssl, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- CONFIG ---
SITE = {"opportunity_id": "TI-OPP-35820", "lat": 41.27277, "lon": -95.50833,
        "radius_ft": 3281, "rad_ft": 450, "state": "IA"}
OUT_FILE   = "probe.json"
INSECURE   = False   # True if a county server throws a TLS/certificate error
SKIP_BULK  = False   # True to skip the FCC ASR HEAD request
MAX_PROBE  = 3       # how many matching layers per need to deep-probe
TIMEOUT    = 30
FOLDERS = ["Assessor", "Planning", "PublicDataEnterprise", "CouncilBluffs",
           "Engineering", "Auditor", "OpenData", "Tyler", "SnapShots",
           "Ag_Catalog", "WestNish", "InternalDataOnly"]
# ---------------------------------------------------------------------------

UA = "da-pipeline-probe/1.1"
FT_PER_DEG_LAT = 364_000.0
COUNTY_ROOT = "https://gis.pottcounty-ia.gov/arcgis/rest/services"
CB_ROOT = "https://gispublic.councilbluffs-ia.gov/publicserver/rest/services"


# ------------------------------------------------------------------ HTTP ---
def _opener():
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener()


def http_get(url, params=None, method="GET"):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    t0 = time.perf_counter()
    try:
        with _opener().open(req, timeout=TIMEOUT) as r:
            return {"ok": True, "status": r.status, "ms": round((time.perf_counter()-t0)*1000),
                    "url": url, "headers": {k.lower(): v for k, v in r.headers.items()},
                    "body": b"" if method == "HEAD" else r.read()}
    except urllib.error.HTTPError as e:
        try:
            head = e.read()[:300].decode("utf-8", "replace")
        except Exception:
            head = ""
        return {"ok": False, "status": e.code, "ms": round((time.perf_counter()-t0)*1000),
                "url": url, "error": f"HTTP {e.code} {e.reason}", "body_head": head}
    except Exception as e:
        return {"ok": False, "status": None, "ms": round((time.perf_counter()-t0)*1000),
                "url": url, "error": f"{type(e).__name__}: {e}"}


def get_json(url, params=None):
    params = dict(params or {})
    params.setdefault("f", "json")
    r = http_get(url, params)
    if not r["ok"]:
        return r
    try:
        r["json"] = json.loads(r.pop("body").decode("utf-8", "replace"))
    except Exception as e:
        r["ok"] = False
        r["error"] = f"non-JSON response: {e}"
        return r
    if isinstance(r["json"], dict) and "error" in r["json"]:   # ArcGIS: 200 + error body
        err = r["json"]["error"]
        r["ok"] = False
        r["error"] = f"ArcGIS error {err.get('code')}: {err.get('message')}"
    return r


# -------------------------------------------------------------- GEOMETRY ---
def ring_envelope(lat, lon, radius_ft, pad=1.05):
    d_lat = (radius_ft * pad) / FT_PER_DEG_LAT
    d_lon = (radius_ft * pad) / (FT_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    return {"xmin": round(lon-d_lon, 7), "ymin": round(lat-d_lat, 7),
            "xmax": round(lon+d_lon, 7), "ymax": round(lat+d_lat, 7),
            "spatialReference": {"wkid": 4326}}


ENV = ring_envelope(SITE["lat"], SITE["lon"], SITE["radius_ft"])


# --------------------------------------------------------- ARCGIS PROBES ---
def summarize_layers(j):
    out = []
    for key in ("layers", "tables"):
        for l in j.get(key) or []:
            out.append({"id": l.get("id"), "name": l.get("name"),
                        "type": l.get("type") or key[:-1],
                        "geometryType": l.get("geometryType")})
    return out


def probe_layer(service_url, layer):
    lid = layer.get("id")
    lurl = f"{service_url.rstrip('/')}/{lid}"
    res = {"layer_id": lid, "layer_name": layer.get("name"),
           "geometry_type": layer.get("geometryType"), "url": lurl}

    meta = get_json(lurl)
    if not meta["ok"]:
        res["metadata"] = {"ok": False, "error": meta.get("error")}
        return res
    mj = meta["json"]
    res["metadata"] = {
        "ok": True,
        "fields": [{"name": f.get("name"), "type": f.get("type"), "alias": f.get("alias")}
                   for f in (mj.get("fields") or [])],
        "extent_sr": (mj.get("extent") or {}).get("spatialReference") or {},
        "max_record_count": mj.get("maxRecordCount"),
        "supports_pagination": bool((mj.get("advancedQueryCapabilities") or {})
                                    .get("supportsPagination")),
        "capabilities": mj.get("capabilities"),
    }

    gp = {"geometry": json.dumps(ENV), "geometryType": "esriGeometryEnvelope",
          "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "where": "1=1"}

    c = get_json(f"{lurl}/query", {**gp, "returnCountOnly": "true"})
    res["count_in_ring_envelope"] = ({"ok": True, "count": c["json"].get("count")}
                                     if c["ok"] else {"ok": False, "error": c.get("error")})

    s = get_json(f"{lurl}/query", {**gp, "outFields": "*", "returnGeometry": "false",
                                   "resultRecordCount": 1})
    if s["ok"]:
        feats = s["json"].get("features") or []
        res["sample_attributes"] = feats[0].get("attributes") if feats else None
    else:
        res["sample_attributes"] = None
        res["sample_error"] = s.get("error")

    g = get_json(f"{lurl}/query", {**gp, "outFields": "*", "returnGeometry": "true",
                                   "outSR": 4326, "resultRecordCount": 1, "f": "geojson"})
    res["geojson_supported"] = bool(g["ok"] and isinstance(g.get("json"), dict)
                                    and g["json"].get("type"))
    return res


def probe_service(url, keywords, limit=4):
    out = {"service_url": url}
    svc = get_json(url)
    if not svc["ok"]:
        return {**out, "reachable": False, "error": svc.get("error"), "status": svc.get("status")}
    j = svc["json"]
    layers = summarize_layers(j)
    out.update({"reachable": True, "ms": svc["ms"],
                "spatial_reference": (j.get("spatialReference")
                                      or (j.get("fullExtent") or {}).get("spatialReference") or {}),
                "max_record_count": j.get("maxRecordCount"), "all_layers": layers})
    picked = [l for l in layers
              if any(k.lower() in (l.get("name") or "").lower() for k in keywords)][:limit]
    out["matched_layers"] = [probe_layer(url, l) for l in picked]
    if not picked:
        out["note"] = f"no layer name matched {keywords}; see all_layers"
    return out


def probe_folder(url):
    r = get_json(url)
    if not r["ok"]:
        return {"url": url, "reachable": False, "error": r.get("error")}
    j = r["json"]
    return {"url": url, "reachable": True, "folders": j.get("folders") or [],
            "services": [{"name": s.get("name"), "type": s.get("type")}
                         for s in (j.get("services") or [])]}


# ------------------------------------------------------- PHASE 1 TARGETS ---
TARGETS = [
    {"name": "zoning_pottawattamie", "kind": "svc", "critical": True,
     "why": "Unincorporated county zoning districts; joins each parcel to a district.",
     "url": f"{COUNTY_ROOT}/Hosted/Zoning_Pottawattamie/FeatureServer",
     "kw": ["zoning", "district", "zone"]},
    {"name": "zoning_council_bluffs", "kind": "svc", "critical": True,
     "why": "City zoning districts; the demo sites include Council Bluffs parcels.",
     "url": f"{CB_ROOT}/CommDev_Public/cbZoningDistricts_Public/FeatureServer",
     "kw": ["zoning", "district", "zone"]},
    {"name": "flood_fema_nfhl", "kind": "svc", "critical": True,
     "why": "FEMA flood zones; reported as a risk flag, not an automatic disqualifier.",
     "url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer",
     "kw": ["flood hazard zone", "flood hazard", "firm panel"]},
    {"name": "wetlands_nwi", "kind": "svc_candidates", "critical": True,
     "why": "National Wetlands Inventory; subtracts from buildable area, constrains guy anchors.",
     "urls": ["https://www.fws.gov/wetlandsmapservice/rest/services/Wetlands/MapServer",
              "https://fwsprimary.wim.usgs.gov/server/rest/services/Wetlands/MapServer",
              "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer"],
     "kw": ["wetland"]},
    {"name": "elevation_usgs_epqs", "kind": "point", "critical": True,
     "why": "Ground elevation at ring center; feeds the RAD-to-tower-height adjustment.",
     "url": f"https://epqs.nationalmap.gov/v1/json?x={SITE['lon']}&y={SITE['lat']}"
            f"&units=Feet&wkid=4326&includeDate=false"},
    {"name": "asr_towers_fcc_bulk", "kind": "head", "critical": True,
     "why": "FCC ASR weekly bulk file; towers within 2 mi and 0.5 mi.",
     "url": "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"},
]

# Services seen in the county's Hosted listing that map onto ordinance rules.
KNOWN_HOSTED = {
    "buildings_2024": ("Hosted/Building_Footprints_2024/FeatureServer",
                       "Dwelling proxy for Sec. 8.004.220.13 (tower >= own height from dwellings)."),
    "wetlands_county": ("Hosted/USFS_Wetlands_Dataset_Pottawattamie_County_6_6_2023/FeatureServer",
                        "County wetlands copy; cross-check against federal NWI."),
    "loess_hills": ("Hosted/Special_Landscape_Areas_Loess_Hills/FeatureServer",
                    "Drives A-4 Loess Hills, CUP-only per Sec. 8.014.030.15 (seen in 34659)."),
    "slope_protection": ("Hosted/Slope_Protection_Overlay_District_view/FeatureServer",
                         "SP overlay, Chapter 8.065; no human report surfaced it."),
    "airport_hazard": ("Hosted/Airport_Hazard_Polygons_view/FeatureServer",
                       "Chapter 8.067 height limits + Sec. 8.004.220.10 airport sign-off."),
    "zoning_doc_index": ("Hosted/Zoning_Document_Index_View/FeatureServer",
                         "May map districts to governing documents: a source URL per district."),
    "parcel_designations": ("Hosted/Parcel_Designations_View/FeatureServer",
                            "Check whether it carries owner/PIN or is only a classification."),
    "two_mile_area": ("Hosted/Two_Mile_Area/FeatureServer",
                      "City extraterritorial review; can pull a county parcel into city process."),
    "electric_service": ("Hosted/Electric_Service_Areas/FeatureServer",
                         "Power provider for the utilities section."),
    "spot_elevations": ("Hosted/Spot_Elevations/FeatureServer",
                        "Local elevation checks alongside USGS 3DEP."),
}

# What phase 2 hunts for, and how to recognize it in a layer name.
NEEDS = {
    "parcels": {"critical": True,
                "use": "Owner, PIN, site address, owner mailing address, acres. Backbone of the report.",
                "kw": ["parcel", "cadastral", "ownership", "property", "land record", "taxlot"],
                "neg": ["soil", "designation", "buffer", "protected", "survey", "index", "archive"]},
    "city_limits": {"critical": True,
                    "use": "Jurisdiction per parcel; flags parcels split by the boundary.",
                    "kw": ["city limit", "corporate", "municipal", "incorporated",
                           "city boundar", "jurisdiction"],
                    "neg": ["growth", "proposed"]},
    "buildings": {"critical": False, "use": "Dwelling proxy (Sec. 8.004.220.13).",
                  "kw": ["building", "footprint", "structure"], "neg": ["permit", "survey"]},
    "addresses": {"critical": False, "use": "Second dwelling signal; cross-check on site addresses.",
                  "kw": ["address point", "site address", "e911", "address"],
                  "neg": ["grid", "archive"]},
    "roads": {"critical": False,
              "use": "Access routes + the 75 ft setback abutting a highway or county road (8.010.070.04).",
              "kw": ["road", "centerline", "street", "highway"],
              "neg": ["sign", "calendar", "vacation", "easement", "service level", "atlas",
                      "suitability", "trail"]},
}


def _norm(s):
    return (s or "").lower().replace("_", " ").replace("-", " ")


def match_need(name, need):
    n = _norm(name)
    if any(_norm(b) in n for b in need.get("neg", [])):
        return False
    return any(_norm(k) in n for k in need["kw"])


# ------------------------------------------------------------------- RUN ---
def run_all():
    rep = {"probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "probe_version": "1.1", "site": SITE, "ring_envelope_wgs84": ENV,
           "targets": {}, "folders": {}, "matches": {k: [] for k in NEEDS},
           "known_hosted": {}, "deep_probes": {}}

    print("PHASE 1: known endpoints")
    for t in TARGETS:
        print(f"  {t['name']} ...", flush=True)
        base = {"why": t["why"], "critical": t["critical"], "kind": t["kind"]}
        try:
            if t["kind"] == "svc":
                base.update(probe_service(t["url"], t["kw"]))
            elif t["kind"] == "svc_candidates":
                base["candidates"] = []
                for u in t["urls"]:
                    r = probe_service(u, t["kw"])
                    base["candidates"].append(r)
                    if r.get("reachable"):
                        base["working_url"] = u
                        base.update({k: v for k, v in r.items() if k != "service_url"})
                        break
                base["reachable"] = "working_url" in base
                if not base["reachable"]:
                    base["error"] = "; ".join(
                        f"{c.get('service_url','?').split('//')[-1].split('/')[0]}: "
                        f"{str(c.get('error'))[:50]}" for c in base["candidates"])
            elif t["kind"] == "point":
                r = http_get(t["url"])
                base.update({"reachable": r["ok"], "url": t["url"]})
                if r["ok"]:
                    base["response"] = json.loads(r["body"].decode("utf-8", "replace"))
                else:
                    base["error"] = r.get("error")
            elif t["kind"] == "head":
                if SKIP_BULK:
                    base["skipped"] = True
                else:
                    r = http_get(t["url"], method="HEAD")
                    base.update({"reachable": r["ok"], "url": t["url"]})
                    if r["ok"]:
                        sz = r["headers"].get("content-length")
                        base["content_length_mb"] = (round(int(sz)/1048576, 1)
                                                     if sz and sz.isdigit() else None)
                        base["last_modified"] = r["headers"].get("last-modified")
                    else:
                        base["error"] = r.get("error")
        except Exception as e:
            base.update({"reachable": False, "error": f"probe crashed: {type(e).__name__}: {e}"})
        rep["targets"][t["name"]] = base

    print("\nPHASE 2: crawling folders for parcels / city limits / buildings")
    all_layers = []
    for folder in FOLDERS:
        print(f"  folder {folder} ...", flush=True)
        f = probe_folder(f"{COUNTY_ROOT}/{folder}")
        rep["folders"][folder] = f
        if not f.get("reachable"):
            continue
        for svc in f["services"]:
            if svc["type"] not in ("MapServer", "FeatureServer"):
                continue
            url = f"{COUNTY_ROOT}/{svc['name']}/{svc['type']}"
            s = get_json(url)
            if not s["ok"]:
                f.setdefault("dead_services", []).append({"name": svc["name"],
                                                          "error": s.get("error")})
                continue
            for l in summarize_layers(s["json"]):
                all_layers.append({**l, "service_url": url, "service_name": svc["name"],
                                   "folder": folder})
            time.sleep(0.05)
    rep["layers_seen"] = len(all_layers)
    print(f"  {len(all_layers)} layers found")

    for need_name, need in NEEDS.items():
        hits = [l for l in all_layers if match_need(l.get("name"), need)]
        rep["matches"][need_name] = [{"service": l["service_name"], "folder": l["folder"],
                                      "layer_id": l["id"], "layer_name": l["name"],
                                      "geometry": l.get("geometryType"),
                                      "service_url": l["service_url"]} for l in hits]
        for l in hits[:MAX_PROBE]:
            key = f"{need_name}::{l['service_name']}::{l['id']}"
            print(f"  probing {l['service_name']} / {l['name']}", flush=True)
            rep["deep_probes"][key] = {"need": need_name, "use": need["use"],
                                       "critical": need["critical"],
                                       **probe_layer(l["service_url"], l)}

    print("\nPHASE 3: known Hosted services")
    for key, (path, use) in KNOWN_HOSTED.items():
        print(f"  {key} ...", flush=True)
        url = f"{COUNTY_ROOT}/{path}"
        s = get_json(url)
        if not s["ok"]:
            rep["known_hosted"][key] = {"use": use, "reachable": False, "error": s.get("error")}
            continue
        layers = summarize_layers(s["json"])
        e = {"use": use, "reachable": True, "url": url, "layers": layers}
        if layers:
            e["layer_probe"] = probe_layer(url, layers[0])
        rep["known_hosted"][key] = e

    try:
        with open(OUT_FILE, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=str)
        import os
        rep["_out_path"] = os.path.abspath(OUT_FILE)
    except Exception as e:
        rep["_out_path"] = f"(could not write {OUT_FILE}: {e})"
    return rep


# --------------------------------------------------------- PASTE SUMMARY ---
def paste_block(rep):
    """Compact text to copy into chat. Avoids having to find probe.json."""
    L = []
    A = L.append
    A("===== DA PROBE PASTE BLOCK =====")
    A(f"probed_at: {rep['probed_at']}   layers_seen: {rep.get('layers_seen')}")
    A(f"out: {rep.get('_out_path')}")

    A("\n-- phase 1 --")
    for name, r in rep["targets"].items():
        if r.get("skipped"):
            A(f"SKIP {name}")
            continue
        if not r.get("reachable"):
            A(f"FAIL {name}: {str(r.get('error'))[:90]}")
            continue
        ml = r.get("matched_layers")
        if ml:
            bits = [f"{m['layer_name']}(id={m['layer_id']},"
                    f"n={(m.get('count_in_ring_envelope') or {}).get('count')},"
                    f"f={len((m.get('metadata') or {}).get('fields') or [])})" for m in ml]
            A(f"OK   {name}: {'; '.join(bits)}")
        else:
            extra = ""
            if r.get("content_length_mb"):
                extra = f" {r['content_length_mb']}MB {r.get('last_modified','')}"
            if r.get("response"):
                extra = f" {json.dumps(r['response'])[:120]}"
            A(f"OK   {name}:{extra}")
        if r.get("note"):
            A(f"       note: {r['note']}")

    A("\n-- folders --")
    for f, v in rep["folders"].items():
        if v.get("reachable"):
            A(f"  {f}: {len(v.get('services') or [])} services"
              f"{', ' + str(len(v['dead_services'])) + ' dead' if v.get('dead_services') else ''}")
        else:
            A(f"  {f}: UNREACHABLE {str(v.get('error'))[:60]}")

    A("\n-- layer matches --")
    for need, hits in rep["matches"].items():
        if not hits:
            A(f"  {need}: NONE")
            continue
        A(f"  {need}: {len(hits)}")
        for h in hits[:MAX_PROBE]:
            k = f"{need}::{h['service']}::{h['layer_id']}"
            dp = rep["deep_probes"].get(k, {})
            cnt = (dp.get("count_in_ring_envelope") or {}).get("count", "?")
            flds = [f["name"] for f in ((dp.get("metadata") or {}).get("fields") or [])]
            A(f"    {h['service']}[{h['layer_id']}] {h['layer_name']} ring={cnt}")
            if flds:
                A(f"      fields: {', '.join(flds[:40])}")
            sa = dp.get("sample_attributes")
            if sa:
                A(f"      sample: {json.dumps(sa, default=str)[:400]}")

    A("\n-- known hosted --")
    for k, e in rep["known_hosted"].items():
        if not e.get("reachable"):
            A(f"  FAIL {k}: {str(e.get('error'))[:70]}")
            continue
        lp = e.get("layer_probe") or {}
        cnt = (lp.get("count_in_ring_envelope") or {}).get("count", "?")
        flds = [f["name"] for f in ((lp.get("metadata") or {}).get("fields") or [])]
        A(f"  OK   {k}: {lp.get('layer_name')} ring={cnt} fields={', '.join(flds[:25])}")
    A("===== END =====")
    return "\n".join(L)


REPORT = run_all()
print()
print(paste_block(REPORT))
