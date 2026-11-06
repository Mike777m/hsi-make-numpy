import os

import numpy as np

import settings


def save_npy(array, name):
    """<OUTPUT_DIR>/<RAW名>.npy に保存する。

    書きかけの npy が残らないよう、一時ファイルに書いてから名前を変える。
    """
    final = settings.OUTPUT_DIR / f"{name}.npy"
    temp = settings.OUTPUT_DIR / f"{name}.npy.part"
    with temp.open("wb") as f:
        np.save(f, array)
    os.replace(temp, final)
    return final
