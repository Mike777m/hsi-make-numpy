import threading
import time

from hsi2rgb import white_board

import settings
from make_bands import make_bands
from save_npy import save_npy


class ReadTimeout(Exception):
    """再構成バンドの読み込みが READ_TIMEOUT_SEC 秒たっても終わらない。"""


def make_npy_files(targets, dark):
    """targets [(Scene, roi)] を1枚ずつ npy にする。

    戻り値: (作った RAW 名, 失敗した RAW 名と理由, 途中で止めた理由 or None)

    バンドの読み込みが READ_TIMEOUT_SEC 秒で終わらなければ、Box Drive がファイルを
    ダウンロードできない状態とみなし、残りは処理せずに止める（そのまま続けると1枚ごとに待たされるため）。
    """
    made, failed = [], []
    for i, (scene, roi) in enumerate(targets, start=1):
        name = scene.output_stem
        print(f"[{i}/{len(targets)}] {name}  ({scene.reconstruct_dir})")
        for attempt in range(settings.RETRY_COUNT + 1):
            try:
                array = _make_bands_with_timeout(scene, roi, dark)
                path = save_npy(array, name)
                print(f"  {path} 保存完了  shape={array.shape} {array.dtype}")
                made.append(name)
                break
            except ReadTimeout as e:  # やり直しても待たされるだけなので、ここで止める
                print(f"  止めます: {e}")
                failed.append(f"{name}  {e}")
                return made, failed, str(e)
            except white_board.WhiteBoardNotUsable as e:  # やり直しても同じなので飛ばす
                print(f"  スキップ: {e}")
                failed.append(f"{name}  {e}")
                break
            except Exception as e:
                if attempt < settings.RETRY_COUNT:
                    print(f"  失敗、やり直します: {type(e).__name__}: {e}")
                    time.sleep(settings.RETRY_WAIT_SEC)
                else:
                    print(f"  スキップします: {type(e).__name__}: {e}")
                    failed.append(f"{name}  {type(e).__name__}: {e}")
    return made, failed, None


def _make_bands_with_timeout(scene, roi, dark):
    """make_bands を別スレッドで動かし、READ_TIMEOUT_SEC 秒で待つのをやめる。

    Box Drive がダウンロードできない状態だと、ファイルの読み込みがエラーにならずに何十分も戻らない。
    待つのをやめたスレッドは止められないので残るが、読むだけで何も書き込まない
    （npy の保存はこのスレッドではなく呼び出し側でするので、書きかけの npy ができることはない）。
    """
    result = {}

    def run():
        try:
            result["bands"] = make_bands(scene, roi, dark)
        except BaseException as e:  # 呼び出し側で同じ例外を出し直す
            result["error"] = e

    worker = threading.Thread(target=run, name=f"read-{scene.output_stem}", daemon=True)
    worker.start()
    worker.join(settings.READ_TIMEOUT_SEC)
    if worker.is_alive():
        raise ReadTimeout(
            f"再構成バンドの読み込みが {settings.READ_TIMEOUT_SEC} 秒たっても終わりません"
            "（Box Drive がファイルをダウンロードできていない可能性）"
        )
    if "error" in result:
        raise result["error"]
    return result["bands"]
