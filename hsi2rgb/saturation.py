"""飽和画素の検出。

飽和はセンサで起きる現象なので、判定は必ず再構成「前」の計測画像で行う。
再構成後のバンド画像では飽和の情報が29バンドに拡散していて復元できない。
（バンド値が大きいこと自体は飽和を意味しない。単に明るいだけかもしれない。）
"""

import numpy as np

from .camera import SENSOR_BIT_DEPTH, sensor_full_scale


def saturation_mask(measurement, bit_depth=SENSOR_BIT_DEPTH):
    """計測画像（センサDN空間）から飽和画素マスク (H, W) bool を作る。

    fileio の load_raw_measurement / load_compressed_measurement は
    どちらもセンサDN空間（0〜4095）で返すので、しきい値は共通で良い。
    """
    measurement = np.asarray(measurement)
    if measurement.ndim != 2:
        raise ValueError(
            f"計測画像は2次元である必要があります: shape={measurement.shape}"
        )
    full_scale = sensor_full_scale(bit_depth)
    if measurement.max() > full_scale:
        raise ValueError(
            f"計測画像の最大値 {measurement.max()} が {bit_depth}bit の"
            f"フルスケール {int(full_scale)} を超えています。"
            "再構成後のバンド画像を渡していませんか。"
        )
    return measurement >= full_scale


def mark_saturated_red(rgb_image, mask, peak):
    """飽和画素を純赤 (peak, 0, 0) で塗った複製を返す。元画像は変更しない。"""
    marked = rgb_image.copy()
    if mask is not None:
        marked[mask] = (peak, 0, 0)
    return marked
