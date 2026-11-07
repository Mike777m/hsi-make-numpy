# -*- coding: utf-8 -*-
"""
Iromiel Hyper SDK を使った「撮影済み RAW 画像の一括復元・保存」スクリプト

アプリでの操作
  [ファイル接続] → キャリブレーションデータ選択 → 画像ファイル指定
  → [Reconstruct] → [Record] → [View] → 保存されたら [Stop]
を、入力フォルダ内の全ファイルに対して 1 枚ずつ自動で繰り返します。

出力
  <出力フォルダ>/<RAW名>/reconstruct/<RAW名>/band01_<RAW名>.png ...   復元画像 (png)
  <出力フォルダ>/<RAW名>/reconstruct/<RAW名>/bands_<RAW名>.hdr/.raw  復元画像 (ENVI)
  <出力フォルダ>/<RAW名>/log/log_<RAW名>.csv                          SDK が書く記録情報
  <出力フォルダ>/batch_log.csv                                        このスクリプトの処理結果一覧

  SDK は名前に「再構成日時」を付けるが、保存が終わったら RAW 名に付け替える
  (rename_to_raw_name)。元の再構成日時は batch_log.csv に残す。

使い方
  1. 同じフォルダの config.toml に、入力フォルダ・出力フォルダなどを書く
  2. python batch_reconstruct.py   (引数なしで実行)

  別の設定ファイルを使う場合:      python batch_reconstruct.py --config other.toml
  対象ファイルの一覧だけ見る場合:  python batch_reconstruct.py --dry-run
"""

import argparse
import csv
import ctypes
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

try:
    import tomllib  # Python 3.11 以降に標準で入っている
except ModuleNotFoundError:
    sys.exit("Python 3.11 以降が必要です (config.toml の読み込みに tomllib を使います)")

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.toml"

REC_PARAM_KEYS = ["max_iter", "converge_threshold", "tau_1", "tau_2", "tau_turning_point",
                  "max_eigen", "min_eigen", "EM", "TV_denoise_threshold"]
CAM_PARAM_KEYS = ["gain", "gamma", "shutter", "num_dark", "framerate"]

# PNG ファイルの最後の 8 バイト (IEND チャンク)。これがあれば最後まで書き終わっている
PNG_IEND = b"IEND\xaeB`\x82"

# ENVI ヘッダの data type → 1 画素 1 バンドあたりのバイト数
ENVI_TYPE_BYTES = {1: 1, 2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 9: 16, 12: 2, 13: 4, 14: 8, 15: 8}

# 単発撮影モードで、結果ファイルの変化がこの秒数止まったら完了とみなす
# (ファイル名が想定と違って通常の完了判定ができない場合の保険)
ONESHOT_IDLE = 3.0

# ============================================================================
# SDK の型定義 (Library/release/HSProc_C.h と同じ並び)
# ============================================================================

CAMTYPE_BHSVIS = 0
CAMTYPE_DUMMY = 1       # = アプリの「ファイル接続」

CAPMODE_CONTINUOUS = 0
CAPMODE_ONE_SHOT = 1

STATUS_RUN = 3
STATUS_NAMES = {0: "NULL", 1: "READY", 2: "STOP", 3: "RUN"}


class HSPROC_CAM_PARAM(ctypes.Structure):
    _fields_ = [
        ("gain", ctypes.c_double),
        ("gamma", ctypes.c_double),
        ("shutter", ctypes.c_double),
        ("num_dark", ctypes.c_int),
        ("framerate", ctypes.c_double),
    ]


class HSPROC_REC_PARAM(ctypes.Structure):
    _fields_ = [
        ("max_iter", ctypes.c_int),
        ("converge_threshold", ctypes.c_float),
        ("tau_1", ctypes.c_float),
        ("tau_2", ctypes.c_float),
        ("tau_turning_point", ctypes.c_int),
        ("max_eigen", ctypes.c_float),
        ("min_eigen", ctypes.c_float),
        ("EM", ctypes.c_float),
        ("TV_denoise_threshold", ctypes.c_float),
    ]


class Rect(ctypes.Structure):
    """領域 (左上の座標と幅・高さ)。HSProc_GetWhiteWithRoi に値のまま渡す。"""
    _fields_ = [
        ("left", ctypes.c_int),
        ("up", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
    ]


class T_PROCEDURE_IMAGE(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("bitdepth", ctypes.c_int),
    ]


class HSPROC_SAVE_FILELIST(ctypes.Structure):
    _fields_ = [
        ("filenames", ctypes.POINTER(ctypes.c_char_p)),
        ("count", ctypes.c_size_t),
    ]


IMAGES_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.POINTER(T_PROCEDURE_IMAGE),
    ctypes.POINTER(HSPROC_SAVE_FILELIST),
)


class SdkError(RuntimeError):
    pass


# SDK の実行に必要な DLL (仕様書 表 1-2)
REQUIRED_DLLS = ["HSProc.dll", "Procedure.dll", "Lmxptpif.dll", "IMAGECAPTURELIB.dll",
                 "opencv_core440.dll", "opencv_imgcodecs440.dll", "opencv_imgproc440.dll"]


def load_dll(dll_dir: Path):
    """HSProc.dll を読み込む。足りないものがあれば原因が分かるメッセージを出す。"""
    if not dll_dir.is_dir():
        raise SdkError(f"DLL フォルダがありません: {dll_dir}\n"
                       "  → config.toml の [paths] dll_dir を、この PC の SDK の Library\\release に合わせてください")
    missing = [n for n in REQUIRED_DLLS if not (dll_dir / n).is_file()]
    if missing:
        raise SdkError(f"DLL フォルダに次のファイルがありません: {', '.join(missing)}\n  フォルダ: {dll_dir}")

    # 依存 DLL (Procedure.dll, opencv など) を同じフォルダから探せるようにする
    os.add_dll_directory(str(dll_dir))
    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    try:
        return ctypes.CDLL(str(dll_dir / "HSProc.dll"))
    except OSError as e:
        raise SdkError(f"HSProc.dll を読み込めませんでした: {e}\n"
                       "  → Microsoft Visual C++ 再頒布可能パッケージ (2015-2022, x64) が入っているか確認してください\n"
                       "    (Iromiel Hyper アプリをインストール済みの PC なら通常は入っています)") from e


# ============================================================================
# SDK ラッパー (今回使う関数だけ)
# ============================================================================

class HSProc:
    def __init__(self, dll_dir: Path, path_encoding: str):
        self.path_encoding = path_encoding
        self.lib = load_dll(dll_dir)
        self._declare()

        self.handle = self.lib.HSProc_Create()
        if not self.handle:
            raise SdkError("HSProc_Create が失敗しました")

        self._cb = None           # コールバックが GC で消えないよう保持
        self.cb_lock = threading.Lock()
        self.cb_events = []       # [(時刻, 画像数, 保存ファイル名のリスト), ...]
        # コールバックで渡される画像を受け取りたいときに設定する関数 hook(画像数, images)
        # (SDK のスレッドから呼ばれる。images はコールバックの中でしか使えない)
        self.image_hook = None

    def _declare(self):
        L = self.lib
        vp, i, b, cp = ctypes.c_void_p, ctypes.c_int, ctypes.c_bool, ctypes.c_char_p

        def sig(name, restype, *argtypes):
            f = getattr(L, name)
            f.restype = restype
            f.argtypes = list(argtypes)

        sig("HSProc_Create", vp)
        sig("HSProc_Destroy", None, vp)
        sig("HSProc_Init", i, vp, i, cp, i, i, i, i, i)
        sig("HSProc_SetCallback", i, vp, IMAGES_CB, vp)
        sig("HSProc_Close", i, vp)
        sig("HSProc_Start", i, vp, b)
        sig("HSProc_Stop", i, vp)
        sig("HSProc_StartRec", i, vp, b)
        sig("HSProc_StopRec", i, vp)
        sig("HSProc_GetStatus", i, vp)
        sig("HSProc_StartSaveFile", i, vp, cp)
        sig("HSProc_StopSaveFile", i, vp)
        sig("HSProc_SetWhiteFromFile", i, vp, ctypes.POINTER(cp), ctypes.c_size_t, b, b)
        sig("HSProc_GetWhite", i, vp, i, b, b)
        # whites_len はヘッダでは C++ の参照 (size_t&) だが、実際の受け渡しはポインタと同じ
        sig("HSProc_GetWhiteWithRoi", i, vp, i, Rect, ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_size_t), b, b)
        sig("HSProc_SetWhiteFromData", i, vp, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t)
        sig("HSProc_SetDarkFromFile", i, vp, ctypes.POINTER(cp), ctypes.c_size_t, b)
        sig("HSProc_SetCamParamWithFilePath", i, vp, ctypes.POINTER(HSPROC_CAM_PARAM), cp)
        sig("HSProc_SetRecParam", i, vp, ctypes.POINTER(HSPROC_REC_PARAM))
        sig("HSProc_SetLog", i, vp, b)
        sig("HSProc_OneShot", i, vp, b)
        sig("HSProc_ChangeMode", i, vp, i)
        sig("HSProc_SetSave", None, vp, b, b, b)
        sig("HSProc_SetLightweightSave", None, vp, b, b)

    def _path(self, p) -> bytes:
        return str(p).encode(self.path_encoding)

    @staticmethod
    def _check(ret, what):
        if ret != 0:
            raise SdkError(f"{what} が失敗しました (戻り値 {ret})")

    # --- 初期化・終了 -----------------------------------------------------
    def init(self, mask_csv: Path, bitdepth: int, camera=CAMTYPE_DUMMY):
        ret = self.lib.HSProc_Init(self.handle, bitdepth, self._path(mask_csv), camera, 0, 0, 0, 0)
        if ret == -2:
            raise SdkError("HSProc_Init: 無効な切り出し (-2)。マスクと画像サイズを確認してください")
        self._check(ret, "HSProc_Init")

    def close(self):
        if self.handle:
            self.lib.HSProc_Close(self.handle)
            self.lib.HSProc_Destroy(self.handle)
            self.handle = None

    # --- コールバック -----------------------------------------------------
    def set_callback(self):
        def on_images(_ptr, num_images, _images, savelist):
            try:
                names = []
                if savelist:
                    sl = savelist.contents
                    for k in range(sl.count):
                        raw = sl.filenames[k]
                        names.append(raw.decode(self.path_encoding, errors="replace") if raw else "")
                with self.cb_lock:
                    self.cb_events.append((time.monotonic(), num_images, names))
                hook = self.image_hook
                if hook is not None:
                    hook(num_images, _images)
            except Exception as e:  # コールバック内の例外で SDK を止めない
                print(f"  [callback error] {e}")

        self._cb = IMAGES_CB(on_images)
        self._check(self.lib.HSProc_SetCallback(self.handle, self._cb, None), "HSProc_SetCallback")

    @property
    def cb_count(self) -> int:
        with self.cb_lock:
            return len(self.cb_events)

    def reset_callback_stats(self):
        with self.cb_lock:
            self.cb_events = []

    # --- 設定 -------------------------------------------------------------
    def set_rec_param(self, prm: dict):
        s = HSPROC_REC_PARAM(**prm)
        self._check(self.lib.HSProc_SetRecParam(self.handle, ctypes.byref(s)), "HSProc_SetRecParam")

    def set_input_file(self, cam_prm: dict, file_path: Path):
        s = HSPROC_CAM_PARAM(**cam_prm)
        ret = self.lib.HSProc_SetCamParamWithFilePath(self.handle, ctypes.byref(s), self._path(file_path))
        self._check(ret, "HSProc_SetCamParamWithFilePath")

    def set_save(self, png: bool, envi: bool, log: bool, light: bool):
        self.lib.HSProc_SetSave(self.handle, png, envi, log)
        self.lib.HSProc_SetLightweightSave(self.handle, light, log)

    def set_rec_log(self, on: bool):
        self._check(self.lib.HSProc_SetLog(self.handle, on), "HSProc_SetLog")

    def set_white(self, files):
        arr = (ctypes.c_char_p * len(files))(*[self._path(f) for f in files])
        self._check(self.lib.HSProc_SetWhiteFromFile(self.handle, arr, len(files), True, False),
                    "HSProc_SetWhiteFromFile")

    def get_white_roi(self, roi, num: int = 1, capacity: int = 256):
        """領域指定の白板補正 (アプリの「領域指定」+「白板撮影」)。仕様書には記載なし。

        SDK が roi 内から白板の値を求めて白板補正を有効にし、その値を返す。
        指定した枚数の画像を取り込むまで戻らない (撮影中である必要がある)。
        roi: (x0, y0, x1, y1)。x1, y1 はその画素を含まない (numpy の [y0:y1, x0:x1] と同じ)
        """
        x0, y0, x1, y1 = (int(v) for v in roi)
        buf = (ctypes.c_float * capacity)()
        n = ctypes.c_size_t(capacity)  # 渡すときは入れ物の大きさ、戻ると実際の個数
        ret = self.lib.HSProc_GetWhiteWithRoi(self.handle, num, Rect(x0, y0, x1 - x0, y1 - y0),
                                              buf, ctypes.byref(n), True, False)
        self._check(ret, "HSProc_GetWhiteWithRoi")
        return list(buf[: min(n.value, capacity)])

    def set_white_data(self, whites):
        """白板の値 (バンドごと) を設定して白板補正を有効にする (アプリの「白板設定」で json を読むのと同じ)。"""
        arr = (ctypes.c_float * len(whites))(*[float(v) for v in whites])
        self._check(self.lib.HSProc_SetWhiteFromData(self.handle, arr, len(whites)), "HSProc_SetWhiteFromData")

    def set_dark(self, files):
        arr = (ctypes.c_char_p * len(files))(*[self._path(f) for f in files])
        self._check(self.lib.HSProc_SetDarkFromFile(self.handle, arr, len(files), False),
                    "HSProc_SetDarkFromFile")

    def change_mode(self, mode: int):
        self._check(self.lib.HSProc_ChangeMode(self.handle, mode), "HSProc_ChangeMode")

    def status(self) -> int:
        return self.lib.HSProc_GetStatus(self.handle)

    def wait_until_stopped(self, timeout: float = 10.0) -> bool:
        """撮影動作中 (RUN) でなくなるまで待つ。"""
        t0 = time.monotonic()
        while self.status() == STATUS_RUN:
            if time.monotonic() - t0 > timeout:
                return False
            time.sleep(0.05)
        return True

    # --- 動作 -------------------------------------------------------------
    def start_save(self, save_dir: Path):
        self._check(self.lib.HSProc_StartSaveFile(self.handle, self._path(save_dir)), "HSProc_StartSaveFile")

    def stop_save(self):
        self.lib.HSProc_StopSaveFile(self.handle)

    def start_rec(self):
        self._check(self.lib.HSProc_StartRec(self.handle, True), "HSProc_StartRec")

    def stop_rec(self):
        self.lib.HSProc_StopRec(self.handle)

    def start(self):
        # 引数の保存フラグは仕様書では「撮影画像の保存」だが、実際は False にすると
        # 復元画像も含めて何も保存されなかった (実機で確認) ため、常に True で呼ぶ
        self._check(self.lib.HSProc_Start(self.handle, True), "HSProc_Start")

    def stop(self):
        self.lib.HSProc_Stop(self.handle)

    def one_shot(self):
        self._check(self.lib.HSProc_OneShot(self.handle, True), "HSProc_OneShot")


# ============================================================================
# 処理本体
# ============================================================================

def list_inputs(input_dir: Path, exts):
    exts = {e.lower().lstrip(".") for e in exts}
    files = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower().lstrip(".") in exts]
    return sorted(files, key=lambda p: p.name)


def read_band_count(mask_csv: Path) -> int:
    """_hsmask.csv の 2 行目 (幅,高さ,バンド数,...) からバンド数を読む。"""
    try:
        with open(mask_csv, encoding="utf-8-sig") as f:
            f.readline()
            return int(f.readline().split(",")[2])
    except (OSError, ValueError, IndexError):
        sys.exit(f"キャリブレーションデータからバンド数を読めませんでした: {mask_csv}")


def count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.rglob("*") if p.is_file())


def result_groups(rec_dir: Path) -> dict:
    """reconstruct フォルダの中身を「1 回の復元」ごとにまとめる。{名前: {パス: サイズ}}, 古い順。

    アプリの取扱説明書によると、復元画像は band**_xxxx.png, bands_xxxx.hdr/.raw という名前で
    撮影ごとにフォルダに分けて保存される。フォルダの分け方が想定と違っても判定できるよう、
    「サブフォルダ名 + ファイル名末尾の番号 xxxx」の組み合わせでまとめる。
    """
    groups, first_seen = {}, {}
    if not rec_dir.is_dir():
        return groups
    for p in rec_dir.rglob("*"):
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:  # 書き込み中・削除中のファイルは次回に回す
            continue
        rel = p.relative_to(rec_dir)
        sub = rel.parts[0] if len(rel.parts) > 1 else ""
        m = re.search(r"_(\d+)(?:_light)?$", p.stem)
        if m:
            key = f"{sub}/{m.group(1)}"
        else:
            key = sub or p.stem
        groups.setdefault(key, {})[p] = st.st_size
        first_seen[key] = min(first_seen.get(key, st.st_mtime), st.st_mtime)
    return {k: groups[k] for k in sorted(groups, key=lambda k: first_seen[k])}


def png_complete(p: Path) -> bool:
    """PNG が最後まで書き終わっているか (末尾が IEND チャンクか)。"""
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() < len(PNG_IEND):
                return False
            f.seek(-len(PNG_IEND), os.SEEK_END)
            return f.read() == PNG_IEND
    except OSError:
        return False


def envi_expected_size(hdr: Path):
    """ENVI ヘッダ (.hdr) から、対応する .raw の本来のサイズ [byte] を求める。読めなければ None。"""
    try:
        text = hdr.read_text(encoding="ascii", errors="ignore")
    except OSError:
        return None
    vals = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip().lower()] = v.strip()
    try:
        pixels = int(vals["samples"]) * int(vals["lines"]) * int(vals["bands"])
        return pixels * ENVI_TYPE_BYTES[int(vals["data type"])] + int(vals.get("header offset", "0"))
    except (KeyError, ValueError):
        return None


def is_complete(files, nbands: int, need_png: bool, need_envi: bool, light: bool = False) -> bool:
    """1 回分の復元結果がそろい、すべて最後まで書き終わっているか。

    light=False は通常版 (名前に _light を含まないファイル)、True は 8bit 軽量化版を判定する。
    """
    sel = [p for p in files if ("_light" in p.name.lower()) == light]
    if need_png:
        pngs = [p for p in sel if p.name.lower().startswith("band") and p.suffix.lower() == ".png"]
        if len(pngs) < nbands or not all(png_complete(p) for p in pngs):
            return False
    if need_envi:
        hdrs = [p for p in sel if p.suffix.lower() == ".hdr"]
        if not hdrs:
            return False
        for hdr in hdrs:
            size = envi_expected_size(hdr)
            try:
                if size is None or hdr.with_suffix(".raw").stat().st_size < size:
                    return False
            except OSError:
                return False
    return need_png or need_envi


def first_group_complete(rec_dir: Path, args, light: bool = False) -> bool:
    groups = result_groups(rec_dir)
    return bool(groups) and is_complete(next(iter(groups.values())), args.nbands, args.png, args.envi, light)


def wait_for_first_result(out_dir: Path, args, progress=None, nudge=None):
    """最初の 1 回分の復元結果が書き終わるまで待つ。戻り値: (結果の名前, ファイル数) / タイムアウト時 None

    完了の判定:
      1. バンド数ぶんの png と .hdr/.raw がそろい、すべて最後まで書き終わっている
         (png は末尾の IEND、.raw は .hdr から計算したサイズで確認)。
         軽量化版も保存する場合はそちらも。その状態が settle 秒続いたら完了。
      2. [連続撮影] 2 回目の結果が出始め、1 回目のファイルが 1 秒以上変化していない
      3. [単発撮影] 結果ファイルの変化が ONESHOT_IDLE 秒止まった (繰り返しが無いので書き終わっている)
      2, 3 はファイル名が想定と違って 1 の判定ができない場合の保険。

    progress: 10 秒ごとに progress(経過秒, groups) を呼ぶ (待っている間の様子の表示用)
    nudge:    毎回 nudge(現在時刻) を呼ぶ。True を返したら待つのをやめる (反応が無いときの合図の送り直し)
    """
    rec_dir = out_dir / "reconstruct"
    t0 = time.monotonic()
    prev, stable_since = None, t0
    next_report = 10.0
    while time.monotonic() - t0 < args.timeout:
        now = time.monotonic()
        if nudge and nudge(now):
            return None
        groups = result_groups(rec_dir)
        if progress and now - t0 >= next_report:
            progress(now - t0, groups)
            next_report += 10.0
        if groups:
            key, files = next(iter(groups.items()))
            if files != prev:
                prev, stable_since = files, now
            stable = now - stable_since
            done = (is_complete(files, args.nbands, args.png, args.envi)
                    and (not args.light or first_group_complete(out_dir / "reconstruct_light", args, light=True)))
            if done and stable >= args.settle:
                return key, len(files)
            if args.mode == "continuous" and len(groups) >= 2 and stable >= 1.0:
                return key, len(files)
            if args.mode == "oneshot" and stable >= ONESHOT_IDLE:
                return key, len(files)
        time.sleep(0.1)
    return None


def remove_extra_results(out_dir: Path) -> int:
    """reconstruct / reconstruct_light の中の、最初の 1 回分以外の結果を削除する。戻り値: 削除した結果の数"""
    removed = 0
    for sub in ("reconstruct", "reconstruct_light"):
        groups = list(result_groups(out_dir / sub).items())
        for _key, files in groups[1:]:
            for p in files:
                p.unlink(missing_ok=True)
            removed += 1
        # 空になったサブフォルダを片付ける
        d = out_dir / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_dir() and not any(f.rglob("*")):
                    shutil.rmtree(f, ignore_errors=True)
    return removed


def already_done(out_dir: Path, args) -> bool:
    """最初の 1 回分の復元結果が最後まで書き終わっていれば処理済み (途中で止まったものはやり直す)。"""
    if not first_group_complete(out_dir / "reconstruct", args):
        return False
    return not args.light or first_group_complete(out_dir / "reconstruct_light", args, light=True)


# 復元結果のファイル名 (band01_<名前>, bands_<名前>, band01_<名前>_light) から <名前> を取り出す
STAMP_RE = re.compile(r"^bands?\d*_(.+?)(?:_light)?$")


def find_stamps(out_dir: Path) -> set:
    """復元結果のファイル名に付いている名前 (再構成日時、付け替え後は RAW 名) を集める。"""
    stamps = set()
    for sub in ("reconstruct", "reconstruct_light"):
        d = out_dir / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            m = STAMP_RE.match(p.stem) if p.is_file() else None
            if m:
                stamps.add(m.group(1))
    return stamps


def rename_to_raw_name(out_dir: Path, raw_name: str):
    """out_dir の中の再構成日時を RAW 名に付け替える。戻り値: 付け替えた再構成日時 (済んでいれば None)

    フォルダ名・ファイル名の再構成日時を置き換え、log の csv に書かれたパスも書き換える。
    何度呼んでも同じ結果になる (途中で止まっても、次の実行で続きから付け替える)。
    """
    stamps = find_stamps(out_dir) - {raw_name}
    if not stamps:
        return None
    if len(stamps) > 1:
        raise SdkError(f"復元結果の名前が複数あり、RAW 名に付け替えられません: {sorted(stamps)} ({out_dir})")
    stamp = stamps.pop()

    # 深い階層から順に付け替える (先にフォルダ名を変えると、中のファイルのパスが変わるため)
    for p in sorted(out_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if stamp not in p.name:
            continue
        if p.is_file() and p.suffix.lower() == ".csv" and stamp.isascii() and raw_name.isascii():
            p.write_bytes(p.read_bytes().replace(stamp.encode("ascii"), raw_name.encode("ascii")))
        _move(p, p.with_name(p.name.replace(stamp, raw_name)))
    return stamp


def _move(src: Path, dst: Path):
    """src を dst に移す。dst が既にあるフォルダなら中身をまとめる (前回の付け替えの続き)。"""
    if src.is_dir() and dst.is_dir():
        for child in list(src.iterdir()):
            _move(child, dst / child.name)
        src.rmdir()
    else:
        os.replace(src, dst)


def clear_folder(folder: Path):
    """フォルダの中身だけ削除する (フォルダ自体は残す)。"""
    if not folder.is_dir():
        return
    for p in folder.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def describe_output(out_dir: Path, max_files: int = 15):
    """出力フォルダに何ができているかを表示する (うまく完了しなかったときの手がかり用)。"""
    print(f"  --- 出力フォルダの中身: {out_dir}")
    if not out_dir.is_dir():
        print("    (フォルダがありません)")
        return
    subs = sorted(p for p in out_dir.iterdir() if p.is_dir())
    for d in subs:
        print(f"    {d.name}/  {count_files(d)} ファイル")
    if not subs:
        print("    (サブフォルダがありません)")
    rec = out_dir / "reconstruct"
    shown = sorted(p for p in rec.rglob("*") if p.is_file())[:max_files] if rec.is_dir() else []
    for p in shown:
        print(f"      {p.relative_to(out_dir)}  ({p.stat().st_size:,} bytes)")


def trigger(hs: HSProc, mode: str, delay: float = 0.0):
    """撮影を開始する。

    oneshot:    単発撮影。start() で撮影待ちの状態 (RUN) になり、oneShot() 1 回につき 1 回だけ
                撮影・復元・保存される (2026-09-30 に probe_oneshot.py で実機確認。
                start() を呼ばずに oneShot() を呼ぶと -1 が返る)。
                start() 直後に oneShot() を送ると 1 割ほど無視された (2026-10-12) ので、
                RUN になるのを待ち、さらに delay 秒おいてから送る。
    continuous: 連続撮影。1 つのファイルを指定した場合、止めるまで同じ画像が繰り返し再生・復元される
                (実機では「復元 約 1 秒 → 保存 約 1.2 秒」の繰り返し)。
    """
    hs.start()
    if mode == "oneshot":
        if delay > 0:
            t0 = time.monotonic()
            while hs.status() != STATUS_RUN and time.monotonic() - t0 < 2.0:
                time.sleep(0.05)
            time.sleep(delay)
        hs.one_shot()


def process_one(hs: HSProc, src: Path, out_dir: Path, args):
    """1 ファイルを復元して保存する。戻り値: (成功したか, 結果のファイル数, 所要秒, 削除した余分な結果の数)"""
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    # 前回の途中結果が残っていると「最初の 1 回分」を取り違えるので、このファイルの復元結果だけ消してから始める
    for sub in ("reconstruct", "reconstruct_light"):
        shutil.rmtree(out_dir / sub, ignore_errors=True)
    hs.reset_callback_stats()

    # アプリの「ファイル接続」でファイルを指定するのと同じ (撮影停止中に行う)
    hs.set_input_file(args.cam_param, src)

    def progress(elapsed, groups):
        parts = [f"{d.name} {count_files(d)}" for d in sorted(out_dir.iterdir()) if d.is_dir()]
        print(f"\n    経過 {elapsed:.0f} 秒: 結果のまとまり {len(groups)} 個 / callback {hs.cb_count} 回 / "
              f"状態 {STATUS_NAMES.get(hs.status(), hs.status())} / ファイル数 [{', '.join(parts) or 'なし'}]",
              end="", flush=True)

    triggers = []

    def nudge(now):
        # 単発撮影の合図を SDK が取りこぼすことがある (callback も復元結果も出てこない)。
        # retrigger 秒ごとに送り直し、retrigger_max 回送り直しても反応が無ければ待つのをやめる
        if args.mode != "oneshot" or args.retrigger <= 0 or not triggers:
            return False
        if hs.cb_count or count_files(out_dir / "reconstruct") or now - triggers[-1] < args.retrigger:
            return False
        if len(triggers) > args.retrigger_max:
            print(f"\n    反応がありません ({now - triggers[0]:.0f} 秒)", end="", flush=True)
            return True
        print(f"\n    反応がないため撮影の合図を送り直します ({len(triggers)} 回目)", end="", flush=True)
        hs.one_shot()
        triggers.append(now)
        return False

    # [Reconstruct] → [Record] → [View]
    # 仕様書 5 章の図は「start → startRec / startSaveFile」の順だが、単発撮影では撮影の瞬間に
    # 復元と保存が有効になっている必要があるため、先に startRec / startSaveFile を呼んでおく
    hs.start_rec()
    hs.start_save(out_dir)
    try:
        trigger(hs, args.mode, args.trigger_delay)
        triggers.append(time.monotonic())
        result = wait_for_first_result(out_dir, args, progress, nudge)
    except KeyboardInterrupt:
        print()
        describe_output(out_dir)
        raise
    finally:
        hs.retriggers = max(len(triggers) - 1, 0)  # batch_log.csv に残す
        # 仕様書 5 章・SDK サンプルと同じ順番で止める: 保存停止 → 復元停止 → 撮影停止
        # 保存を最初に止めるので、停止処理の間に次の復元結果が保存されることがない
        hs.stop_save()
        hs.stop_rec()
        hs.stop()
        stopped = hs.wait_until_stopped()
        time.sleep(0.3)  # 停止直前に始まった書き込みが終わるのを少し待つ
        if not args.save_camera:
            # 撮影画像は保存を止められないので、終わってから消す (元の RAW があるので不要)
            clear_folder(out_dir / "camera")
    if not stopped:
        raise SdkError("撮影停止 (HSProc_Stop) のあとも動作中 (RUN) のままです")

    if result is None:
        print()
        describe_output(out_dir)
        return False, count_files(out_dir / "reconstruct"), time.monotonic() - t0, 0
    _key, n = result
    removed = remove_extra_results(out_dir) if args.remove_extra else 0
    return True, n, time.monotonic() - t0, removed


def load_config(config_path: Path) -> SimpleNamespace:
    """config.toml を読み込み、処理で使う設定をまとめて返す。"""
    if not config_path.is_file():
        sys.exit(f"設定ファイルがありません: {config_path}")
    try:
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"設定ファイルの書き方に誤りがあります: {config_path}\n  {e}")

    base = config_path.resolve().parent

    def get(section, key):
        try:
            return cfg[section][key]
        except KeyError:
            sys.exit(f"設定ファイルに [{section}] の {key} がありません: {config_path}")

    def opt(section, key, default):
        # 後から追加した項目は省略可能 (古い config.toml もそのまま使えるように)
        return cfg.get(section, {}).get(key, default)

    def path(p) -> Path:
        # 相対パスは config.toml のあるフォルダ基準
        p = Path(p)
        return p if p.is_absolute() else (base / p).resolve()

    def params(section, keys):
        missing = [k for k in keys if k not in cfg.get(section, {})]
        if missing:
            sys.exit(f"設定ファイルの [{section}] に {', '.join(missing)} がありません")
        return {k: cfg[section][k] for k in keys}

    mode = get("run", "mode")
    if mode not in ("continuous", "oneshot"):
        sys.exit(f"[run] mode は 'continuous' か 'oneshot' にしてください (今の値: {mode!r})")

    return SimpleNamespace(
        input=path(get("paths", "input")),
        output=path(get("paths", "output")),
        mask=path(get("paths", "mask")),
        white=[path(p) for p in get("paths", "white")],
        dark=[path(p) for p in get("paths", "dark")],
        dll_dir=path(get("paths", "dll_dir")),
        ext=",".join(get("input", "extensions")),
        limit=int(get("input", "limit")),
        overwrite=bool(get("input", "overwrite")),
        png=bool(get("save", "png")),
        envi=bool(get("save", "envi")),
        light=bool(get("save", "light")),
        log=bool(get("save", "log")),
        rec_log=bool(get("save", "rec_log")),
        save_camera=bool(opt("save", "camera", False)),
        remove_extra=bool(opt("run", "remove_extra", True)),
        trigger_delay=float(opt("run", "trigger_delay", 0.5)),
        retrigger=float(opt("run", "retrigger", 20.0)),
        retrigger_max=int(opt("run", "retrigger_max", 2)),
        restart_on_fail=bool(opt("run", "restart_on_fail", True)),
        mode=mode,
        timeout=float(get("run", "timeout")),
        settle=float(opt("run", "settle", 0.3)),
        path_encoding=get("run", "path_encoding"),
        rec_param=params("rec_param", REC_PARAM_KEYS),
        cam_param=params("cam_param", CAM_PARAM_KEYS),
        bitdepth=int(get("cam_param", "bitdepth")),
    )


# ============================================================================
# 一括処理の部品 (main と、HSI2RGB_main の automation_rgb.py が使う)
# ============================================================================

def check_settings(args):
    """入力フォルダ以外の設定を確かめ、バンド数を args.nbands に入れる。"""
    if sys.maxsize <= 2**32:
        sys.exit("64bit 版の Python が必要です (SDK は x64 の DLL です)")
    if not args.mask.is_file():
        sys.exit(f"キャリブレーションデータがありません: {args.mask}")
    for f in args.white + args.dark:
        if not f.is_file():
            sys.exit(f"ファイルがありません: {f}")
    args.nbands = read_band_count(args.mask)
    print(f"  バンド数: {args.nbands}  /  撮影モード: {args.mode}")
    if args.mode == "continuous" and args.settle >= 0.8:
        print(f"  注意: [run] settle = {args.settle} は長すぎます。連続撮影では約 1 秒ごとに次の復元結果が"
              "保存されるため、余分な結果ができます (config.toml で 0.3 程度にしてください)")


def plan_folder(input_dir: Path, output_dir: Path, args):
    """入力フォルダの処理対象と、そのうち未処理のもの。戻り値: (files, todo)"""
    files = list_inputs(input_dir, args.ext.split(","))
    if args.limit > 0:
        files = files[: args.limit]
    todo = [f for f in files if args.overwrite or not already_done(output_dir / f.stem, args)]
    return files, todo


def finish_renames(files, todo, output_dir: Path):
    """処理済みでも、前回の付け替えが途中で止まっていれば続きを行う。"""
    for f in files:
        if f in todo:
            continue
        try:
            stamp = rename_to_raw_name(output_dir / f.stem, f.stem)
        except (SdkError, OSError) as e:
            print(f"  警告: {f.name} を RAW 名に付け替えられません: {e}")
            continue
        if stamp:
            print(f"  {f.name}: 再構成日時 {stamp} を RAW 名に付け替えました")


def open_sdk(args) -> HSProc:
    """SDK を初期化して設定する (アプリの「ファイル接続」と各種設定)。"""
    hs = HSProc(args.dll_dir, args.path_encoding)
    try:
        hs.init(args.mask, args.bitdepth, CAMTYPE_DUMMY)
        hs.set_callback()
        hs.set_rec_param(args.rec_param)
        hs.set_rec_log(args.rec_log)
        hs.set_save(png=args.png, envi=args.envi, log=args.log, light=args.light)
        if args.dark:
            hs.set_dark(args.dark)
            print(f"遮光画像を設定: {len(args.dark)} 枚")
        if args.white:
            hs.set_white(args.white)
            print(f"白板画像を設定: {len(args.white)} 枚")
        if args.mode == "oneshot":
            hs.change_mode(CAPMODE_ONE_SHOT)
    except BaseException:
        hs.close()
        raise
    print(f"状態: {STATUS_NAMES.get(hs.status(), hs.status())}")
    return hs


class SdkSession:
    """SDK を開いたまま使い回す。反応しなくなったら restart() で初期化し直す。"""

    def __init__(self, args):
        self.args = args
        self.hs = None

    def open(self):
        if self.hs is None:
            print("SDK を初期化しています...")
            self.hs = open_sdk(self.args)

    def restart(self):
        self.close()
        self.open()

    def close(self):
        if self.hs is not None:
            self.hs.close()
            self.hs = None


def reconstruct_one(hs: HSProc, src: Path, out_dir: Path, args) -> SimpleNamespace:
    """1 ファイルを復元して RAW 名に付け替える。result は "ok" / "timeout" / "error"。"""
    try:
        success, n, seconds, removed = process_one(hs, src, out_dir, args)
        retried = f" / 合図の送り直し {hs.retriggers} 回"
        if not success:
            return SimpleNamespace(result="timeout", n=n, seconds=seconds, removed=0, message=(
                f"timeout ({args.timeout:.0f}s) / 出力 {n} ファイル / callback {hs.cb_count} 回"
                f" (期待: バンド png {args.nbands} 枚)" + retried))
        rec_stamp = rename_to_raw_name(out_dir, src.stem)
        return SimpleNamespace(result="ok", n=n, seconds=seconds, removed=removed, message=(
            f"callback {hs.cb_count} 回 / 余分な結果の削除 {removed} 個 / 再構成日時 {rec_stamp or '-'}"
            + retried))
    except (SdkError, OSError) as e:
        return SimpleNamespace(result="error", n=0, seconds=None, removed=0, message=str(e))


def reconstruct_files(session: SdkSession, todo, output_dir: Path, args, stats):
    """todo の RAW を 1 枚ずつ復元して <output_dir>/<RAW名>/ に保存する。成否は stats.ok / stats.ng に数える。

    失敗したら (restart_on_fail) SDK を初期化し直して、その 1 枚をもう一度だけ試す。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "batch_log.csv"
    new_log = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8-sig") as log_f:
        log = csv.writer(log_f)
        if new_log:
            log.writerow(["time", "input", "output_dir", "result", "files", "seconds", "message"])

        for idx, src in enumerate(todo, 1):
            out_dir = output_dir / src.stem
            print(f"[{idx}/{len(todo)}] {src.name} ... ", end="", flush=True)
            stamp = datetime.now().isoformat(timespec="seconds")
            r = reconstruct_one(session.hs, src, out_dir, args)
            if r.result != "ok" and args.restart_on_fail:
                print(f"失敗: {r.message}")
                print("    SDK を初期化し直して、もう一度試します")
                session.restart()
                print(f"    {src.name} ... ", end="", flush=True)
                r = reconstruct_one(session.hs, src, out_dir, args)
                r.message += " / SDK を初期化し直して再試行"
            if r.result == "ok":
                stats.ok += 1
                extra = f", 余分な結果 {r.removed} 個を削除" if r.removed else ""
                print(f"完了 ({r.n} ファイル, {r.seconds:.1f} 秒{extra})")
            else:
                stats.ng += 1
                print(f"失敗: {r.message}")
            seconds = "" if r.seconds is None else f"{r.seconds:.1f}"
            log.writerow([stamp, src, out_dir, r.result, r.n, seconds, r.message])
            log_f.flush()


def main():
    ap = argparse.ArgumentParser(description="Iromiel Hyper SDK: RAW 画像の一括復元 (設定は config.toml)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="設定ファイル (既定: 同じフォルダの config.toml)")
    ap.add_argument("--dry-run", action="store_true", help="SDK を動かさず、処理対象の一覧だけ表示")
    cli = ap.parse_args()

    args = load_config(cli.config)
    args.dry_run = cli.dry_run
    print(f"設定ファイル: {cli.config}")
    print(f"  入力: {args.input}")
    print(f"  出力: {args.output}")
    print(f"  マスク: {args.mask}")
    print(f"  SDK:   {args.dll_dir}")

    if not args.input.is_dir():
        sys.exit(f"入力フォルダがありません: {args.input}")
    check_settings(args)

    files, todo = plan_folder(args.input, args.output, args)
    if not files:
        sys.exit(f"処理対象のファイルがありません ({args.ext}): {args.input}")
    print(f"対象 {len(files)} 枚 / 未処理 {len(todo)} 枚")
    if args.dry_run:
        for f in todo:
            print(f"  {f.name}  ->  {args.output / f.stem}")
        # DLL が読み込めるかだけ確認 (カメラや GPU はまだ使わない)
        try:
            load_dll(args.dll_dir)
            print("SDK の DLL: 読み込み OK")
        except SdkError as e:
            print(f"SDK の DLL: {e}")
            return 1
        return 0

    finish_renames(files, todo, args.output)
    if not todo:
        print("すべて処理済みです (やり直す場合は config.toml の overwrite = true)")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    # SDK は実行時のカレントフォルダにログ (logBHSIR.txt など) を書くので、出力フォルダに移動しておく
    # (設定のパスはすべて絶対パスに変換済みなので、移動しても影響しない)
    os.chdir(args.output)

    session = SdkSession(args)
    stats = SimpleNamespace(ok=0, ng=0)
    t_all = time.monotonic()
    try:
        session.open()
        reconstruct_files(session, todo, args.output, args, stats)
    except KeyboardInterrupt:
        print("\n中断しました (次回実行時は未処理のファイルから再開します)")
    except SdkError as e:
        print(f"\nエラー: {e}")
        return 1
    finally:
        session.close()

    total = time.monotonic() - t_all
    print(f"\n成功 {stats.ok} 枚 / 失敗 {stats.ng} 枚 / 合計 {total / 60:.1f} 分")
    print(f"処理結果の一覧: {args.output / 'batch_log.csv'}")
    return 0 if stats.ng == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
