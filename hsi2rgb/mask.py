"""圧縮センシングのマスク（ランダムフィルター）に関する処理。

AG-HSV10M は画素ごとに透過スペクトルの違うマスク越しに1枚だけ撮る。
そのときセンサが受ける信号は、バンド画像 x とマスク透過率 M の内積になる。

    計測値 y(i,j) = alpha * sum_b M_b(i,j) * x_b(i,j) + 黒レベル      … 順モデル

この順モデルには2つの使い道がある。

  1. 対応付けの検証（forward_model_r2）
     ある RAW が本当にこの再構成結果の元データかを R^2 で確かめる。
     検証済みペアでは R^2 = 0.997、対応がズレていると 0.2 未満に落ちる。

  2. 白基準の計算（white_reference.py が使う）
     「センサが飽和するとき、バンド値はいくつか」を逆算する。
"""

import glob
import os

import cv2
import numpy as np

from .fileio import imread_unicode


# ---------------------------------------------------------------------------
# マスクの正規化定数（実測値）
# ---------------------------------------------------------------------------
# HSmaskdata の透過率PNGは 29 バンドすべてが同一平均に正規化されており、
# 絶対透過率が失われている。その正規化定数 alpha を、検証済みペアから
# 最小二乗で求めた:
#
#     計測値 = alpha * sum_b (M_b / 65535) * x_b + b
#     alpha = 0.156973,  R^2 = 0.9969,  切片 = 黒レベル 128（独立した検算）
#
# 別の6枚（撮影日・被写体が異なる）で alpha を独立に求め直しても
# 0.1570〜0.1615（±1.4%）に収まった。撮影内容には依存しない。
#
# ただし再構成の設定を変えると alpha は変わる。変えた場合は測り直すこと:
#   - マスク構成（レンズ・波長範囲・解像度モード）
#   - 白色板補正の有無 / 暗電流補正の有無
MASK_NORMALIZATION_ALPHA = 0.156973

# sum_b (M_b / 65535) の全画素平均。measure_mask_band_sum_mean() で測り直せる。
MASK_BAND_SUM_MEAN = 6.4245


def load_mask_bands(mask_dir, n_bands=29):
    """マスクの透過率PNG を (B, H, W) float32（0〜1 に正規化）で読む。"""
    bands = []
    for b in range(1, n_bands + 1):
        path = os.path.join(mask_dir, f"b{b}_transmittance.png")
        img = imread_unicode(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"透過率PNGを読み込めません: {path}")
        bands.append(img.astype(np.float32) / 65535.0)
    return np.array(bands, dtype=np.float32)


def measure_mask_band_sum_mean(mask_dir):
    """透過率PNG から sum_b (M_b/65535) の全画素平均を計算する。

    マスク構成を変えたときに MASK_BAND_SUM_MEAN を測り直すのに使う。
    """
    files = sorted(glob.glob(os.path.join(mask_dir, "b*_transmittance.png")))
    if not files:
        raise FileNotFoundError(f"透過率PNGが見つかりません: {mask_dir}")
    band_sum = None
    for path in files:
        img = imread_unicode(path, cv2.IMREAD_UNCHANGED).astype(np.float64) / 65535.0
        band_sum = img if band_sum is None else band_sum + img
    return float(band_sum.mean())


def forward_model_r2(bands, measurement, mask_bands):
    """順モデル 計測値 = a * sum_b(M_b * x_b) + b の当てはまり(R^2)を返す。

    この計測画像が本当にこの再構成結果の元データかを判定するために使う。
    a, b は最小二乗で求めるので、スケールや黒レベルの違いは吸収される。
    """
    predicted = np.einsum("bij,bij->ij", mask_bands, bands).ravel().astype(np.float64)
    observed = np.asarray(measurement, dtype=np.float64).ravel()
    design = np.vstack([predicted, np.ones_like(predicted)]).T
    coef, *_ = np.linalg.lstsq(design, observed, rcond=None)
    residual = observed - design @ coef
    return 1.0 - (residual @ residual) / ((observed - observed.mean()) ** 2).sum()


class MaskCache:
    """マスク29枚の読み込みは重いので、1度だけ読んで使い回す。"""

    def __init__(self, mask_dir, n_bands=29):
        self.mask_dir = mask_dir
        self.n_bands = n_bands
        self._bands = None

    def bands(self):
        if self._bands is None:
            print(f"  マスクを読み込み中: {self.mask_dir}")
            self._bands = load_mask_bands(self.mask_dir, self.n_bands)
        return self._bands
