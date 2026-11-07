"""選んだ RAW だけ、29 バンドの配列を .npy にする。日付フォルダごとに 再構成 → npy 化 を終えてから次の日付へ進む。

    再構成結果（PNG）があるものは PNG から、無いものは SDK で復元して直接 npy にする（DIRECT_NPY）。

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
from npy_dtype import npy_mb
from reconstruct_missing import reconstruct_missing
from reconstruct_to_npy import reconstruct_to_npy
from run_log import RunLog
from select_targets import select_targets

NO_SPACE_ADVICE = "npy 化が終わった日付の再構成結果を消して容量を空け、もう一度実行してください。"


def main():
    check_settings()
    log = RunLog()  # 1 枚ごとの秒数と、何枚を何時間で作ったかを LOG_DIR に記録する
    note = ""
    try:
        code, note = make_all(log)
        return code
    except BaseException as e:  # Ctrl+C などで止めたときも、そこまでの記録を残す
        note = f"途中で止まりました（{type(e).__name__}）"
        raise
    finally:
        log.finish(note)


def make_all(log):
    """日付ごとに npy を作り、結果を表示する。戻り値: (終了コード, 途中で止めた理由 or "")"""
    names = load_names(settings.NAMES_FILE) if settings.NAMES_FILE else None  # None: RAW 全部
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

    made, failed, existing, no_scene, no_roi, unreadable = [], [], [], [], [], []
    deleted = 0
    stopped_at = None
    for n, (date, date_found) in enumerate(by_date.items(), start=1):
        print(f"\n===== {date}（{n}/{len(by_date)}）=====")
        log.begin_date(date)
        picked = select_targets(date_found, calibration)
        log.count_skipped(len(picked["existing"]), len(picked["unreadable"]))
        missing_names = {name for folder_names in picked["missing"].values() for name in folder_names}
        n_missing = len(missing_names)
        direct = settings.RECONSTRUCT_MISSING and settings.DIRECT_NPY  # 再構成結果が無いものは SDK から直接 npy

        # 容量が足りなければ、この日付を始める前に止める
        n_npy = len(picked["targets"]) + (n_missing if settings.RECONSTRUCT_MISSING else 0)
        if not check_disk_space(settings.OUTPUT_DIR, n_npy, npy_mb()):
            stopped_at = (f"npy の保存先の容量が足りないため、{date} から先は処理していません", NO_SPACE_ADVICE)
            break
        if settings.RECONSTRUCT_MISSING and not direct and n_missing:  # PNG に再構成してから npy にする
            settings.RECONSTRUCT_ROOT.mkdir(parents=True, exist_ok=True)
            if not check_disk_space(settings.RECONSTRUCT_ROOT, n_missing, settings.RECONSTRUCT_MB_PER_RAW):
                stopped_at = (f"再構成結果の保存先の容量が足りないため、{date} から先は処理していません",
                              NO_SPACE_ADVICE)
                break
            if reconstruct_missing(picked["missing"], calibration):
                picked = select_targets(date_found, calibration)

        if picked["targets"]:
            print(f"--- npy 作成（再構成結果の PNG から）: {len(picked['targets'])} 件 ---")
        date_made, date_failed, stalled = make_npy_files(picked["targets"], dark, log)
        if direct and n_missing and not stalled:
            print(f"--- npy 作成（SDK から直接）: {n_missing} 件 ---")
            direct_result = reconstruct_to_npy(picked["missing"], calibration, dark, log)
            date_made += direct_result["made"]
            date_failed += direct_result["failed"]
            no_roi += direct_result["no_roi"]
            picked["missing"] = {}  # 作れなかったものは「失敗」に入っているので、「再構成結果が無い名前」には入れない
        print(f"{date}: 作成 {len(date_made)} 件 / 既存スキップ {len(picked['existing'])} 件 / "
              f"失敗 {len(date_failed)} 件")
        log.end_date("Box Drive の読み込みが終わらず途中で止めた" if stalled else "")

        # この日付で再構成して npy にできたものだけ、再構成結果を消す（npy 化に失敗したものは残す）
        if settings.DELETE_RECONSTRUCT_AFTER_NPY:
            deleted += delete_reconstruct([scene for scene, _roi in picked["targets"]
                                           if scene.output_stem in missing_names
                                           and scene.output_stem in date_made])
        made += date_made
        failed += date_failed
        existing += picked["existing"]
        no_roi += picked["no_roi"]
        unreadable += picked["unreadable"]
        if picked["unreadable"]:
            print(f"{date}: 一時的なエラーで読めず飛ばした {len(picked['unreadable'])} 件（最後にまとめて表示）")
        no_scene += [name for folder_names in picked["missing"].values() for name in folder_names]
        if stalled:  # Box Drive がファイルを渡してくれない。続けても1枚ごとに待たされるだけなので止める
            stopped_at = (f"再構成バンドの読み込みが終わらないため、{date} の途中で止めました",
                          "Box Drive を終了→起動して、同期が落ち着いてからもう一度実行してください。")
            break

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
        ("Box Drive などの一時的なエラーで読めず、飛ばした名前（npy は作っていません）", unreadable),
        ("失敗", failed),
    ]:
        if items:
            print(f"--- {title} ({len(items)} 件) ---")
            for item in items:
                print(f"  {item}")
    if unreadable:
        print("  Box Drive の同期が落ち着いてから（直らなければ Box Drive を終了→起動してから）もう一度実行してください。")
        print("  npy 化が終わった RAW は飛ばして、続きから処理します。")
    if stopped_at:
        headline, advice = stopped_at
        print(f"--- {headline} ---")
        print(f"  {advice}")
        print("  npy 化が終わった RAW は飛ばして、続きから処理します。")
    return (1 if failed or unreadable or stopped_at else 0), (stopped_at[0] if stopped_at else "")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    sys.exit(main())
