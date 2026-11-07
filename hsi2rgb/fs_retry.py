"""Box Drive などクラウド同期フォルダ上での、一時的な読み書きエラーのやり直し。

Box Drive は、フォルダの中身をクラウドから取ってこられないとき、Windows のエラー 1237
（「この操作を完了できませんでした。再実行してください。」）を返す。
pathlib の is_file() / is_dir() はこれを「無い」とはみなさず例外にするので、
そのままだと 1 か所読めないだけで実行全体が止まる。

retry_io は、こうした「やり直せば通るかもしれない」エラーのときだけ、少し待ってやり直す。
やり直しても通らなければ TransientIOError を出すので、呼び出し側はそれを捕まえて
「その回は飛ばして次の実行に回す」ことができる。ほかのエラーはそのまま出す。
"""

import time
from pathlib import Path

# やり直せば通る見込みのある Windows のエラー番号
TRANSIENT_WINERRORS = {
    121,   # ERROR_SEM_TIMEOUT  セマフォがタイムアウトしました。
    1237,  # ERROR_RETRY        この操作を完了できませんでした。再実行してください。（Box Drive）
    1460,  # ERROR_TIMEOUT      タイムアウト期間が経過したため、操作は終了しました。
}

# 1回目の失敗のあと 1 秒、2回目のあと 2 秒、3回目のあと 4 秒待ってやり直す（計 4 回試す）
RETRY_WAITS_SEC = (1, 2, 4)

# 1回の失敗にこれ以上かかったときはやり直さない。Box Drive は中でクラウドからの取得を最大 30 秒待ってから
# 失敗を返すことがあり、そのあとすぐやり直しても同じ結果になる（1か所で数分待たされるだけ）。
SLOW_FAILURE_SEC = 10


class TransientIOError(OSError):
    """やり直しても一時的なエラー（Box Drive の 1237 など）が続いた。"""


def is_transient(error):
    """やり直せば通る見込みのあるエラーか。"""
    return isinstance(error, OSError) and getattr(error, "winerror", None) in TRANSIENT_WINERRORS


def retry_io(func, *args, waits=RETRY_WAITS_SEC, **kwargs):
    """func(*args, **kwargs) を呼ぶ。一時的なエラーなら waits の秒数ずつ待ってやり直す。

    やり直しても通らなければ TransientIOError を出す。一時的でないエラーはそのまま出す。
    失敗するまでに SLOW_FAILURE_SEC 秒以上かかったときは、やり直さずに TransientIOError を出す。
    """
    for tries, wait in enumerate((*waits, None), start=1):
        started = time.monotonic()
        try:
            return func(*args, **kwargs)
        except TransientIOError:
            raise  # 内側でやり直し済み。重ねてやり直すと待ち時間が掛け算になる
        except OSError as e:
            if not is_transient(e):
                raise
            took = time.monotonic() - started
            if wait is None or took >= SLOW_FAILURE_SEC:
                raise TransientIOError(
                    e.errno, f"{tries} 回試しても読めません（最後は {took:.0f} 秒待って失敗）: {e.strerror}", e.filename
                ) from e
            print(f"  一時的な読み書きエラー、{wait} 秒後にやり直します: {_short(e.filename)}", flush=True)
            time.sleep(wait)


def _short(path, keep=4):
    """長いパスを末尾の数階層だけにする（表示用）。"""
    if not path:
        return "(パス不明)"
    parts = Path(path).parts
    return str(Path(*parts[-keep:])) if len(parts) > keep else str(path)
