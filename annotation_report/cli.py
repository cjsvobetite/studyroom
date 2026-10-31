"""python -m annotation_report report  --primary R1.zip [--second R2.zip] [--adjudicated R3.zip] [--ai AI.zip]
                                      [--meta meta.csv] [--config report.json] --out out_dir
   python -m annotation_report predict --export R1.zip --model-url http://127.0.0.1:8100 --out ai_dir
   python -m annotation_report demo    --out out_dir [--n 400] [--filled]   (synthetic data + example report)
"""
import argparse
import json
import os
import time

from . import analysis, render
from .loader import Export


def run_report(primary, second=None, adjudicated=None, ai=None, meta=None, config=None, out="report_out"):
    cfg = {}
    if config:
        with open(config, encoding="utf-8") as f:
            cfg = json.load(f)
    p = Export(primary)
    s = Export(second, ref_labels=p.labels) if second else None
    a = Export(adjudicated, ref_labels=p.labels) if adjudicated else None
    x = Export(ai, ref_labels=p.labels) if ai else None
    data = analysis.build(p, s, a, x, analysis.read_metadata(meta), cfg)
    data["generated_at"] = time.strftime("%Y-%m-%d %H:%M")
    os.makedirs(out, exist_ok=True)
    items = render.gallery(data, p, a, x, data["config"]["gallery"])
    with open(os.path.join(out, "report.html"), "w", encoding="utf-8") as f:
        f.write(render.render(data, items))
    analysis.write_json(data, os.path.join(out, "report.json"))
    analysis.write_cases_csv(data, os.path.join(out, "cases.csv"))
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(prog="annotation_report", description="Automatic annotation report from platform exports")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="build the report from export zips / folders")
    r.add_argument("--primary", required=True, help="R1 export (every case)")
    r.add_argument("--second", help="R2 export (second reader, usually a sample)")
    r.add_argument("--adjudicated", help="R3 export (confirmed labels for the cases it contains)")
    r.add_argument("--ai", help="AI predictions (export layout or a folder with masks/<stem>.png)")
    r.add_argument("--meta", help="CSV with name + subgroup columns (age, sex, device, ...)")
    r.add_argument("--config", help="report.json: cover, endpoint, thresholds, subgroups, readers")
    r.add_argument("--out", default="report_out")
    p = sub.add_parser("predict", help="AI auto-segmentation of an export through the model service")
    p.add_argument("--export", required=True)
    p.add_argument("--model-url", default=os.environ.get("MODEL_URL", "http://127.0.0.1:8100"))
    p.add_argument("--out", required=True)
    d = sub.add_parser("demo", help="synthetic OCT data + the example report")
    d.add_argument("--out", default="demo_out")
    d.add_argument("--n", type=int, default=400)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--filled", action="store_true", help="fill every specialist / LLM slot with fictional text")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        data = run_report(args.primary, args.second, args.adjudicated, args.ai, args.meta, args.config, args.out)
        print(f"{len(data['cases'])} cases -> {os.path.join(args.out, 'report.html')}")
        for w in data["warnings"]:
            print("warning:", w)
    elif args.cmd == "predict":
        from .ai_predict import predict_export
        n = predict_export(args.export, args.model_url, args.out)
        print(f"{n} predictions -> {args.out}")
    else:
        from .demo.make_demo import make_demo
        paths = make_demo(args.out, args.n, args.seed, args.filled)
        run_report(**paths, out=args.out)
        print(f"demo report -> {os.path.join(args.out, 'report.html')}")
