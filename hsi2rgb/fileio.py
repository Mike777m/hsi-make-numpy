"""ファイルの読み書き（日本語パス対応）。

このプロジェクトが扱うファイルは4種類。

    reconstruct/<TS>/band01..29.png  16bit PNG  再構成バンド画像（独自スケール）
    camera/compressed_<TS>.png       16bit PNG  計測画像（RAW から黒レベルを引いたもの）
    raw_data/*.RAW                   独自形式   カメラが出す元データ（0〜4095）
    HSmaskdata/.../b*_transmittance.png 16bit PNG マスクの透過率

読み込んだ計測画像は必ず「センサDN空間（0〜4095）」に揃えて返す。
どちらの経路で読んでも飽和判定のしきい値を共通にできるようにするため。
"""

import fnmatch
import json
import os

import cv2
import numpy as np

from .camera import (
    RAW_HEADER_BYTES,
    RAW_HEIGHT_FHD,
    RAW_WIDTH_FHD,
    SENSOR_BLACK_LEVEL,
)
from .fs_retry import retry_io


def imread_unicode(file_path, flags=cv2.IMREAD_GRAYSCALE):
    """日本語パス対応の画像読み込み（cv2.imread の代替）。"""
    data = np.fromfile(file_path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite_unicode(file_path, image):
    """日本語パス対応の画像保存（cv2.imwrite の代替）。"""
    ext = os.path.splitext(file_path)[1]
    ok, buf = cv2.imencode(ext, image)
    if not ok:
        return False
    buf.tofile(file_path)
    return True


def load_band_images(folder_path, dtype=np.float32):
    """再構成フォルダの band*.png を読み、(B, H, W) の dtype（既定は float32）で返す。

    dtype=np.uint16 なら PNG の値をそのまま返す（make_numpy で uint16 の npy を作るとき）。
    保存形式は16bit PNG だが、値は 0〜65535 でも 0〜4095 でもなく、
    再構成ソフトが出す独自スケール（実測で13000超）。センサの12bitとは別物。
    ファイル名の band01..band29 の順（辞書順）がそのままバンド順になる。

    フォルダの一覧には glob ではなく os.listdir を使う。glob は一覧の途中で読み取りエラーが
    起きても黙ってそこまでの分を返すので、Box Drive 上ではバンドが欠けたまま進むおそれがある。
    os.listdir は全部読めるかエラーかのどちらかになる（読むファイルと順番は glob と同じ）。
    """
    try:
        names = retry_io(os.listdir, folder_path)
    except (FileNotFoundError, NotADirectoryError):
        names = []
    file_list = sorted(
        os.path.join(folder_path, name) for name in names if fnmatch.fnmatch(name, "band*.png")
    )
    if not file_list:
        raise FileNotFoundError(f"バンドPNGが見つかりません: {folder_path}")

    images = []
    for file_path in file_list:
        img = retry_io(imread_unicode, file_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"画像を読み込めません: {file_path}")
        if img.ndim != 2:
            raise ValueError(f"バンド画像は2次元である必要があります: {file_path}")
        if img.dtype != np.uint16:
            raise TypeError(f"16bit PNGが必要です (got {img.dtype}): {file_path}")
        images.append(img)

    return np.array(images, dtype=dtype)


def load_compressed_measurement(file_path):
    """camera/compressed_*.png を読み、センサDN空間(0〜4095)に戻して返す。

    ファイル自体は黒レベル 128 が引かれた状態なので、足し戻す。
    こうしておけば RAW と同じ「4095で飽和」の判定が使える。
    """
    img = imread_unicode(file_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"計測画像を読み込めません: {file_path}")
    if img.ndim != 2:
        raise ValueError(f"計測画像は2次元である必要があります: {file_path}")
    if img.dtype != np.uint16:
        raise TypeError(f"16bit PNGが必要です (got {img.dtype}): {file_path}")
    return img.astype(np.int32) + SENSOR_BLACK_LEVEL


def load_raw_measurement(file_path, width=RAW_WIDTH_FHD, height=RAW_HEIGHT_FHD):
    """AG-HSV10M の RAW を (H, W) uint16（センサDN 0〜4095）として読む。"""
    data = np.fromfile(file_path, dtype=np.uint8)
    need = RAW_HEADER_BYTES + width * height * 2
    if data.size < need:
        raise ValueError(
            f"RAWが小さすぎます: {data.size} bytes "
            f"(最低 {need} = ヘッダ{RAW_HEADER_BYTES} + {width}x{height}x2 が必要): "
            f"{file_path}"
        )
    body = data[RAW_HEADER_BYTES:need].tobytes()
    return np.frombuffer(body, dtype="<u2").reshape(height, width)


def save_rgb_png(image_rgb, save_dir, stem):
    """RGB 画像（uint8/uint16）を PNG として保存する。cv2 は BGR 順なので変換する。"""
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"{stem}.png")
    if not imwrite_unicode(save_path, cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)):
        raise RuntimeError(f"RGB画像を保存できません: {save_path}")
    return save_path


def save_bands_numpy(bands, save_dir, stem):
    """再構成バンド配列を .npy で保存する（1枚あたり約320MB）。"""
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, stem)
    np.save(save_path, bands)
    return save_path + ".npy"


# ---------------------------------------------------------------------------
# 白板ROI（bbox）の記録
# ---------------------------------------------------------------------------
def load_calibration_json(file_path):
    """白板ROI(bbox)の記録を読む。ファイルが無ければ空の辞書を返す。

    形式は {"00800693": [x0, y0, x1, y1], ...}。
    SKIP_EXISTING で再開したときに、前回ぶんの記録を消さないために使う。
    """
    if not os.path.isfile(file_path):
        return {}
    with open(file_path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, dict):
        raise ValueError(f"白板ROI記録は辞書である必要があります: {file_path}")
    return records


def save_calibration_json(file_path, records):
    """白板ROI(bbox)の記録を、1行1シーンの形で書き出す。

        {
          "00800693": [420, 713, 763, 897],
          "00800694": [421, 714, 764, 898]
        }

    json.dump(indent=2) だと4要素が4行に散って読みにくいので自前で整形する。
    キー順に並べるので、実行のたびに差分が暴れない。
    """
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    lines = [
        f'  "{stem}": {json.dumps([int(v) for v in roi])}'
        for stem, roi in sorted(records.items())
    ]
    if lines:
        text = "{\n" + ",\n".join(lines) + "\n}\n"
    else:
        text = "{}\n"
    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return file_path
