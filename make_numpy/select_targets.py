from hsi2rgb import dataset

import settings


def select_targets(found, calibration):
    """再構成結果・白板ROI・既存の npy を見て、RAW 名を振り分ける。

    戻り値の辞書:
        targets   [(Scene, roi)]     npy を作るもの（白板補正なしなら roi は None）
        existing  [RAW名]            npy が既にあるもの
        missing   {Folder: [RAW名]}  再構成結果が無いもの
        no_roi    [RAW名]            白板ROIの記録が無いもの
    """
    picked = {"targets": [], "existing": [], "missing": {}, "no_roi": []}
    for folder, names in found.items():
        scenes = {}
        if dataset.detect_layout(folder.input_dir) is not None:
            for scene in dataset.discover_scenes(folder.input_dir, folder.raw_dir, verbose=False):
                scenes[scene.output_stem] = scene

        for name in names:
            # npy が既にあれば、再構成結果を消してあっても作成済みとして飛ばす
            if not settings.OVERWRITE and (settings.OUTPUT_DIR / f"{name}.npy").is_file():
                picked["existing"].append(name)
            elif name not in scenes:
                picked["missing"].setdefault(folder, []).append(name)
            elif settings.WHITE_CORRECTION and name not in calibration:
                picked["no_roi"].append(name)
            else:
                roi = tuple(int(v) for v in calibration[name]) if settings.WHITE_CORRECTION else None
                picked["targets"].append((scenes[name], roi))
    return picked
