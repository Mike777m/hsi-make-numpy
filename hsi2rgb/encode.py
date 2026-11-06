"""線形RGB を「見せる画像」に落とす段階。

    線形RGB (H, W, 3) float  … 放射輝度に比例した生の値。上限は決まっていない
        |  normalize_linear_rgb(scale)   … scale を 1.0 とみなして 0〜1 にする
        v
    正規化RGB (H, W, 3) float 0〜1
        |  apply_gamma(gamma)            … 表示用の非線形変換（1.0 なら何もしない）
        v
    ガンマ後RGB (H, W, 3) float 0〜1
        |  quantize(bit_depth)           … 0〜255 / 0〜65535 の整数にする
        v
    出力画像 (H, W, 3) uint8 / uint16
"""

import numpy as np


def output_peak(bit_depth=8):
    """出力画像の最大値（8bit なら 255、16bit なら 65535）。"""
    if bit_depth not in (8, 16):
        raise ValueError(f"bit_depth は 8 か 16 です: {bit_depth}")
    return (1 << bit_depth) - 1


def linear_rgb_max(linear_rgb):
    """その画像の線形RGB（負値は0）の最大値。"""
    return float(np.max(np.clip(linear_rgb, 0, None)))


def linear_rgb_percentile(linear_rgb, percentile=100.0):
    """その画像の線形RGB（負値は0）の指定パーセンタイル値。

    100 なら最大値そのもの。ただし鏡面反射などで数画素だけ極端に明るいと、
    その1画素に引きずられて画像全体が暗くなる（実測で最大値がp99の3.3倍の例あり）。
    99.9 などにすると上位0.1%は白飛びする代わりに、全体が適正な明るさになる。
    """
    if not 0 < percentile <= 100:
        raise ValueError(f"percentile は 0 より大きく 100 以下: {percentile}")
    values = np.clip(linear_rgb, 0, None)
    if percentile >= 100:
        return float(np.max(values))
    return float(np.percentile(values, percentile))


def normalize_linear_rgb(linear_rgb, scale):
    """【正規化】線形RGB を scale で割って 0〜1 にクリップする。

    scale はスカラー（明るさだけを合わせる）でも R,G,B の3要素でも良い。
    3要素を渡すとチャンネルごとに割られるので、白基準を渡せばそのまま
    ホワイトバランスになる。
    """
    scale = np.asarray(scale, dtype=np.float32)
    if scale.ndim > 1 or (scale.ndim == 1 and scale.size != 3):
        raise ValueError(f"scale はスカラーか3要素です: shape={scale.shape}")
    if not np.all(scale > 0):
        raise ValueError(f"scale は正の値である必要があります: {scale}")
    normalized = np.clip(linear_rgb, 0, None) / scale
    return np.clip(normalized, 0, 1)


def apply_gamma(normalized_rgb, gamma):
    """【ガンマ】0〜1 の値に x^(1/gamma) をかける。gamma=1.0 なら素通し。

    gamma=1.0 のまま保存すると画素値が放射輝度に正比例するので解析向きだが、
    画面上はかなり暗く見える。見せるためには 2.2 前後をかける。
    """
    if gamma <= 0:
        raise ValueError(f"gamma は正の値である必要があります: {gamma}")
    if gamma == 1.0:
        return normalized_rgb
    return np.power(normalized_rgb, 1.0 / gamma)


def quantize(normalized_rgb, bit_depth=8):
    """【量子化】0〜1 の float を uint8 / uint16 にする。

    切り捨てだと系統的に約0.5LSB暗くなるので四捨五入する。
    """
    peak = output_peak(bit_depth)
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    return np.rint(normalized_rgb * peak).astype(dtype)


def encode_display_rgb(linear_rgb, scale, gamma, bit_depth=8):
    """正規化 -> ガンマ -> 量子化 をまとめて実行する近道。"""
    normalized = normalize_linear_rgb(linear_rgb, scale)
    return quantize(apply_gamma(normalized, gamma), bit_depth)
