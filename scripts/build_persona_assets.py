"""Export authored prototype illustrations, with no simulated tourist records."""
from __future__ import annotations
import csv
import io
import json
import os
from pathlib import Path
import tempfile

# Cache writes stay in a disposable writable directory.
with tempfile.TemporaryDirectory(prefix="jeju-mpl-") as cache:
    os.environ.setdefault("MPLCONFIGDIR", cache)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    import numpy as np

from persona_model import load_catalog, ROOT


def main():
    c = load_catalog()
    assets = ROOT / "reports/assets"
    assets.mkdir(exist_ok=True, parents=True)
    font = next((f.fname for f in font_manager.fontManager.ttflist if f.name == "Noto Sans CJK KR"), None)
    if font is None:
        font = next(f.fname for f in font_manager.fontManager.ttflist if f.name.startswith("Noto Sans CJK"))
    fp = font_manager.FontProperties(fname=font)
    plt.rcParams.update({"font.family": fp.get_name(), "axes.unicode_minus": False, "font.size": 10})
    scalars = [s["id"] for s in c["scalars"]]
    labels = [t["label"] for t in c["themes"]] + [s["label"] for s in c["scalars"]]
    rows = np.array([p["themes"] + [p[k] for k in scalars] for p in c["prototypes"]])
    names = [p["id"] + " " + p["label"] for p in c["prototypes"]]
    fig, ax = plt.subplots(figsize=(14.5, 6.5), layout="constrained")
    im = ax.imshow(rows, cmap="YlGnBu", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(13), labels, rotation=35, ha="right")
    ax.set_yticks(range(8), names)
    for i in range(8):
        for j in range(13):
            ax.text(j, i, f"{rows[i,j]:.2f}", ha="center", va="center", fontsize=8,
                    color="white" if rows[i,j] > .6 else "#162c38")
    ax.axvline(8.5, color="#b04632", linewidth=2)
    ax.set_title("8개 페르소나 설계 후보 · 13개 취향 축", loc="left", fontsize=17, weight="bold", pad=20)
    ax.set_xlabel("왼쪽: 9개 테마의 상대 비중(행 합=1)  |  오른쪽: 각각 독립적인 0~1 선호값\n수작업 설계 가설 · 실제 관광객 표본 0건 · 학습된 군집이나 선호 비율이 아님", labelpad=12)
    fig.colorbar(im, ax=ax, label="설계값 (0~1)", shrink=.8)
    for ext in ["png", "svg"]:
        fig.savefig(assets / f"persona-prototypes-heatmap.{ext}", dpi=170, facecolor="white")
    plt.close(fig)

    # Weighted feature projection is only an illustration, not the match metric.
    projected = rows.copy()
    projected[:, :9] /= np.linalg.norm(projected[:, :9], axis=1)[:, None]
    projected[:, :9] *= np.sqrt(c["weights"]["themes"])
    for j, key in enumerate(scalars, 9):
        projected[:, j] *= np.sqrt(c["weights"][key])
    mean = projected.mean(axis=0)
    centered = projected - mean
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:2].copy()
    for v in components:
        if v[np.argmax(np.abs(v))] < 0:
            v *= -1
    points = centered @ components.T
    ratios = singular[:2] ** 2 / sum(singular ** 2)
    c["projection"] = {"mean": mean.tolist(), "components": components.tolist(),
                       "variance_ratio": ratios.tolist(), "points": points.tolist()}
    fig, ax = plt.subplots(figsize=(11, 7.5), layout="constrained")
    palette = ["#177e89","#538b35","#ba6842","#8064a2","#ce4754","#be8c21","#3e6f9b","#ae5b88"]
    for i, (x, y) in enumerate(points):
        ax.scatter(x, y, s=180, color=palette[i], zorder=3, edgecolor="white", linewidth=1.5)
        offset = (9, -32) if i == 2 else (9, 10 if i%2 else -20)
        ax.annotate(names[i], (x,y), xytext=offset, textcoords="offset points", fontsize=10)
    xr, yr = np.ptp(points[:,0]), np.ptp(points[:,1])
    ax.set_xlim(points[:,0].min()-.17*xr, points[:,0].max()+.60*xr)
    ax.set_ylim(points[:,1].min()-.25*yr, points[:,1].max()+.25*yr)
    ax.axhline(0,color="#e1e7ea",lw=1); ax.axvline(0,color="#e1e7ea",lw=1)
    ax.set_xlabel(f"주성분 1 · 설계 행렬 분산 {ratios[0]*100:.1f}%")
    ax.set_ylabel(f"주성분 2 · 설계 행렬 분산 {ratios[1]*100:.1f}%")
    ax.set_title("페르소나 설계 후보의 상대 위치", fontsize=17, loc="left", weight="bold", pad=18)
    ax.text(0,1.01,"점 하나 = 수작업 프로토타입 하나 · 관광객 분포/군집 경계가 아님", transform=ax.transAxes, fontsize=10)
    fig.text(.50,-.025,"13차원을 2차원으로 투영한 그림. 근접/중첩은 설계값의 유사성만 뜻하며 매칭 점수·군집 타당성의 증거가 아님.",ha="center",fontsize=9)
    ax.spines[["top","right"]].set_visible(False)
    for ext in ["png","svg"]:
        fig.savefig(assets / f"persona-prototypes-map.{ext}", dpi=170, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    (assets / "persona-projection.json").write_text(json.dumps(c["projection"], ensure_ascii=False, indent=2)+"\n")
    with (assets / "persona-prototypes.csv").open("w",encoding="utf-8",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id","label","provenance"]+[t["id"] for t in c["themes"]]+scalars)
        for p, row in zip(c["prototypes"], rows):
            writer.writerow([p["id"],p["label"],"manually_authored_not_learned"]+row.tolist())
    template = (ROOT / "scripts/persona_explorer_template.html").read_text()
    assert template.count("__PERSONA_DATA__") == 1
    payload = json.dumps(c, ensure_ascii=False).replace("<","\\u003c")
    (ROOT / "reports/persona-explorer.html").write_text(template.replace("__PERSONA_DATA__",payload))
    print("Built heatmap, PCA map, CSV, projection metadata and standalone HTML; real respondents = 0")


if __name__ == "__main__":
    main()
