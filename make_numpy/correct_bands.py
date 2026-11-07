from hsi2rgb import white_board

import settings


def correct_bands(bands, roi, dark):
    """再構成バンド (29, H, W) に黒補正・白板補正をして、npy にする配列を返す。

    PNG から作るとき（make_bands）と、SDK から直接作るとき（reconstruct_to_npy）の共通部分。
    roi も dark も None（補正なし）なら、bands をそのまま返す（uint16 なら uint16 のまま）。
    """
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
