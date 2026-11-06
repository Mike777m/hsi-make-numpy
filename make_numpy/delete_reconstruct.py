import shutil


def delete_reconstruct(scenes):
    """npy 化のために再構成した結果を、RAW ごとのフォルダ <日付>/<時間>/<RAW名>/ ごと消す。"""
    deleted = 0
    for scene in scenes:
        raw_folder = scene.reconstruct_dir.parent.parent   # <RAW名>/reconstruct/<RAW名> → <RAW名>
        # RAW名フォルダ形式でなければ消さない（Iromiel Hyper 形式だと時間フォルダごと消してしまうため）
        if scene.reconstruct_dir != raw_folder / "reconstruct" / scene.output_stem \
                or raw_folder.name != scene.output_stem:
            print(f"  警告: RAW名フォルダ形式ではないので消しません: {scene.reconstruct_dir}")
            continue
        try:
            shutil.rmtree(raw_folder)
        except OSError as e:  # ほかのソフトが開いている等。npy はできているので先へ進む
            print(f"  警告: 再構成結果を消せません: {raw_folder}  {e}")
            continue
        deleted += 1
    if deleted:
        print(f"  npy 化のために再構成した結果を消しました: {deleted} 件")
    return deleted
