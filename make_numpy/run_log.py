import csv
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import settings
from npy_dtype import npy_dtype

STEP_HEADER = ["run_start", "finished", "date", "raw", "method", "result",
               "read_sec", "reconstruct_sec", "save_sec", "interval_sec", "message"]
SUMMARY_HEADER = ["run_start", "scope", "start", "end", "elapsed_h", "made", "made_png", "made_sdk", "failed",
                  "existing", "unreadable", "sec_per_npy", "npy_per_hour",
                  "avg_read_sec", "avg_reconstruct_sec", "avg_save_sec", "npy_dtype", "note"]


class RunLog:
    """実行の記録。1 枚ごとの記録と、日付ごと・実行全体のまとめを LOG_DIR の CSV に追記する。

        npy_log.csv      1 枚ごと（いつ・どの RAW・PNG から / SDK から・成否・読み込み / 復元 / 保存の秒数・前の 1 枚からの間隔）
        run_summary.csv  日付ごとと実行全体（何枚を何時間で作ったか・1 枚あたりの秒数・1 時間あたりの枚数）

    記録を書けなくても（Box Drive がファイルを掴んでいるなど）npy の作成は止めない。
    書けなかった行は覚えておき、次に書くときにまとめて書く。
    record は npy を保存する別スレッドからも呼ばれるので、ロックをかける。
    """

    def __init__(self):
        self.dir = Path(settings.LOG_DIR)
        self.step_path = self.dir / "npy_log.csv"
        self.summary_path = self.dir / "run_summary.csv"
        self.lock = threading.Lock()
        self.total = _Tally()
        self.run_id = self.total.start.isoformat(timespec="seconds")
        self.day = None
        self.date = ""
        self.last_done = time.monotonic()  # 直前の npy ができた時刻（最初は実行の開始）
        self.pending = {self.step_path: [], self.summary_path: []}
        self.warned = False

    # --- 日付ごと ------------------------------------------------------------
    def begin_date(self, date):
        with self.lock:
            self.date = date
            self.day = _Tally()

    def count_skipped(self, existing, unreadable):
        """npy が既にあって飛ばした数、一時的なエラーで読めず飛ばした数を足す。"""
        with self.lock:
            for tally in (self.total, self.day):
                if tally is not None:
                    tally.existing += existing
                    tally.unreadable += unreadable

    def end_date(self, note=""):
        """日付のまとめを表示して、run_summary.csv に書く。"""
        with self.lock:
            self._end_date(note)

    def _end_date(self, note):
        day, self.day = self.day, None
        if day is None:
            return
        row = self._summary_row(self.date, day, note)
        per = f"、1 枚あたり {_sec(day.per_npy())}" if day.made else ""
        print(f"{self.date}: 所要 {_hms(day.elapsed())}（作成 {day.made} 枚{per}）")
        self._write(self.summary_path, SUMMARY_HEADER, [row])

    # --- 1 枚ごと ------------------------------------------------------------
    def record(self, raw, method, result, read_sec=None, reconstruct_sec=None, save_sec=None, message=""):
        """1 枚の結果を記録する。

        method: "png"（再構成結果の PNG から）/ "sdk"（SDK から直接）
        result: "ok" / "error" / "skip" / "timeout"
        """
        with self.lock:
            interval = None
            if result == "ok":
                now = time.monotonic()
                interval, self.last_done = now - self.last_done, now
            for tally in (self.total, self.day):
                if tally is not None:
                    tally.add(method, result, read_sec, reconstruct_sec, save_sec)
            row = [self.run_id, _now(), self.date, raw, method, result,
                   _num(read_sec), _num(reconstruct_sec), _num(save_sec), _num(interval), message]
            self._write(self.step_path, STEP_HEADER, [row])

    # --- 実行全体 ------------------------------------------------------------
    def finish(self, note=""):
        """実行全体のまとめを表示して、run_summary.csv に書く。途中で止まったときも呼ぶ。"""
        with self.lock:
            self._end_date(note)  # 途中で止まった日付
            t = self.total
            row = self._summary_row("全体", t, note)
            print("--- 実行の記録 ---")
            print(f"開始 {t.start:%Y-%m-%d %H:%M:%S}  終了 {datetime.now():%Y-%m-%d %H:%M:%S}  経過 {_hms(t.elapsed())}"
                  + (f"  （{note}）" if note else ""))
            print(f"作成 {t.made} 枚（PNG から {t.made_by['png']} / SDK から直接 {t.made_by['sdk']}）  "
                  f"失敗 {t.failed}  既存スキップ {t.existing}" + (f"  読めず飛ばした {t.unreadable}" if t.unreadable else ""))
            if t.made:
                print(f"1 枚あたり {_sec(t.per_npy())}（経過時間 ÷ 作成枚数）  1 時間あたり {t.per_hour():.1f} 枚")
                parts = [f"{label} {_sec(t.avg(key))}" for key, label in
                         (("read", "PNG の読み込み"), ("reconstruct", "SDK の復元"), ("save", "npy の保存"))
                         if t.avg(key) is not None]
                print("平均の内訳: " + " / ".join(parts))
            self._write(self.summary_path, SUMMARY_HEADER, [row])
            print(f"記録: {self.step_path}（1 枚ごと）")
            print(f"      {self.summary_path}（日付ごと・全体）")
            for path, rows in self.pending.items():
                if rows:
                    print(f"  警告: {path.name} に {len(rows)} 行を書けませんでした（Box Drive などがファイルを掴んでいる可能性）")

    # --- 内部 ------------------------------------------------------------------
    def _summary_row(self, scope, t, note):
        return [self.run_id, scope, t.start.isoformat(timespec="seconds"), _now(), f"{t.elapsed() / 3600:.3f}",
                t.made, t.made_by["png"], t.made_by["sdk"], t.failed, t.existing, t.unreadable,
                _num(t.per_npy()), _num(t.per_hour()),
                _num(t.avg("read")), _num(t.avg("reconstruct")), _num(t.avg("save")),
                np.dtype(npy_dtype()).name, note]

    def _write(self, path, header, rows):
        self.pending[path] += rows
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(header)
                writer.writerows(self.pending[path])
            self.pending[path] = []
        except OSError as e:
            if not self.warned:
                print(f"  警告: 実行の記録を書けません（npy の作成は続けます。次に書くときにまとめて書きます）: {e}")
                self.warned = True


class _Tally:
    """ある期間（日付・実行全体）の枚数と時間の集計。"""

    def __init__(self):
        self.start = datetime.now()
        self.t0 = time.monotonic()
        self.made_by = {"png": 0, "sdk": 0}
        self.failed = self.existing = self.unreadable = 0
        self.sums = {"read": [0.0, 0], "reconstruct": [0.0, 0], "save": [0.0, 0]}

    @property
    def made(self):
        return sum(self.made_by.values())

    def add(self, method, result, read_sec, reconstruct_sec, save_sec):
        if result != "ok":
            self.failed += 1
            return
        self.made_by[method] = self.made_by.get(method, 0) + 1
        for key, value in (("read", read_sec), ("reconstruct", reconstruct_sec), ("save", save_sec)):
            if value is not None:
                self.sums[key][0] += value
                self.sums[key][1] += 1

    def elapsed(self):
        return time.monotonic() - self.t0

    def per_npy(self):
        return self.elapsed() / self.made if self.made else None

    def per_hour(self):
        hours = self.elapsed() / 3600
        return self.made / hours if self.made and hours > 0 else None

    def avg(self, key):
        total, n = self.sums[key]
        return total / n if n else None


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _num(value):
    return "" if value is None else f"{value:.1f}"


def _sec(value):
    return "-" if value is None else f"{value:.1f} 秒"


def _hms(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
