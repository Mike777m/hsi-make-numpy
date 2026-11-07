import os

import numpy as np
from hsi2rgb.fs_retry import retry_io

import settings


def save_npy(array, name):
    """<OUTPUT_DIR>/<RAW名>.npy に保存する。

    書きかけの npy が残らないよう、一時ファイルに書いてから名前を変える。
    名前の変更が Box Drive の一時的なエラーになったときは、少し待ってやり直す。
    （書き込み自体が失敗したときは、make_npy_files が作り直しからやり直す）
    """
    final = settings.OUTPUT_DIR / f"{name}.npy"
    temp = settings.OUTPUT_DIR / f"{name}.npy.part"
    with temp.open("wb") as f:
        np.save(f, array)
    retry_io(os.replace, temp, final)
    return final
