"""make_numpy の設定。ここだけ書き換える（使い方は README.md、詳しい説明は 説明.md）。

別のドライブなどを絶対パスで書くときは Path("D:/data/raw_data") のように Path(...) で囲む。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- 入力 ---
RAW_ROOT = PROJECT_ROOT / "raw_data"            # <RAW_ROOT>/<日付>/<時間>/<RAW名>.RAW
RECONSTRUCT_ROOT = PROJECT_ROOT / "input_folder"  # <RECONSTRUCT_ROOT>/<日付>/<時間>/ に再構成結果
DATES = []                                      # 探す日付。空なら RAW_ROOT の中を全部
CALIBRATION_JSON = PROJECT_ROOT / "calibration.json"  # 白板ROIの記録（WHITE_CORRECTION = True のときだけ使う）
NAMES_FILE = PROJECT_ROOT / "names.txt"               # npy にする RAW 名（1行1名。meta_data.json も可）。None なら RAW 全部
DARK_DIR = PROJECT_ROOT / "dark"                      # 黒補正のダーク（BLACK_CORRECTION = True のときだけ使う）

# --- 出力 ---
OUTPUT_DIR = PROJECT_ROOT / "output_npy"        # <OUTPUT_DIR>/<RAW名>.npy
NPY_UINT16 = True             # 補正なし（下の2つが False）なら npy を uint16 で保存する。値は再構成の値そのまま（劣化なし）で 1 枚 約160MB。
                              # False なら float32（約320MB、値は同じ）。補正するときは常に float32
LOG_DIR = PROJECT_ROOT / "logs"   # 実行の記録（npy_log.csv: 1 枚ごとの秒数、run_summary.csv: 何枚を何時間で作ったか）

# --- 補正（False / False なら再構成の値そのまま）---
BLACK_CORRECTION = False
WHITE_CORRECTION = False
WHITE_BOARD_REFLECTANCE = 0.9

# --- 動作 ---
OVERWRITE = False             # 既にある npy も作り直すか
RETRY_COUNT = 1               # 1枚が失敗したときのやり直し回数
RETRY_WAIT_SEC = 2
READ_TIMEOUT_SEC = 600        # 1枚の再構成バンドを読み終わるまでの上限 [秒]。超えたら Box Drive などが応答していないとみなして止める
RECONSTRUCT_MISSING = False   # 再構成結果が無ければ SDK で再構成する（Iromiel Hyper SDK と GPU のある PC のみ。README の「再構成」を参照）
DIRECT_NPY = True             # RECONSTRUCT_MISSING のとき、PNG を作らずに SDK から直接 npy にする（1 枚 約4秒）。
                              # False なら PNG に再構成してから npy にする（1 枚 約10秒）
AUTOMATION_DIR = PROJECT_ROOT / "automation"   # batch_reconstruct.py と config.toml の場所（RECONSTRUCT_MISSING のときだけ）
RECONSTRUCT_MB_PER_RAW = 100  # 再構成結果 1 枚の容量の目安（DIRECT_NPY = False で、config.toml の envi = false のとき。true なら 260）
DELETE_RECONSTRUCT_AFTER_NPY = False  # npy 化のために再構成した結果を、npy を作ったら消す（もとからある分は消さない）
