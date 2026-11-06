from hsi2rgb import fileio, white_board

import settings


def make_bands(scene, roi, dark):
    """再構成バンド 29 枚を読み、黒補正・白板補正をして、npy にする配列 (29, H, W) を返す。"""
    bands = fileio.load_band_images(str(scene.reconstruct_dir))
    bands = white_board.subtract_dark(bands, None if dark is None else dark.bands())
    if roi is None:
        return bands

    x0, y0, x1, y1 = roi
    height, width = bands.shape[1:]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise white_board.WhiteBoardNotUsable(f"白板ROI {list(roi)} が画像 {width}x{height} の外に出ています")
    print(f"  白板ROI: x {x0}-{x1}, y {y0}-{y1}")

    white_spectrum = white_board.roi_band_means(bands, roi)
    print(f"  白板スペクトル: {white_board.format_spectrum(white_spectrum)}")
    return white_board.to_reflectance(bands, white_spectrum, settings.WHITE_BOARD_REFLECTANCE)
