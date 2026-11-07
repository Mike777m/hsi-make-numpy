"""AG-HSV10M の再構成バンド画像を RGB 画像に変換するための部品集。

パイプラインの流れは main.py を読むのが一番早い。
このパッケージは「1ステップぶんの処理」を提供するだけで、順番は持たない。

    camera.py           カメラ AG-HSV10M の仕様値（ビット深度・RAW構造・バンド構成）
    fileio.py           ファイルの読み書き（日本語パス対応）
    fs_retry.py         Box Drive などの一時的な読み書きエラーのやり直し
    dataset.py          データセット走査、再構成フォルダ <-> RAW の対応付け
    saturation.py       飽和画素の検出
    white_board.py      白板補正（ArUcoで白板を見つけ、反射率に変換）
    colorimetry.py      バンド -> XYZ -> 線形RGB（色の計算）
    white_reference.py  白基準（線形RGB の「これを255とみなす値」）
    mask.py             圧縮センシングのマスク、順モデルによる対応検証
    encode.py           正規化 -> ガンマ -> 量子化（見せる画像への落とし込み）
"""

from . import (
    camera,
    colorimetry,
    dataset,
    encode,
    fileio,
    fs_retry,
    mask,
    saturation,
    white_board,
    white_reference,
)

__all__ = [
    "camera",
    "colorimetry",
    "dataset",
    "encode",
    "fileio",
    "fs_retry",
    "mask",
    "saturation",
    "white_board",
    "white_reference",
]
