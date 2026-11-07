import numpy as np

import settings


def npy_dtype():
    """npy の型。

    補正なしで NPY_UINT16 なら uint16。再構成の値は 16bit の整数なので、そのまま入り劣化しない（1 枚 約160MB）。
    黒補正・白板補正をすると小数・負の値になるので float32（1 枚 約320MB）。
    """
    if settings.NPY_UINT16 and not settings.BLACK_CORRECTION and not settings.WHITE_CORRECTION:
        return np.uint16
    return np.float32


def npy_mb():
    """npy 1 枚 (29, 1440, 1920) の容量の目安 [MB]。空き容量の確認に使う。"""
    return 161 if npy_dtype() == np.uint16 else 321
