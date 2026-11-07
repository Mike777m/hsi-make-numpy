from hsi2rgb import fileio

from correct_bands import correct_bands
from npy_dtype import npy_dtype


def make_bands(scene, roi, dark):
    """再構成バンド 29 枚（PNG）を読み、黒補正・白板補正をして、npy にする配列 (29, H, W) を返す。

    補正なしで uint16 保存なら、PNG の値を uint16 のまま返す（float32 を経由しない）。
    """
    bands = fileio.load_band_images(str(scene.reconstruct_dir), dtype=npy_dtype())
    return correct_bands(bands, roi, dark)
