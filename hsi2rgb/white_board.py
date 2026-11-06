"""白板補正：ArUcoマーカーで白板を見つけ、バンドごとに反射率へ変換する。

白板は撮影のたびに画面内に写っているので、1枚ごとにその画像自身の白板で割る。
得られる値は「白板を 1.0 としたときの反射率」になり、照明スペクトルと露光量が
除去される。

    バンド画像 x_b(i,j)                    ← 照明スペクトルが乗ったまま
        |  白板ROI内の平均 w_b（29個）を求める
        v
    反射率 r_b(i,j) = x_b(i,j) / w_b       ← バンドごとに別々の数で割る
                                              白板の場所は全バンドで 1.0 になる

白板の位置は、白板の下に貼った ArUco マーカー2つ（ID 0, 1）から推定する。
推定のロジックは aruco-main/white_board_roi.py にあり、このモジュールは
それを読み込んで使うだけ（計算式の二重管理を避けるため）。

白板が使えないシーンは WhiteBoardNotUsable を投げてスキップする。
中途半端に補正なしの画像を出すと、あとから区別がつかなくなるため。
"""

import importlib.util
import os

import cv2
import numpy as np

from . import colorimetry, encode


class WhiteBoardNotUsable(Exception):
    """白板が使えないのでこのシーンはスキップする、という合図。

    例外メッセージがそのままスキップ理由としてログに出る。
    """


# ---------------------------------------------------------------------------
# ArUco 検出（aruco-main の white_board_roi.py を読み込んで使う）
# ---------------------------------------------------------------------------
class WhiteBoardDetector:
    """aruco-main/white_board_roi.py を読み込み、白板ROIの検出を担当する。"""

    def __init__(self, aruco_dir, roi_params=None, detector_params=None):
        """
        Args:
            aruco_dir:  white_board_roi.py があるフォルダ（aruco-main）
            roi_params: find_board に渡す上書きパラメータ。
                        {"gap":1.6, "height":2.2, "margin":-0.3, "shift":0.10}
                        空なら aruco-main 側の既定値を使う。
            detector_params: OpenCV の ArUco 検出パラメータの上書き。
                        {"adaptiveThreshWinSizeMax": 45} など。
                        空なら OpenCV の既定値。
        """
        self.aruco_dir = str(aruco_dir)
        self.roi_params = dict(roi_params or {})
        self.detector_params = dict(detector_params or {}) 
        self._module = None

    def _load(self):
        """white_board_roi.py をファイルパス指定で読み込む（初回のみ）。"""
        if self._module is None:
            path = os.path.join(self.aruco_dir, "white_board_roi.py")
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"white_board_roi.py が見つかりません: {path}\n"
                    "config.ARUCO_DIR を確認してください。"
                )
            spec = importlib.util.spec_from_file_location("white_board_roi", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._apply_detector_params(module)
            self._module = module
        return self._module

    def _apply_detector_params(self, module):
        """読み込んだ検出器の ArUco パラメータを上書きする。

        aruco-main 側のファイルは書き換えず、モジュールが持っている
        ArucoDetector の設定だけを差し替える。
        """
        if not self.detector_params:
            return
        params = module._detector.getDetectorParameters()
        for name, value in self.detector_params.items():
            if not hasattr(params, name):
                raise ValueError(
                    f"ArUco検出パラメータにこの名前はありません: {name}"
                )
            setattr(params, name, value)
        module._detector.setDetectorParameters(params)

    def annotate_markers(self, preview_rgb, caption=None, min_candidate_side=40):
        """検出の様子を描き込んだ仮RGBの複製を返す（失敗原因の確認用）。

        緑 : ID まで読めたマーカー（左上に ID を表示）
        青 : 四角形としては拾えたが ID を読めなかった候補
              -> 枠は見えているがパターンが汚れている、という意味になる
              -> 逆に、読めなかったマーカーの場所に青い枠すら無ければ、
                 枠が途切れていて四角形として拾えていない、と分かる

        Args:
            caption: 画像の上に重ねる説明。OpenCV は日本語を描けないので ASCII のみ。
            min_candidate_side: この画素数より小さい候補は描かない。草や地面の模様が
                数十個の小さな候補として拾われ、画面が埋まって読めなくなるため。
                実測ではマーカーの1辺は 75〜100 px だった。

        Returns:
            (描き込んだ画像, 読めたIDのリスト, 描いた候補の数)
        """
        module = self._load()
        gray = module.to_gray(preview_rgb)
        corners, ids, rejected = module._detector.detectMarkers(gray)

        def side_of(quad):
            p = quad[0]
            return float(np.mean([np.linalg.norm(p[k] - p[(k + 1) % 4]) for k in range(4)]))

        big = [q for q in rejected if side_of(q) >= min_candidate_side]

        marked = np.ascontiguousarray(preview_rgb.copy())
        if big:
            cv2.aruco.drawDetectedMarkers(marked, big, None, (0, 0, 255))
        if ids is not None:
            cv2.aruco.drawDetectedMarkers(marked, corners, ids, (0, 255, 0))
        found = sorted(int(v) for v in ids.flatten()) if ids is not None else []

        lines = [
            f"found={found}  unreadable candidates={len(big)}"
            f" (of {len(rejected)}, >={min_candidate_side}px only)"
        ]
        if caption:
            lines.insert(0, caption)
        for row, text in enumerate(lines):
            y = 40 + row * 38
            for color, thickness in (((0, 0, 0), 6), ((255, 255, 0), 2)):
                cv2.putText(marked, text, (16, y), cv2.FONT_HERSHEY_SIMPLEX,
                            1.1, color, thickness, cv2.LINE_AA)
        return marked, found, len(big)

    def find_roi(self, preview_rgb):
        """仮RGB画像から白板のROI (x0, y0, x1, y1) を返す。

        マーカーが見つからない／ROIが潰れている場合は WhiteBoardNotUsable。
        """
        module = self._load()
        gray = module.to_gray(preview_rgb)
        try:
            roi = module.find_board(gray, **self.roi_params)
        except module.MarkerNotFound as e:
            raise WhiteBoardNotUsable(f"ArUcoマーカー未検出 ({e})") from e

        x0, y0, x1, y1 = roi
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise WhiteBoardNotUsable(
                f"白板ROIが潰れています (x:{x0}-{x1}, y:{y0}-{y1})。"
                "マーカーが画面端にありませんか。"
            )
        return roi


# ---------------------------------------------------------------------------
# 仮RGB（ArUco を認識させるためだけの画像）
# ---------------------------------------------------------------------------
def make_preview_rgb(bands, percentile=99.9, gamma=2.2):
    """ArUco検出用の仮RGB画像 (H, W, 3) uint8 を作る。

    白基準はまだ決まっていないので、この画像の 99.9 パーセンタイルで
    露出を合わせる（＝画像ごとの自動露出）。マーカーの白黒のコントラストが
    出れば十分なので、色の正確さは問わない。
    """
    linear_rgb = colorimetry.bands_to_linear_rgb(bands)
    level = encode.linear_rgb_percentile(linear_rgb, percentile)
    if level <= 0:
        level = encode.linear_rgb_max(linear_rgb)
    if level <= 0:
        raise WhiteBoardNotUsable("画像が真っ黒で仮RGBを作れません")
    return encode.encode_display_rgb(linear_rgb, level, gamma, 8)


# ---------------------------------------------------------------------------
# 白板スペクトルと反射率への変換
# ---------------------------------------------------------------------------
def fallback_roi(setting, stem, image_shape, reason):
    """ROIの自動検出に失敗したとき、設定された固定ROIで代替する。

    ArUcoマーカーが写っておらず、白板が背景（床の反射・明るい地面など）と
    つながっていて二値化でも切り出せない場合に使う。三脚固定の撮影なら
    1度ROIを測っておけば、そのデータセット全体に使える。

    Args:
        setting: config.WHITE_BOARD_FALLBACK_ROI。
            (x0, y0, x1, y1)          … 全シーン共通
            {"00820616": (...), ...}  … シーンごと（キーは出力ファイル名）
            None                      … 代替しない
        stem: 出力ファイル名（辞書のキー）
        image_shape: (H, W)。範囲チェックに使う。
        reason: 自動検出が投げた例外。代替できないときはこれを投げ直す。
    """
    if setting is None:
        raise reason
    roi = setting.get(stem) if isinstance(setting, dict) else setting
    if roi is None:
        raise WhiteBoardNotUsable(
            f"{reason}（固定ROIにも {stem} の指定がありません）"
        )

    x0, y0, x1, y1 = (int(v) for v in roi)
    height, width = image_shape
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise WhiteBoardNotUsable(
            f"固定ROIが画像の範囲外です: {(x0, y0, x1, y1)}（画像 {width}x{height}）"
        )
    print(f"  自動検出に失敗したので固定ROIを使います（{reason}）")
    return (x0, y0, x1, y1)


def check_roi_saturation(saturation_mask, roi, max_saturated_px=0):
    """白板ROI内に飽和画素があればスキップする。

    白板は画面内で最も明るいので、露出次第で真っ先に飽和する。飽和した画素を
    平均に含めると白板が実際より暗く見積もられ、補正後の画像全体が明るく
    なりすぎる。

    saturation_mask が None（計測画像が無い）の場合は確認できないので、
    警告だけ出して先に進む。
    """
    if saturation_mask is None:
        print("  警告: 計測画像が無いため、白板ROIの飽和を確認できません。")
        return 0

    x0, y0, x1, y1 = roi
    n_saturated = int(np.count_nonzero(saturation_mask[y0:y1, x0:x1]))
    if n_saturated > max_saturated_px:
        area = max((y1 - y0) * (x1 - x0), 1)
        raise WhiteBoardNotUsable(
            f"白板ROI内に飽和画素 {n_saturated:,} px "
            f"({100.0 * n_saturated / area:.2f}%、許容 {max_saturated_px} px)"
        )
    return n_saturated


def roi_band_means(bands, roi):
    """白板ROI内のバンド別平均 (B,) を返す。これが白板スペクトル。

    band01 の平均、band02 の平均、… と29個の数字が並ぶ。
    """
    x0, y0, x1, y1 = roi
    spectrum = bands[:, y0:y1, x0:x1].mean(axis=(1, 2)).astype(np.float64)

    if not np.all(np.isfinite(spectrum)):
        raise WhiteBoardNotUsable("白板スペクトルに NaN / inf が含まれています")
    if spectrum.min() <= 0:
        bad = int(np.argmin(spectrum)) + 1
        raise WhiteBoardNotUsable(
            f"白板スペクトルに 0 以下の値があります (band{bad:02d} = {spectrum.min():.1f})"
        )
    return spectrum


def subtract_dark(bands, dark_bands):
    """【黒補正】各画素・各バンドからダーク画像を引く。

    ダークはレンズに蓋をして撮り、同じ設定で29バンドに再構成したもの。
    暗電流や再構成のオフセットなど「光が無くても出ている分」を取り除く。

        corrected[b](i,j) = bands[b](i,j) - dark_bands[b](i,j)

    dark_bands が None なら何もせず元の配列をそのまま返す。
    暗い場所ではノイズで負値になりうるが、後段の正規化で 0 にクリップされる。
    """
    if dark_bands is None:
        return bands
    dark_bands = np.asarray(dark_bands, dtype=np.float32)
    if dark_bands.shape != bands.shape:
        raise ValueError(
            f"ダーク画像の形が合いません: {dark_bands.shape} != {bands.shape}"
        )
    return (bands - dark_bands).astype(np.float32)


def to_reflectance(bands, white_spectrum, white_board_reflectance=1.0):
    """【白板補正】バンドごとに白板の平均で割り、反射率 (B, H, W) にする。

    band01 の画像全体を band01 の白板平均で、band02 の画像全体を band02 の
    白板平均で……と、29回の独立した割り算を行う。
    バンドをまたいで値が混ざることはない。

        reflectance[b] = white_board_reflectance * bands[b] / white_spectrum[b]

    bands と white_spectrum の両方が黒補正済み（ダークを引いた後）であること。
    全体の式は次のようになる。

        r_b(i,j) = rho * (x_b(i,j) - d_b(i,j)) / mean_ROI(x_b - d_b)

    白板の場所は全バンドで白板の反射率 rho そのものになり（反射率90%の板なら
    0.9）、それより暗い物体はさらに小さく、白板より明るい鏡面反射などは
    rho を超える。

    Args:
        white_board_reflectance: 使った白板の反射率。標準白色板なら 0.99 など、
            90%の板なら 0.9。1.0 だと「白板を1.0とした相対値」になる。
    """
    white_spectrum = np.asarray(white_spectrum, dtype=np.float64)
    if white_spectrum.shape != (bands.shape[0],):
        raise ValueError(
            f"白板スペクトルのバンド数が合いません: "
            f"{white_spectrum.shape} != ({bands.shape[0]},)"
        )
    if not 0 < white_board_reflectance <= 1:
        raise ValueError(
            f"白板の反射率は 0 より大きく 1 以下です: {white_board_reflectance}"
        )
    # (B, H, W) / (B, 1, 1) -> バンド軸にブロードキャストされる
    reflectance = bands / white_spectrum[:, None, None]
    return (white_board_reflectance * reflectance).astype(np.float32)


class DarkFrame:
    """ダーク画像29枚は毎回読むと重いので、1度だけ読んで使い回す。"""

    def __init__(self, dark_dir):
        self.dark_dir = str(dark_dir)
        self._bands = None

    def bands(self):
        from .fileio import load_band_images

        if self._bands is None:
            print(f"  ダーク画像を読み込み中: {self.dark_dir}")
            self._bands = load_band_images(self.dark_dir)
        return self._bands


def format_spectrum(white_spectrum, n_show=4):
    """ログ用に白板スペクトルを短く整形する。"""
    values = np.asarray(white_spectrum, dtype=np.float64)
    head = " ".join(f"{v:.0f}" for v in values[:n_show])
    return (f"band01..{n_show:02d}=[{head} ...] "
            f"min={values.min():.0f} max={values.max():.0f}")
