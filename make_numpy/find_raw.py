from dataclasses import dataclass
from pathlib import Path

from hsi2rgb import dataset
from hsi2rgb.fs_retry import retry_io

import settings


@dataclass(frozen=True)
class Folder:
    name: str          # "10082026/10"
    raw_dir: Path      # RAW のフォルダ
    input_dir: Path    # 同じ日付・時間の再構成結果のフォルダ


def find_raw(names):
    """RAW 名ごとに、どの日付・時間フォルダにあるかを探す。names が None なら見つかった RAW を全部対象にする。

    戻り値: (found {Folder: [RAW名]}, RAW が無い名前, 複数のフォルダにある名前)
    """
    if settings.DATES:
        date_dirs = [Path(d) if Path(d).is_absolute() else settings.RAW_ROOT / str(d)
                     for d in settings.DATES]
    else:
        date_dirs = sorted(p for p in _list_dir(settings.RAW_ROOT) if retry_io(p.is_dir))

    # RAW の入ったフォルダを集める（日付フォルダの直下に RAW があれば、日付フォルダごと1つ）
    # RAW のフォルダが読めないと対象を決められないので、やり直しても読めなければここで止める
    folders = []
    for date_dir in date_dirs:
        if not retry_io(date_dir.is_dir):
            print(f"警告: 日付フォルダがありません: {date_dir}")
            continue
        if dataset.list_raw_files(date_dir):
            folders.append(Folder(date_dir.name, date_dir,
                                  settings.RECONSTRUCT_ROOT / date_dir.name))
        for hour_dir in sorted(p for p in _list_dir(date_dir) if retry_io(p.is_dir)):
            if dataset.list_raw_files(hour_dir):
                folders.append(Folder(f"{date_dir.name}/{hour_dir.name}", hour_dir,
                                      settings.RECONSTRUCT_ROOT / date_dir.name / hour_dir.name))

    where = {}
    for folder in folders:
        for raw in dataset.list_raw_files(folder.raw_dir):
            where.setdefault(raw.stem, []).append(folder)
    if names is None:
        names = list(where)

    found, not_found, duplicated = {}, [], []
    for name in names:
        hits = where.get(name, [])
        if not hits:
            not_found.append(name)
        elif len(hits) > 1:
            duplicated.append(f"{name}: " + " / ".join(f.name for f in hits))
        else:
            found.setdefault(hits[0], []).append(name)

    print(f"対象 {len(names)} 件 / RAW が見つかった {sum(map(len, found.values()))} 件")
    for folder, folder_names in found.items():
        print(f"  {folder.name}: {len(folder_names)} 件")
    return found, not_found, duplicated


def _list_dir(root):
    """root の中身を一覧する（Box Drive などの一時的なエラーはやり直す）。"""
    return retry_io(lambda: list(root.iterdir()))
