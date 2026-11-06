"""データセットの走査と、再構成フォルダ <-> RAW の対応付け。

Iromiel Hyper の出力フォルダはこうなっている。

    <dataset>/camera/compressed_<TS>.png    計測画像（再構成前）
    <dataset>/reconstruct/<TS>/band01..29.png  再構成バンド
    <dataset>/log/log_<TS>.csv              使ったマスク等の記録

<TS> は「再構成した日時」で、元の RAW のファイル名（例 00800648.RAW）は
引き継がれない。そこで対応付けのルールは2本立てになる。

    飽和マスク用の計測画像 : <TS> が一致する camera/compressed_<TS>.png（確実）
    出力ファイル名         : reconstruct/ の並び順 <-> raw_data/ の並び順（推定）

後者は推定なので、RAW を計測画像の代わりに使うときは mask.forward_model_r2 で
裏を取る（main.py 側でやっている）。

自動化（Iromiel hyper/automation/batch_reconstruct.py）の出力は、<TS> を RAW 名に
付け替えた「RAW名フォルダ形式」になっている。こちらは名前で対応するので確実。

    <dataset>/<RAW名>/reconstruct/<RAW名>/band01_<RAW名>.png ...
    <dataset>/<RAW名>/camera/compressed_<RAW名>.png   （残す設定のときだけ）

discover_scenes はどちらの形式かを自動で見分ける（detect_layout）。
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .fileio import load_compressed_measurement, load_raw_measurement


@dataclass
class Scene:
    """1枚の撮影に対応する入力の組。"""

    reconstruct_dir: Path       # reconstruct/<TS>  バンドPNGが29枚入っている
    timestamp: str              # <TS>（再構成日時。RAW名フォルダ形式では RAW 名）
    output_stem: str            # 出力ファイル名（元のRAW名。無ければ <TS>）
    raw_path: Path | None       # 対応する RAW（並び順で推定。RAW名フォルダ形式では名前で確実）
    compressed_path: Path | None  # camera/compressed_<TS>.png（<TS>一致で確実）

    def __str__(self):
        return f"{self.timestamp} -> {self.output_stem}"


def list_reconstruct_folders(reconstruct_root):
    """reconstruct 直下の各再構成フォルダを名前順（= 撮影順）で返す。"""
    root = Path(reconstruct_root)
    if not root.is_dir():
        raise FileNotFoundError(f"reconstructフォルダが見つかりません: {root}")
    folders = sorted(p for p in root.iterdir() if p.is_dir())
    if not folders:
        raise FileNotFoundError(f"再構成フォルダが空です: {root}")
    return folders


def list_raw_files(raw_root):
    """raw_data 内の RAW ファイルを名前順で返す。フォルダが無ければ空リスト。"""
    root = Path(raw_root)
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".raw"),
        key=lambda p: p.name,
    )


def detect_layout(dataset_dir):
    """入力フォルダの形式。"iromiel" / "raw_named" / None（どちらでもない）"""
    root = Path(dataset_dir)
    if (root / "reconstruct").is_dir():
        return "iromiel"
    if root.is_dir() and any(_raw_named_band_dir(p) for p in root.iterdir()):
        return "raw_named"
    return None


def _raw_named_band_dir(folder):
    band_dir = folder / "reconstruct" / folder.name
    return band_dir if band_dir.is_dir() else None


def discover_raw_named_scenes(dataset_dir, raw_dir, verbose=True):
    """RAW名フォルダ形式のデータセットを走査して Scene のリストを作る。"""
    root = Path(dataset_dir)
    raw_by_stem = {p.stem: p for p in list_raw_files(raw_dir)} if raw_dir else {}

    scenes = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        band_dir = _raw_named_band_dir(folder)
        if band_dir is None:
            continue
        name = folder.name
        compressed = folder / "camera" / f"compressed_{name}.png"
        scenes.append(
            Scene(
                reconstruct_dir=band_dir,
                timestamp=name,
                output_stem=name,
                raw_path=raw_by_stem.get(name),
                compressed_path=compressed if compressed.is_file() else None,
            )
        )
    if not scenes:
        raise FileNotFoundError(f"RAW名フォルダ形式の再構成結果がありません: {root}")

    if verbose:
        print(f"--- 入力: RAW名フォルダ形式 {len(scenes)} 件（RAW とは名前で対応）---")
        no_raw = [s.output_stem for s in scenes if s.raw_path is None]
        if raw_dir and no_raw:
            print(f"  警告: 同じ名前の RAW が {raw_dir} にありません: {', '.join(no_raw)}")
    return scenes


def discover_scenes(dataset_dir, raw_dir, verbose=True):
    """データセットを走査して Scene のリストを作る。

    RAW名フォルダ形式なら discover_raw_named_scenes に任せる。
    Iromiel Hyper 形式では、RAW が無い／足りない分は再構成フォルダ名をそのまま出力名に使う。
    """
    if detect_layout(dataset_dir) == "raw_named":
        return discover_raw_named_scenes(dataset_dir, raw_dir, verbose)

    dataset_dir = Path(dataset_dir)
    reconstruct_root = dataset_dir / "reconstruct"
    camera_root = dataset_dir / "camera"

    folders = list_reconstruct_folders(reconstruct_root)
    raw_files = list_raw_files(raw_dir) if raw_dir else []

    if not raw_files:
        print(
            f"警告: RAWファイルがありません ({raw_dir})。"
            "出力ファイル名は再構成フォルダ名を使います。"
        )
    elif len(raw_files) != len(folders):
        print(
            "警告: RAWファイル数と再構成フォルダ数が一致しません "
            f"(RAW={len(raw_files)}, reconstruct={len(folders)})。"
            "名前順で先頭から対応づけます。"
        )

    scenes = []
    for i, folder in enumerate(folders):
        raw_path = raw_files[i] if i < len(raw_files) else None
        compressed = camera_root / f"compressed_{folder.name}.png"
        scenes.append(
            Scene(
                reconstruct_dir=folder,
                timestamp=folder.name,
                output_stem=raw_path.stem if raw_path else folder.name,
                raw_path=raw_path,
                compressed_path=compressed if compressed.is_file() else None,
            )
        )

    if verbose:
        print("--- 出力ファイル名の対応 ---")
        for scene in scenes:
            origin = scene.raw_path.name if scene.raw_path else "(対応RAWなし、フォルダ名を使用)"
            print(f"  {scene.timestamp} -> {origin}")
    return scenes


def load_measurement(scene, allow_raw_fallback=True):
    """飽和マスク用の計測画像を、センサDN空間で読む。

    Returns:
        (計測画像 or None, 由来の説明文字列)
    """
    if scene.compressed_path is not None:
        # タイムスタンプが一致しているので対応は保証されている。
        return (
            load_compressed_measurement(str(scene.compressed_path)),
            f"camera/{scene.compressed_path.name}",
        )

    if allow_raw_fallback and scene.raw_path is not None and scene.raw_path.is_file():
        # 並び順で推定した対応なので、呼び出し側で順モデル検証をすること。
        return load_raw_measurement(str(scene.raw_path)), f"raw_data/{scene.raw_path.name}"

    return None, f"compressed_{scene.timestamp}.png が見つかりません"


def load_keep_names(json_path):
    """selection JSON のうち値が keep のファイル名集合を返す。"""
    path = Path(json_path)
    if not path.is_file():
        raise FileNotFoundError(f"selection JSONが見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {name for name, status in data.items() if status == "keep"}


def outputs_exist(stem, output_dirs, numpy_dir=None):
    """必要な出力がすべて揃っていれば True。一部欠けていれば再変換する。"""
    paths = [os.path.join(d, f"{stem}.png") for d in output_dirs]
    if numpy_dir:
        paths.append(os.path.join(numpy_dir, f"{stem}.npy"))
    return all(os.path.isfile(p) for p in paths)
