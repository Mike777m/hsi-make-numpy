"""選んだ RAW だけ、29 バンドの配列を .npy にする。日付フォルダごとに 再構成 → npy 化 を終えてから次の日付へ進む。

    使い方: settings.py を書き換えて  python make_numpy/main.py
    説明  : make_numpy/説明.md
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))  # hsi2rgb を読み込むため

from hsi2rgb import fileio, white_board

import settings
from check_disk_space import check_disk_space
from check_settings import check_settings
from delete_reconstruct import delete_reconstruct
from find_raw import find_raw
from load_names import load_names
from make_npy_files import make_npy_files
from reconstruct_missing import reconstruct_missing
from select_targets import select_targets

NPY_MB = 321  # npy 1 枚 (29, 1440, 1920) float32


def main():
    check_settings()
    names = load_names(settings.NAMES_FILE)
    calibration = {}
    if settings.WHITE_CORRECTION:
        calibration = fileio.load_calibration_json(str(settings.CALIBRATION_JSON))
        print(f"白板ROIの記録 {len(calibration)} 件")
    found, not_found, duplicated = find_raw(names)
    dark = white_board.DarkFrame(settings.DARK_DIR) if settings.BLACK_CORRECTION else None
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 日付フォルダごとに分ける（"10082026/10" → "10082026"）
    by_date = {}
    for folder, folder_names in found.items():
        by_date.setdefault(folder.name.split("/")[0], {})[folder] = folder_names

    made, failed, existing, no_scene, no_roi = [], [], [], [], []
    deleted = 0
    stopped_at = None
    for n, (date, date_found) in enumerate(by_date.items(), start=1):
        print(f"\n===== {date}（{n}/{len(by_date)}）=====")
        picked = select_targets(date_found, calibration)
        missing_names = {name for folder_names in picked["missing"].values() for name in folder_names}
        n_missing = len(missing_names)

        # 容量が足りなければ、この日付を始める前に止める
        n_npy = len(picked["targets"]) + (n_missing if settings.RECONSTRUCT_MISSING else 0)
        if not check_disk_space(settings.OUTPUT_DIR, n_npy, NPY_MB):
            stopped_at = (date, "npy の保存先")
            break
        if settings.RECONSTRUCT_MISSING and n_missing:
            settings.RECONSTRUCT_ROOT.mkdir(parents=True, exist_ok=True)
            if not check_disk_space(settings.RECONSTRUCT_ROOT, n_missing, settings.RECONSTRUCT_MB_PER_RAW):
                stopped_at = (date, "再構成結果の保存先")
                break
            if reconstruct_missing(picked["missing"], calibration):
                picked = select_targets(date_found, calibration)

        print("--- npy 作成 ---")
        date_made, date_failed = make_npy_files(picked["targets"], dark)
        print(f"{date}: 作成 {len(date_made)} 件 / 既存スキップ {len(picked['existing'])} 件 / "
              f"失敗 {len(date_failed)} 件")

        # この日付で再構成して npy にできたものだけ、再構成結果を消す（npy 化に失敗したものは残す）
        if settings.DELETE_RECONSTRUCT_AFTER_NPY:
            deleted += delete_reconstruct([scene for scene, _roi in picked["targets"]
                                           if scene.output_stem in missing_names
                                           and scene.output_stem in date_made])
        made += date_made
        failed += date_failed
        existing += picked["existing"]
        no_roi += picked["no_roi"]
        no_scene += [name for folder_names in picked["missing"].values() for name in folder_names]

    # 結果
    print("--- 完了 ---")
    print(f"作成 {len(made)} 件 / 既存スキップ {len(existing)} 件 / 失敗 {len(failed)} 件")
    if deleted:
        print(f"npy 化のために再構成して、npy を作ったあと消した再構成結果: {deleted} 件")
    for title, items in [
        ("RAW が見つからない名前（RAW名の綴り・DATES・RAW_ROOT を確認）", not_found),
        ("同じ名前の RAW が複数のフォルダにある名前（DATES で日付を絞ってください）", duplicated),
        ("再構成結果が無い名前（RECONSTRUCT_ROOT を確認）", no_scene),
        ("calibration.json に白板ROIが無い名前（RGB 化で白板スキップされた等）", no_roi),
        ("失敗", failed),
    ]:
        if items:
            print(f"--- {title} ({len(items)} 件) ---")
            for item in items:
                print(f"  {item}")
    if stopped_at:
        date, where = stopped_at
        print(f"--- {where}の容量が足りないため、{date} から先は処理していません ---")
        print("  npy 化が終わった日付の再構成結果を消して容量を空け、もう一度実行してください。")
        print("  npy 化が終わった RAW は飛ばして、続きから処理します。")
    return 1 if failed or stopped_at else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    sys.exit(main())
