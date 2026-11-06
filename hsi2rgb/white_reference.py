"""白基準：線形RGB の「これを 255 とみなす値」を決める。

再構成バンド画像には「理論上の最大値」が無い（再構成ソフトの独自スケール）。
そこでセンサ側から逆算する。

    センサが飽和する = 計測値が フルスケール - 黒レベル = 4095 - 128
    順モデル          計測値 = alpha * sum_b M_b * x_b
      -> 飽和するときのバンド値の総和 total が決まる
      -> total を白のスペクトル形で各バンドに配分し、線形RGBに変換したものが白基準

戻り値を R,G,B の3要素のまま扱うのが重要。チャンネルごとにこの値で割ることで
初めて「白い被写体が R=G=B になる」＝ホワイトバランスが成立する。
最大チャンネルだけを取って共通のスカラーで割ると、白基準そのものの色
（実測の白色板で 0.880 : 1.000 : 0.756）がそのまま出力に残ってしまう。
"""

import numpy as np

from .camera import SENSOR_BIT_DEPTH, SENSOR_BLACK_LEVEL, sensor_full_scale
from .colorimetry import band_to_linear_rgb_weights
from .mask import MASK_BAND_SUM_MEAN, MASK_NORMALIZATION_ALPHA


def white_reference_linear_rgb(
    white_spectrum=None,
    alpha=MASK_NORMALIZATION_ALPHA,
    mask_band_sum_mean=MASK_BAND_SUM_MEAN,
    black_level=SENSOR_BLACK_LEVEL,
    bit_depth=SENSOR_BIT_DEPTH,
    cmf=None,
):
    """白がセンサを飽和させるときの線形RGB を R,G,B の3要素で返す。

    撮影内容には依存せず、センサ仕様・マスク構成・等色関数だけで決まる。
    だから何枚追加しても明るさが揃い続ける。

    Args:
        white_spectrum: 白色板を再構成したスペクトル（バンド数ぶんの1次元配列）。
            スケールは無視され「形」だけが使われるので、白色板の露光量や
            再構成スケールが違っても問題ない。
            None ならフラット白（全バンド等強度）を仮定する。
        alpha: マスクの正規化定数。再構成設定ごとに測り直す。
        mask_band_sum_mean: sum_b (M_b/65535) の全画素平均。
        black_level: 黒レベル。飽和信号 = フルスケール - 黒レベル。
    """
    weights = band_to_linear_rgb_weights(cmf)      # (B, 3)
    n_bands = weights.shape[0]

    if white_spectrum is None:
        shape = np.full(n_bands, 1.0 / n_bands)
    else:
        shape = np.asarray(white_spectrum, dtype=np.float64).ravel()
        if shape.size != n_bands:
            raise ValueError(
                f"白色板スペクトルのバンド数が合いません: {shape.size} != {n_bands}"
            )
        if not np.all(np.isfinite(shape)) or shape.min() < 0 or shape.sum() <= 0:
            raise ValueError("白色板スペクトルが不正です（負値・NaN・総和0）")
        shape = shape / shape.sum()

    if alpha <= 0:
        raise ValueError(f"alpha は正の値である必要があります: {alpha}")

    per_band_mask_mean = mask_band_sum_mean / n_bands
    signal_at_saturation = sensor_full_scale(bit_depth) - black_level
    total = signal_at_saturation / (alpha * per_band_mask_mean)
    return total * (shape[:, None] * weights).sum(axis=0)


# フラット白を仮定した既定値（白色板が無いときに使う）。
# 注意: 再構成後のスペクトルは白色板を撮っても平坦にならない（実測で
# 最小/最大 = 0.197）。フラット白はあくまで当て推量なので、
# 実測の白色板を使えるならそちらのほうが正しい。
FLAT_WHITE_LINEAR_RGB = white_reference_linear_rgb()


def reflectance_white_linear_rgb(full_scale=1.0, cmf=None):
    """反射率 full_scale の線形RGB を R,G,B の3要素で返す。

    白板補正（white_board.to_reflectance）を通したデータ専用。
    バンド値がすでに「白板を1.0とした反射率」になっているので、
    全バンドが full_scale のときの線形RGB がそのまま白基準になる。

    センサ飽和から逆算する white_reference_linear_rgb() と違い、
    マスクの正規化定数 alpha も黒レベルも要らない。実測の白板が
    基準そのものなので、推定が1段少なく精度も高い。

    Args:
        full_scale: 出力の最大値（255 / 65535）に対応させる反射率。
            1.0 だと白板ぴったりが上限になるが、白板より明るい面
            （日向の地面など）が白飛びする。実測では反射率が 1.0 を
            超える画素が 36.8% あり、2.0 まで許すと 0.18% に減った。
    """
    if full_scale <= 0:
        raise ValueError(f"full_scale は正の値である必要があります: {full_scale}")
    return full_scale * band_to_linear_rgb_weights(cmf).sum(axis=0)


def white_balance_unit(scale):
    """スケールから、色かぶりだけを打ち消す単位ベクトル (3,) を取り出す。

    3要素なら最大チャンネルで割った比、スカラーなら (1,1,1)。
    明るさは呼び出し側が別に決めるので、ここでは色の比だけを返す。
    """
    arr = np.asarray(scale, dtype=np.float32)
    if arr.ndim == 0:
        return np.ones(3, dtype=np.float32)
    return arr / arr.max()
