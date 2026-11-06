from dataclasses import dataclass
from pathlib import Path

from hsi2rgb import dataset

import settings


@dataclass(frozen=True)
class Folder:
    name: str          # "10082026/10"
    raw_dir: Path      # RAW のフォルダ
    input_dir: Path    # 同じ日付・時間の再構成結果のフォルダ


def find_raw(names):
    """RAW 名ごとに、どの日付・時間フォルダにあるかを探す。

    戻り値: (found {Folder: [RAW名]}, RAW が無い名前, 複数のフォルダにある名前)
    """
    if settings.DATES:
        date_dirs = [Path(d) if Path(d).is_absolute() else settings.RAW_ROOT / str(d)
                     for d in settings.DATES]
    else:
        date_dirs = sorted(p for p in settings.RAW_ROOT.iterdir() if p.is_dir())

    # RAW の入ったフォルダを集める（日付フォルダの直下に RAW があれば、日付フォルダごと1つ）
    folders = []
    for date_dir in date_dirs:
        if not date_dir.is_dir():
            print(f"警告: 日付フォルダがありません: {date_dir}")
            continue
        if dataset.list_raw_files(date_dir):
            folders.append(Folder(date_dir.name, date_dir,
                                  settings.RECONSTRUCT_ROOT / date_dir.name))
        for hour_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            if dataset.list_raw_files(hour_dir):
                folders.append(Folder(f"{date_dir.name}/{hour_dir.name}", hour_dir,
                                      settings.RECONSTRUCT_ROOT / date_dir.name / hour_dir.name))

    where = {}
    for folder in folders:
        for raw in dataset.list_raw_files(folder.raw_dir):
            where.setdefault(raw.stem, []).append(folder)

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
