"""Flask wrapper around `pipeline.run()`.

Runs on localhost only. The pipeline needs the local venv, network access to
county ArcGIS, the ordinance snapshots in data/ and the ASR cache, so none of
this is hostable and none of it is meant to be.

Every run goes through the same `pipeline.run()` the CLI calls. The only thing
this module adds is a progress stream, a JSON view of the report and the
sensitivity re-run, which recomputes geometry against already-fetched layers.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import traceback
import uuid
from typing import Any, Optional

from flask import (Flask, Response, jsonify, request, send_file,
                   send_from_directory)

from ..analysis.evaluate import evaluate_parcel
from ..analysis.letters import assign_letters
from ..cli import parse_row, read_sheet
from ..evidence import Ledger
from ..models import Disposition, SiteInput
from ..pipeline import load_config, run as run_pipeline
from ..report.build import build
from .serialize import binding_constraint, report_json

STATIC = os.path.join(os.path.dirname(__file__), "static")

# Which sites can be compared is a question about what is on disk, not a list
# to maintain. An earlier version hardcoded the calibration ids here and drifted
# from tools/calibrate.py within a day, marking the wrong rows, so the answer is
# now read from the human_reports directory itself.
_OPP_IN_FILENAME = re.compile(r"(\d{4,6})")

RUNS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _state(run_id: str) -> Optional[dict]:
    with _LOCK:
        return RUNS.get(run_id)


def create_app(*, sheet: str = "tranche.csv", out_dir: str = "outputs",
               config_path: str = "config/client_rules.yml",
               ordinance: str = "data/ordinance.txt",
               human_reports: str = "human_reports",
               derived_sheet: str = "data/derived_sites.csv") -> Flask:
    app = Flask(__name__, static_folder=None)
    # Absolute, because Flask resolves a relative send_file path against the
    # app's root_path (this module's directory), not the working directory the
    # server was started from. Everything else here is relative to the repo
    # root the way the CLI expects.
    app.config.update(SHEET=os.path.abspath(sheet),
                      OUT=os.path.abspath(out_dir),
                      CONFIG=os.path.abspath(config_path),
                      ORDINANCE=os.path.abspath(ordinance),
                      HUMAN=os.path.abspath(human_reports),
                      DERIVED=os.path.abspath(derived_sheet))

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    # ---------------------------------------------------------------- sites
    @app.get("/api/sites")
    def sites():
        """Tranche rows for the dropdown, flagged for calibration availability."""
        human = _human_reports(app.config["HUMAN"])
        all_rows, status = _sheet_rows_with_status(app)
        rows = [{
            "opportunity_id": site.opportunity_id,
            "latitude": site.latitude,
            "longitude": site.longitude,
            "radius_ft": site.search_ring_radius_ft,
            "rad_ft": site.requested_rad_ft,
            "state": site.state,
            "derived": derived,
            "has_human_report": site.opportunity_id in human,
        } for site, derived, _ in all_rows]
        return jsonify({"sheet": app.config["SHEET"],
                        "found": bool(rows), "rows": rows,
                        "sources": status,
                        "human_reports": sorted(human.keys())})

    # ------------------------------------------------------------------ run
    @app.post("/api/run")
    def start_run():
        body = request.get_json(force=True) or {}
        try:
            site = _site_from_request(body, app)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        run_id = uuid.uuid4().hex[:12]
        with _LOCK:
            RUNS[run_id] = {"status": "running", "events": [],
                            "site": site, "result": None, "error": None,
                            "docx": None, "report": None, "ledger": None,
                            "collected": None, "compare": None}

        opts = {
            "skip_asr": bool(body.get("skip_asr")),
            "skip_elevation": bool(body.get("skip_elevation")),
            "compare": bool(body.get("compare")),
        }
        t = threading.Thread(target=_execute, args=(app, run_id, site, opts),
                             daemon=True)
        t.start()
        return jsonify({"run_id": run_id})

    @app.get("/api/events/<run_id>")
    def events(run_id):
        st = _state(run_id)
        if st is None:
            return jsonify({"error": "unknown run"}), 404

        def stream():
            # Read the event list by index rather than from a queue. The worker
            # starts before the browser subscribes, so a replay-then-drain
            # scheme delivered the early events twice; an index cannot.
            i = 0
            while True:
                events = st["events"]
                while i < len(events):
                    item = events[i]
                    i += 1
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get("type") in ("done", "error"):
                        return
                if st["status"] != "running":
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return
                time.sleep(0.12)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.get("/api/result/<run_id>")
    def result(run_id):
        st = _state(run_id)
        if st is None:
            return jsonify({"error": "unknown run"}), 404
        if st["status"] == "running":
            return jsonify({"status": "running"}), 202
        if st["status"] == "error":
            return jsonify({"status": "error", "error": st["error"]}), 500
        payload = dict(st["result"])
        payload["status"] = "done"
        payload["comparison"] = st["compare"]
        return jsonify(payload)

    # -------------------------------------------------------------- outputs
    @app.get("/api/docx/<run_id>")
    def docx(run_id):
        st = _state(run_id)
        if st is None or not st.get("docx") or not os.path.exists(st["docx"]):
            return jsonify({"error": "no document for this run"}), 404
        return send_file(st["docx"], as_attachment=True,
                         download_name=os.path.basename(st["docx"]))

    @app.get("/api/map/<run_id>/<name>")
    def map_png(run_id, name):
        st = _state(run_id)
        if st is None or st.get("report") is None:
            return jsonify({"error": "unknown run"}), 404
        path = st["report"].map_paths.get(name)
        if not path or not os.path.exists(path):
            return jsonify({"error": "no such map"}), 404
        return send_file(path, mimetype="image/png")

    # ---------------------------------------------------------- sensitivity
    @app.post("/api/sensitivity/<run_id>")
    def sensitivity(run_id):
        """Re-run gates 3 to 6 at a different fall-distance ratio.

        Uses the layers already fetched for this run, so it costs no network
        and completes in well under a second. Sec. 8.004.220.12's setback is a
        manufacturer figure that does not exist at desktop stage, and this is
        the assumption that moves results most, so being able to show the
        sensitivity directly is worth more than describing it.
        """
        st = _state(run_id)
        if st is None or st.get("collected") is None:
            return jsonify({"error": "no cached geometry for this run"}), 404
        try:
            ratio = float((request.get_json(force=True) or {}).get("ratio", 1.0))
        except (TypeError, ValueError):
            return jsonify({"error": "ratio must be a number"}), 400
        if not 0.0 <= ratio <= 2.0:
            return jsonify({"error": "ratio must be between 0 and 2"}), 400

        c = st["collected"]
        cfg = c["config"]
        ccfg = cfg.get("compound", {})
        # A scratch ledger seeded from the real one. The parcels cite parent
        # evidence ids, and the ledger refuses computed records whose parents
        # it has never seen, so a blank ledger would fail that check. Seeding a
        # copy keeps the integrity guard honest while keeping this what-if's
        # records out of the delivered audit's evidence.
        ledger = _scratch_ledger(st["ledger"])

        # Evaluation mutates the parcel, so work on copies. Geometry is shared
        # by reference deliberately: shapely objects are immutable in use here
        # and copying them for every slider tick would be the slow part.
        parcels = [_shallow_parcel(p) for p in st["report"].parcels]
        for p in parcels:
            evaluate_parcel(
                p, c["ring"], c["tower"], ledger,
                wetlands=c["wetlands"], flood=c["flood"],
                dwelling_points=c["dwelling_points"],
                compound_primary_ft=ccfg.get("primary_ft", 100.0),
                compound_exception_ft=ccfg.get("exception_ft", 80.0),
                apply_dwelling_rule=cfg.get("dwellings", {}).get("apply_rule", True),
                fall_distance_ratio=ratio,
            )
        parcels = assign_letters(parcels, c["ring"])

        counts: dict[str, int] = {}
        for p in parcels:
            label = binding_constraint(p)
            if label:
                counts[label] = counts.get(label, 0) + 1
        height = c["tower"].overall_height_ft
        return jsonify({
            "ratio": ratio,
            "setback_ft": round(height * ratio, 1),
            "candidates": sum(1 for p in parcels
                              if p.disposition is Disposition.PURSUE),
            "dispositions": {d.value: sum(1 for p in parcels
                                          if p.disposition is d)
                             for d in Disposition},
            "binding_counts": counts,
            "parcels": [{"letter": p.letter, "pin": p.pin, "owner": p.owner,
                         "disposition": p.disposition.value if p.disposition else None,
                         "binding_constraint": binding_constraint(p),
                         "buildable_sqft": (round(p.buildable_sqft, 1)
                                            if p.buildable_sqft else None),
                         "compound_fit": p.compound_fit}
                        for p in parcels],
        })

    return app


# ------------------------------------------------------------------ helpers

def _scratch_ledger(source: Ledger) -> Ledger:
    """A ledger that can resolve the real run's ids but discards what it adds.

    Evidence records are read-only once written, so the entries are shared by
    reference; only the index is copied.
    """
    scratch = Ledger()
    scratch._items = dict(source._items)   # noqa: SLF001 - deliberate, see above
    scratch._n = source._n                 # noqa: SLF001
    return scratch


def _shallow_parcel(p):
    """A copy whose evaluated fields are reset but whose geometry is shared."""
    q = copy.copy(p)
    q.districts = list(p.districts)
    q.findings = []
    q.unresolved = []
    q.risks = []
    q.conditions = []
    q.reason_codes = []
    q.evidence_ids = list(p.evidence_ids)
    q.disposition = None
    q.buildable_sqft = None
    q.compound_fit = None
    return q


def _human_reports(directory: str) -> dict[str, str]:
    """Opportunity id to .docx path, for every human audit actually present.

    Matches tools/calibrate.py's own filename convention rather than filtering
    against a list, so adding a report to the directory is all it takes.
    """
    out: dict[str, str] = {}
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".docx") or name.startswith("~$"):
            continue
        m = _OPP_IN_FILENAME.search(name)
        if m:
            out.setdefault(f"TI-OPP-{m.group(1)}", os.path.join(directory, name))
    return out


def _sheet_rows(app: Flask) -> list[tuple[SiteInput, bool]]:
    """Every runnable row: the tranche release, then any derived sites.

    TI-OPP-43986 has no row in the release sheet, so its inputs were
    reconstructed from its human audit header and kept separately. It is a
    calibration site, so it has to be selectable, but it is flagged derived
    everywhere it appears because its inputs are not independent of the report
    it gets compared against.
    """
    return [(s, d) for s, d, _ in _sheet_rows_with_status(app)[0]]


def _sheet_rows_with_status(app: Flask
                            ) -> tuple[list[tuple[SiteInput, bool, str]],
                                       list[dict]]:
    """Rows plus what happened to each source file.

    The status half exists because a missing release sheet used to be obvious,
    the list was simply empty, and stopped being obvious once derived sites
    were added: one row still loaded, so the UI looked like it had worked. Each
    source now reports itself and the UI says which one is missing.
    """
    rows: list[tuple[SiteInput, bool, str]] = []
    status: list[dict] = []
    for path, derived, label in ((app.config["SHEET"], False, "release sheet"),
                                 (app.config["DERIVED"], True, "derived sites")):
        entry = {"label": label, "path": path, "found": False,
                 "rows": 0, "skipped": 0}
        status.append(entry)
        if not path or not os.path.exists(path):
            continue
        entry["found"] = True
        try:
            raw_rows = list(read_sheet(path))
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"
            continue
        for raw in raw_rows:
            try:
                site = parse_row(raw)
            except Exception:  # noqa: BLE001 - a bad row must not kill the list
                entry["skipped"] += 1
                continue
            if derived:
                site.input_provenance = "derived_from_report"
            rows.append((site, derived, label))
            entry["rows"] += 1
    return rows, status


def _site_from_request(body: dict, app: Flask) -> SiteInput:
    """Either a known row by id, or an ad-hoc site from explicit fields."""
    oid = (body.get("opportunity_id") or "").strip()
    if oid and not body.get("ad_hoc"):
        rows = _sheet_rows(app)
        for site, _ in rows:
            if site.opportunity_id == oid:
                return site
        if not rows:
            raise ValueError(f"no rows found in {app.config['SHEET']}")
        raise ValueError(f"{oid} is not in the release sheet or derived sites")

    missing = [k for k in ("lat", "lon", "radius_ft", "rad_ft")
               if body.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
        radius = float(body["radius_ft"])
        rad = float(body["rad_ft"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"lat, lon, radius_ft and rad_ft must be numbers: {e}")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("coordinates out of range")
    if radius <= 0 or rad <= 0:
        raise ValueError("radius_ft and rad_ft must be positive")

    return SiteInput(
        opportunity_id=oid or "AD-HOC",
        latitude=lat, longitude=lon,
        search_ring_radius_ft=radius,
        requested_rad_ft=rad,
        state=(body.get("state") or "IA").upper(),
        input_provenance="derived_from_report" if body.get("derived")
                         else "supplied_directly",
    )


def _execute(app: Flask, run_id: str, site: SiteInput, opts: dict) -> None:
    """Run the pipeline on a worker thread, appending progress events."""
    st = _state(run_id)

    def emit(item: dict) -> None:
        st["events"].append(item)

    def log(line: str) -> None:
        emit({"type": "log", "line": str(line).rstrip()})

    collected: dict[str, Any] = {}

    try:
        emit({"type": "log", "line": f"=== {site.opportunity_id} ==="})
        cfg = load_config(app.config["CONFIG"])
        report, ledger = run_pipeline(
            site, config=cfg, out_dir=app.config["OUT"],
            ordinance_path=app.config["ORDINANCE"],
            skip_asr=opts["skip_asr"], skip_elevation=opts["skip_elevation"],
            log=log, collect=collected.update)

        site_dir = os.path.join(app.config["OUT"], site.opportunity_id)
        os.makedirs(site_dir, exist_ok=True)
        docx_path = os.path.join(
            site_dir, f"Desktop Audit - {site.opportunity_id}.docx")
        log("  building document...")
        build(report, ledger, docx_path, assumptions=report.assumptions)
        ledger.to_json(os.path.join(site_dir, "evidence.json"))

        payload = report_json(report, ledger, run_id=run_id, docx_path=docx_path)

        compare = None
        if opts["compare"]:
            compare = _compare(app, site.opportunity_id, report, ledger, log)

        with _LOCK:
            st.update(status="done", result=payload, docx=docx_path,
                      report=report, ledger=ledger,
                      collected=collected or None, compare=compare)
        emit({"type": "done"})
    except Exception as e:  # noqa: BLE001 - the browser needs the reason
        with _LOCK:
            st.update(status="error", error=f"{type(e).__name__}: {e}")
        emit({"type": "error", "error": f"{type(e).__name__}: {e}",
              "traceback": traceback.format_exc()[-2000:]})


def _compare(app: Flask, oid: str, report, ledger, log) -> Optional[dict]:
    """Run the existing calibration comparison, if a human report is present."""
    human = _human_reports(app.config["HUMAN"]).get(oid)
    if not human:
        log(f"  no human report found for {oid}; skipping comparison")
        return None
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        tools = os.path.join(root, "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import calibrate  # type: ignore

        log(f"  comparing against {os.path.basename(human)}")
        return calibrate.compare_site(calibrate.extract_human(human),
                                      report, ledger)
    except Exception as e:  # noqa: BLE001 - a failed comparison is not a failed run
        log(f"  comparison failed: {type(e).__name__}: {e}")
        return None


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="da serve",
                                description="Local UI for the audit pipeline")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--sheet", default="tranche.csv")
    p.add_argument("--out", default="outputs")
    p.add_argument("--config", default="config/client_rules.yml")
    p.add_argument("--ordinance", default="data/ordinance.txt")
    p.add_argument("--human-reports", default="human_reports")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)

    app = create_app(sheet=args.sheet, out_dir=args.out,
                     config_path=args.config, ordinance=args.ordinance,
                     human_reports=args.human_reports)
    print(f"Desktop Audit UI on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
