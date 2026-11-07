import shutil

from hsi2rgb.fs_retry import retry_io


def check_disk_space(folder, n_files, mb_per_file):
    """folder に 1 個 mb_per_file MB のファイルを n_files 個置く空きがあるか。足りなければ False。"""
    need = n_files * mb_per_file * 1e6
    free = retry_io(shutil.disk_usage, folder).free
    print(f"必要な容量: 約 {need / 1e9:.1f} GB（{n_files} 件）  空き: {free / 1e9:.1f} GB  （{folder}）")
    return need <= free
