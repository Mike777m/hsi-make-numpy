import os
import sys
from types import SimpleNamespace

from hsi2rgb import dataset

import settings


def reconstruct_missing(missing, calibration):
    """再構成結果が無い RAW を、Iromiel Hyper SDK で同じ日付・時間のフォルダに再構成する。

    戻り値: 再構成できた枚数
    """
    jobs = []
    for folder, names in missing.items():
        if dataset.detect_layout(folder.input_dir) == "iromiel":
            print(f"  再構成しません: {folder.name}（{folder.input_dir} は Iromiel Hyper 形式）")
            continue
        raws = {p.stem: p for p in dataset.list_raw_files(folder.raw_dir)}
        todo = []
        for name in names:
            if settings.WHITE_CORRECTION and name not in calibration:
                print(f"  再構成しません: {name}（白板ROIの記録がありません。先に RGB 化してください）")
            else:
                todo.append(raws[name])
        if todo:
            jobs.append((folder, todo))
    if not jobs:
        return 0

    print(f"--- 再構成結果が無い {sum(len(todo) for _folder, todo in jobs)} 枚を再構成します")
    if not (settings.AUTOMATION_DIR / "batch_reconstruct.py").is_file():
        raise FileNotFoundError(f"batch_reconstruct.py が見つかりません: {settings.AUTOMATION_DIR}")
    sys.path.append(str(settings.AUTOMATION_DIR))
    import batch_reconstruct as br

    args = br.load_config(settings.AUTOMATION_DIR / "config.toml")
    args.ext = "raw"
    br.check_settings(args)

    # SDK はカレントフォルダにログ（logBHSIR.txt など）を書く
    settings.RECONSTRUCT_ROOT.mkdir(parents=True, exist_ok=True)
    prev_cwd = os.getcwd()
    os.chdir(settings.RECONSTRUCT_ROOT)
    stats = SimpleNamespace(ok=0, ng=0)
    session = br.SdkSession(args)
    try:
        session.open()
        for folder, todo in jobs:
            print(f"--- 再構成 {folder.name}: {len(todo)} 枚 -> {folder.input_dir}")
            br.reconstruct_files(session, todo, folder.input_dir, args, stats)
    except br.SdkError as e:
        print(f"  SDK を使えません: {e}")
    finally:
        session.close()
        os.chdir(prev_cwd)
    print(f"再構成: 成功 {stats.ok} 枚 / 失敗 {stats.ng} 枚")
    return stats.ok
