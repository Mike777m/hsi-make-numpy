import os

from hsi2rgb import dataset
from hsi2rgb.fs_retry import TransientIOError, retry_io

import settings


def select_targets(found, calibration):
    """再構成結果・白板ROI・既存の npy を見て、RAW 名を振り分ける。

    戻り値の辞書:
        targets     [(Scene, roi)]     npy を作るもの（白板補正なしなら roi は None）
        existing    [RAW名]            npy が既にあるもの
        missing     {Folder: [RAW名]}  再構成結果が無いもの
        no_roi      [RAW名]            白板ROIの記録が無いもの
        unreadable  [RAW名と理由]      Box Drive などの一時的なエラーで確認できなかったもの（次の実行に回す）
    """
    picked = {"targets": [], "existing": [], "missing": {}, "no_roi": [], "unreadable": []}

    # 既存の npy は、1件ずつ確認せずに保存先の一覧を1回だけ読んで調べる
    # （保存先が読めないときに、1件ごとにやり直して待たされないように）
    existing_npy = set()
    if not settings.OVERWRITE:
        try:
            existing_npy = {n.lower() for n in retry_io(os.listdir, settings.OUTPUT_DIR)}
        except TransientIOError as e:
            for names in found.values():
                picked["unreadable"] += [f"{name}  npy の保存先: {e}" for name in names]
            return picked

    for folder, names in found.items():
        todo = []
        for name in names:
            # npy が既にあれば、再構成結果を消してあっても作成済みとして飛ばす
            if f"{name}.npy".lower() in existing_npy:
                picked["existing"].append(name)
            else:
                todo.append(name)
        if not todo:
            continue  # 全部作成済みなら再構成結果のフォルダは見に行かない

        scenes = {}
        try:
            if dataset.detect_layout(folder.input_dir) is not None:
                for scene in dataset.discover_scenes(folder.input_dir, folder.raw_dir, verbose=False):
                    scenes[scene.output_stem] = scene
        except TransientIOError as e:  # 時間フォルダの一覧そのものが読めない
            picked["unreadable"] += [f"{name}  {e}" for name in todo]
            continue

        for name in todo:
            scene = scenes.get(name)
            if scene is None:
                picked["missing"].setdefault(folder, []).append(name)
            elif scene.unreadable:
                picked["unreadable"].append(f"{name}  {scene.unreadable}")
            elif settings.WHITE_CORRECTION and name not in calibration:
                picked["no_roi"].append(name)
            else:
                roi = tuple(int(v) for v in calibration[name]) if settings.WHITE_CORRECTION else None
                picked["targets"].append((scene, roi))
    return picked
