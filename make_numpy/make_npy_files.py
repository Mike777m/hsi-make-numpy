import time

from hsi2rgb import white_board

import settings
from make_bands import make_bands
from save_npy import save_npy


def make_npy_files(targets, dark):
    """targets [(Scene, roi)] を1枚ずつ npy にする。戻り値: (作った RAW 名, 失敗した RAW 名と理由)"""
    made, failed = [], []
    for i, (scene, roi) in enumerate(targets, start=1):
        name = scene.output_stem
        print(f"[{i}/{len(targets)}] {name}  ({scene.reconstruct_dir})")
        for attempt in range(settings.RETRY_COUNT + 1):
            try:
                array = make_bands(scene, roi, dark)
                path = save_npy(array, name)
                print(f"  {path} 保存完了  shape={array.shape}")
                made.append(name)
                break
            except white_board.WhiteBoardNotUsable as e:  # やり直しても同じなので飛ばす
                print(f"  スキップ: {e}")
                failed.append(f"{name}  {e}")
                break
            except Exception as e:
                if attempt < settings.RETRY_COUNT:
                    print(f"  失敗、やり直します: {type(e).__name__}: {e}")
                    time.sleep(settings.RETRY_WAIT_SEC)
                else:
                    print(f"  スキップします: {type(e).__name__}: {e}")
                    failed.append(f"{name}  {type(e).__name__}: {e}")
    return made, failed
