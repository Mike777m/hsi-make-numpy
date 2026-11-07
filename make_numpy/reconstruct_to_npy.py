import ctypes
import os
import queue
import sys
import threading
import time

import numpy as np
from hsi2rgb import dataset, white_board
from hsi2rgb.fs_retry import TransientIOError

import settings
from correct_bands import correct_bands
from npy_dtype import npy_dtype
from save_npy import save_npy


class _SdkUnusable(Exception):
    """SDK を開けない・初期化し直せない。残りの RAW は処理できない。"""


def reconstruct_to_npy(missing, calibration, dark, log):
    """再構成結果が無い RAW を、PNG を作らずに SDK で復元して、そのまま npy にする。

    SDK は 1 回の復元ごとに、コールバックで画像 30 枚（0 枚目 = 撮影画像、1〜29 枚目 = band01〜29、16bit）を
    メモリのまま渡してくる。この値は保存される band PNG と画素単位で同じ（2026-11-07 に実機で確認）なので、
    PNG を書いて読み直す代わりに、これをそのまま npy にする。
    npy の保存は別スレッドで行い、Box などへの書き込みを待つ間に SDK が次の 1 枚を復元する。
    1 枚ごとの結果と秒数（復元・保存）は log（run_log.RunLog）に記録する。

    戻り値の辞書:
        made    [RAW名]           npy を作れたもの
        failed  [RAW名と理由]     復元・保存できなかったもの
        no_roi  [RAW名]           白板補正するのに白板ROIの記録が無いもの（復元しない）
    """
    result = {"made": [], "failed": [], "no_roi": []}

    def fail(name, message):
        result["failed"].append(f"{name}  {message}")
        log.record(name, "sdk", "error", message=message)

    jobs = []  # [(RAW名, RAW のパス)]
    for folder, names in missing.items():
        try:
            raws = {p.stem: p for p in dataset.list_raw_files(folder.raw_dir)}
        except TransientIOError as e:  # 次の実行に回す
            for name in names:
                fail(name, f"RAW のフォルダが一時的なエラーで読めません: {e}")
            continue
        for name in names:
            if settings.WHITE_CORRECTION and name not in calibration:
                result["no_roi"].append(name)
            elif name not in raws:
                fail(name, f"RAW が見つかりません: {folder.raw_dir}")
            else:
                jobs.append((name, raws[name]))
    if not jobs:
        return result

    print(f"--- 再構成結果が無い {len(jobs)} 枚を、SDK で復元して直接 npy にします（PNG は作りません）")
    if not (settings.AUTOMATION_DIR / "batch_reconstruct.py").is_file():
        raise FileNotFoundError(f"batch_reconstruct.py が見つかりません: {settings.AUTOMATION_DIR}")
    sys.path.append(str(settings.AUTOMATION_DIR))
    import batch_reconstruct as br

    args = br.load_config(settings.AUTOMATION_DIR / "config.toml")
    args.ext = "raw"
    # 復元画像はコールバックで受け取るので、ファイルは何も保存しない
    # （撮影画像の png だけは SDK が止められないので、作業フォルダに書かれたものを毎回消す）
    args.png = args.envi = args.light = args.log = False
    br.check_settings(args)

    dtype = npy_dtype()
    settings.RECONSTRUCT_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = settings.RECONSTRUCT_ROOT / "_direct_npy_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    # SDK はカレントフォルダにログ（logBHSIR.txt など）を書く
    prev_cwd = os.getcwd()
    os.chdir(settings.RECONSTRUCT_ROOT)
    capture = _Capture(args.nbands)
    session = br.SdkSession(args)
    writer = _NpyWriter(log)
    done = set()  # 結果が決まった RAW 名（保存に回した・失敗した）
    t_all = time.monotonic()
    try:
        _open(br, session, capture)
        for i, (name, raw) in enumerate(jobs, start=1):
            print(f"[{i}/{len(jobs)}] {name}  ({raw})")
            roi = tuple(int(v) for v in calibration[name]) if settings.WHITE_CORRECTION else None
            try:
                bands, seconds, message = _capture_with_retry(br, session, capture, raw, work_dir, args)
                array = bands if dtype == np.uint16 else correct_bands(bands.astype(np.float32), roi, dark)
            except white_board.WhiteBoardNotUsable as e:  # やり直しても同じなので飛ばす
                print(f"  スキップ: {e}")
                result["failed"].append(f"{name}  {e}")
                log.record(name, "sdk", "skip", message=str(e))
            except (br.SdkError, OSError) as e:
                print(f"  失敗: {e}")
                fail(name, f"{type(e).__name__}: {e}")
            else:
                print(f"  復元 {seconds:.1f} 秒{message}")
                writer.put(name, array.astype(dtype, copy=False), seconds, message.strip(" /"))
            done.add(name)
    except _SdkUnusable as e:
        print(f"  SDK を使えません: {e}")
        for name, _raw in jobs:
            if name not in done:
                fail(name, f"SDK を使えません: {e}")
    finally:
        made, failed = writer.close()  # 保存中の npy を書き終えるまで待つ
        session.close()
        os.chdir(prev_cwd)
    result["made"] += made
    result["failed"] += failed
    total = time.monotonic() - t_all
    print(f"SDK から直接 npy: 作成 {len(made)} 件 / 失敗 {len(result['failed'])} 件 / "
          f"{total / 60:.1f} 分（1 枚あたり {total / max(len(done), 1):.1f} 秒）")
    return result


# ============================================================================
# SDK での復元（コールバックで復元画像を受け取る）
# ============================================================================

class _Capture:
    """SDK のコールバックで渡された復元画像を、1 回分だけ numpy 配列にコピーしておく。

    コールバックは SDK のスレッドから呼ばれ、画像のメモリはコールバックの中でしか使えないので、ここでコピーする。
    reset(since) より前に届いたもの（前の RAW の遅れて届いた分）は使わない。
    """

    def __init__(self, nbands):
        self.nbands = nbands
        self.lock = threading.Lock()
        self.since = 0.0
        self.result = None  # (bands, None) / (None, 想定と違う画像だった理由)

    def reset(self, since):
        with self.lock:
            self.since, self.result = since, None

    def hook(self, num_images, images):
        arrived = time.monotonic()
        try:
            result = (_copy_bands(num_images, images, self.nbands), None)
        except Exception as e:  # 呼び出し側で失敗にする（コールバックの中で例外を出さない）
            result = (None, str(e))
        with self.lock:
            if self.result is None and arrived >= self.since:
                self.result = result

    def get(self):
        with self.lock:
            return self.result


def _copy_bands(num_images, images, nbands):
    """コールバックの画像（0 枚目 = 撮影画像、1〜nbands 枚目 = band01〜）から、復元画像を (nbands, H, W) uint16 にコピーする。"""
    if num_images != nbands + 1:
        raise ValueError(f"コールバックの画像が {num_images} 枚です（期待: 撮影画像 1 枚 + 復元画像 {nbands} 枚）")
    width, height = images[1].width, images[1].height
    if not (0 < width <= 20000 and 0 < height <= 20000):
        raise ValueError(f"復元画像の大きさが想定と違います: {width} x {height}")
    bands = np.empty((nbands, height, width), dtype=np.uint16)
    for b in range(nbands):
        img = images[1 + b]
        if not img.data or (img.width, img.height) != (width, height) or img.bitdepth != 16:
            raise ValueError(f"band{b + 1:02d} が想定と違います: {img.width} x {img.height}, {img.bitdepth} bit")
        pixels = (ctypes.c_uint16 * (width * height)).from_address(img.data)
        bands[b] = np.frombuffer(pixels, dtype=np.uint16).reshape(height, width)
    return bands


def _open(br, session, capture):
    """SDK を開き、コールバックで復元画像を受け取るようにする。"""
    try:
        session.open()
    except br.SdkError as e:
        raise _SdkUnusable(str(e)) from e
    session.hs.image_hook = capture.hook


def _capture_with_retry(br, session, capture, raw, work_dir, args):
    """1 枚を復元する。失敗したら（restart_on_fail）SDK を初期化し直して、もう一度だけ試す。"""
    try:
        return _capture_one(br, session.hs, capture, raw, work_dir, args)
    except (br.SdkError, OSError) as e:
        if not args.restart_on_fail:
            raise
        print(f"  失敗: {e}")
        print("    SDK を初期化し直して、もう一度試します")
    session.close()
    _open(br, session, capture)
    bands, seconds, message = _capture_one(br, session.hs, capture, raw, work_dir, args)
    return bands, seconds, message + " / SDK を初期化し直して再試行"


def _capture_one(br, hs, capture, raw, work_dir, args):
    """1 枚を復元して、コールバックで受け取った復元画像を返す。戻り値: (bands, 秒, 表示用の補足)"""
    t0 = time.monotonic()
    capture.reset(t0)
    hs.reset_callback_stats()
    # アプリの「ファイル接続」でファイルを指定するのと同じ（撮影停止中に行う）
    hs.set_input_file(args.cam_param, raw)
    # batch_reconstruct.process_one と同じ順: [Reconstruct] → [Record] → [View]
    hs.start_rec()
    hs.start_save(work_dir)
    triggers = []
    result = None
    try:
        br.trigger(hs, args.mode, args.trigger_delay)
        triggers.append(time.monotonic())
        result = _wait_for_callback(hs, capture, args, triggers)
    finally:
        # 保存停止 → 復元停止 → 撮影停止（仕様書 5 章・SDK サンプルと同じ順）
        hs.stop_save()
        hs.stop_rec()
        hs.stop()
        stopped = hs.wait_until_stopped()
        try:
            br.clear_folder(work_dir)  # SDK が書いた撮影画像（camera/compressed_*.png）
        except OSError:
            pass  # Box などが掴んでいて消せなくても、次の 1 枚のときに消す
    if not stopped:
        raise br.SdkError("撮影停止 (HSProc_Stop) のあとも動作中 (RUN) のままです")
    retriggers = max(len(triggers) - 1, 0)
    if result is None:
        raise br.SdkError(f"復元画像が届きません（{time.monotonic() - t0:.0f} 秒 / callback {hs.cb_count} 回 / "
                          f"合図の送り直し {retriggers} 回）")
    bands, error = result
    if error:
        raise br.SdkError(error)
    message = f" / 合図の送り直し {retriggers} 回" if retriggers else ""
    return bands, time.monotonic() - t0, message


def _wait_for_callback(hs, capture, args, triggers):
    """復元画像がコールバックで届くまで待つ。届かなければ None。

    単発撮影の合図を SDK が取りこぼすことがある（batch_reconstruct.py と同じ対策）。
    retrigger 秒たっても届かなければ合図を送り直し、retrigger_max 回送り直しても届かなければあきらめる。
    """
    while time.monotonic() - triggers[0] < args.timeout:
        result = capture.get()
        if result is not None:
            return result
        now = time.monotonic()
        if args.mode == "oneshot" and args.retrigger > 0 and now - triggers[-1] >= args.retrigger:
            if len(triggers) > args.retrigger_max:
                return None
            print(f"    反応がないため撮影の合図を送り直します ({len(triggers)} 回目)")
            hs.one_shot()
            triggers.append(now)
        time.sleep(0.01)
    return None


# ============================================================================
# npy の保存（別スレッド）
# ============================================================================

class _NpyWriter:
    """npy の保存を別スレッドで行う（保存を待つ間に、SDK が次の 1 枚を復元できるように）。

    待ち行列は 1 件だけなので、保存が追いつかないときは put で待つ（メモリに溜め込まない）。
    保存は save_npy（一時ファイルに書いてから名前を変える）なので、途中で止めても書きかけの npy は残らない。
    """

    def __init__(self, log):
        self.log = log
        self.queue = queue.Queue(maxsize=1)
        self.made, self.failed = [], []
        self.thread = threading.Thread(target=self._run, name="npy-writer", daemon=True)
        self.thread.start()

    def put(self, name, array, reconstruct_sec, message):
        """保存を頼む。reconstruct_sec と message（復元の補足）は記録用。"""
        self.queue.put((name, array, reconstruct_sec, message))

    def close(self):
        """保存し終わるまで待つ。戻り値: (作った RAW 名, 失敗した RAW 名と理由)"""
        self.queue.put(None)
        self.thread.join()
        return self.made, self.failed

    def _run(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            name, array, reconstruct_sec, message = item
            save_sec, error = None, None
            for attempt in range(settings.RETRY_COUNT + 1):
                try:
                    t0 = time.monotonic()
                    path = save_npy(array, name)
                    save_sec, error = time.monotonic() - t0, None
                    print(f"  {path.name} 保存完了（{save_sec:.1f} 秒, {array.dtype}）")
                    break
                except Exception as e:
                    error = f"npy を保存できません: {type(e).__name__}: {e}"
                    if attempt < settings.RETRY_COUNT:
                        print(f"  {name}.npy の保存に失敗、やり直します: {type(e).__name__}: {e}")
                        time.sleep(settings.RETRY_WAIT_SEC)
                    else:
                        print(f"  {name}.npy を保存できません: {type(e).__name__}: {e}")
            if error is None:
                self.made.append(name)
                notes = [m for m in (message, f"保存のやり直し {attempt} 回" if attempt else "") if m]
                self.log.record(name, "sdk", "ok", reconstruct_sec=reconstruct_sec, save_sec=save_sec,
                                message=" / ".join(notes))
            else:
                self.failed.append(f"{name}  {error}")
                self.log.record(name, "sdk", "error", reconstruct_sec=reconstruct_sec, message=error)
