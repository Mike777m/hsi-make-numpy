# hsi-make-numpy

ハイパースペクトルカメラ AG-HSV10M（420〜700 nm・29 バンド）の再構成結果（band01〜29.png）を、
選んだ RAW 名の分だけ 1 枚ずつ `.npy`（29 バンドの配列）にするツール。

この配布版の既定は **白板補正なし・黒補正なし**。npy の値は再構成の PNG の値そのもので、型は `uint16`。
Iromiel Hyper SDK と NVIDIA の GPU がある PC なら、再構成結果が無い RAW を SDK で復元して、PNG を作らずにそのまま npy にもできる
（[再構成](#再構成reconstruct_missing)。SDK はこのリポジトリに入っていない）。

---

## 1. 準備

Python 3.10 以上が必要（再構成もするなら 3.11 以上・64bit）。

```bash
git clone <このリポジトリの URL>
cd hsi-make-numpy
pip install -r requirements.txt     # numpy と opencv-python
```

## 2. データを置く

リポジトリのフォルダの中に、次のように置く（場所は `make_numpy/settings.py` で変えられる）。

```
hsi-make-numpy/
├─ raw_data/<日付>/<時間>/<RAW名>.RAW                                  ← RAW
├─ input_folder/<日付>/<時間>/<RAW名>/reconstruct/<RAW名>/band01〜29.png ← 再構成結果
├─ names.txt                                                          ← npy にする RAW 名
├─ hsi2rgb/        （コード）
├─ make_numpy/     （コード・設定）
└─ automation/     （SDK で再構成するコードと設定。再構成するときだけ使う）
```

* **RAW ファイルも必要**。中身は読まないが、RAW 名から「どの日付・時間フォルダにあるか」を探すのに使う。
  RAW が無い名前は「RAW が見つからない名前」として飛ばされる。
* 再構成結果は、Iromiel Hyper の出力の形 `input_folder/<日付>/<時間>/reconstruct/<再構成日時>/band01〜29.png`
  でもよい（そのときは RAW と名前順で対応させる）。
* `names.txt` は 1 行に 1 つ RAW 名を書く（`names.example.txt` を参照）。
  `00800725.RAW` のように拡張子付きでもよい。`meta_data.json` や `["00800725", ...]` の JSON も使える
  （そのときは `settings.py` の `NAMES_FILE` を書き換える）。

## 3. 実行

```bash
python make_numpy/main.py
```

`output_npy/<RAW名>.npy` ができる。最後に、作れなかった名前が理由ごとに表示される。
もう一度実行すると、npy が既にあるものは飛ばす。

実行するたびに `logs/` の 2 つの CSV に記録を追記し、最後に画面にもまとめを出す（途中で止めたときも）。

| ファイル | 中身 |
|---|---|
| `logs/npy_log.csv` | 1 枚ごと：RAW 名・PNG から / SDK から・成否・読み込み / 復元 / 保存の秒数・前の 1 枚からの間隔（`interval_sec` = 実際に 1 枚にかかった時間） |
| `logs/run_summary.csv` | 日付ごとと実行全体：何枚を何時間で作ったか・1 枚あたりの秒数・1 時間あたりの枚数・失敗と既存スキップの数 |

列の意味は `make_numpy/説明.md` の「実行の記録」。

Box Drive などのクラウド同期フォルダの中で動かすと、フォルダが一時的に読めないことがある（`WinError 1237`）。
そのときは数回やり直し、それでも読めない名前はその回は飛ばして、最後に「一時的なエラーで読めず、飛ばした名前」として表示する。
同期が落ち着いてからもう一度実行すれば、続きから処理する（詳しくは `make_numpy/説明.md`）。

## 4. できる npy

| 項目 | 値 |
|---|---|
| 形 | `(29, 高さ, 幅)`。FHD なら `(29, 1440, 1920)` |
| 型 | `uint16`（1 枚 約 160MB）。`NPY_UINT16 = False` か、黒補正・白板補正をするときは `float32`（約 320MB） |
| バンド | `npy[0]` = 420 nm、`npy[1]` = 430 nm、…、`npy[28]` = 700 nm（10 nm 刻み） |
| 値 | 白板補正なしなら、再構成の PNG の値そのもの（整数値。0〜数千程度） |

再構成の値はもともと 16bit の整数なので、`uint16` でも劣化はない（`float32` で保存した npy と値は同じ）。
**計算する前に `float32` に変換する。** `uint16` のまま引き算すると、負にならずに値が回り込む（100 − 200 が 65436 になる）。

```python
import numpy as np

cube = np.load("output_npy/00800725.npy").astype(np.float32)   # (29, H, W)
wavelengths = [420 + 10 * i for i in range(29)]
spectrum = cube[:, 700, 900]                 # 画素 (x=900, y=700) のスペクトル
```

---

## 5. 設定（`make_numpy/settings.py`）

| 設定 | 意味 |
|---|---|
| `RAW_ROOT` | RAW のフォルダ（`<RAW_ROOT>/<日付>/<時間>/<RAW名>.RAW`） |
| `RECONSTRUCT_ROOT` | 再構成結果のフォルダ（`<RECONSTRUCT_ROOT>/<日付>/<時間>/`） |
| `DATES` | 探す日付。例 `["10082026", "10092026"]`。空 `[]` なら全部 |
| `NAMES_FILE` | npy にする RAW 名のファイル |
| `OUTPUT_DIR` | npy の保存先 |
| `LOG_DIR` | 実行の記録の保存先（既定 `logs/`） |
| `NPY_UINT16` | 補正なしなら npy を `uint16` で保存する（既定 `True`）。`False` なら `float32` |
| `BLACK_CORRECTION` / `WHITE_CORRECTION` | 補正（下の表） |
| `OVERWRITE` | 既にある npy も作り直すか |

別のドライブなどを絶対パスで書くときは、`Path("D:/data/raw_data")` のように **`Path(...)` で囲む**
（文字列のままだと `'str' object has no attribute 'mkdir'` などのエラーになる）。

### 補正

| `BLACK_CORRECTION` | `WHITE_CORRECTION` | npy の中身 | 必要なもの |
|---|---|---|---|
| False | False | 再構成の値そのもの（**既定**） | なし |
| True | False | 再構成の値 − ダーク | `DARK_DIR`（ダークの band01〜29.png） |
| False | True | 白板で割った反射率 | `CALIBRATION_JSON`（白板ROI `{"RAW名": [x0, y0, x1, y1]}`） |
| True | True | ダークを引いてから白板で割った反射率 | 両方 |

白板補正なしで作った npy も、白板ROI が分かればあとから反射率にできる。

```
反射率 = 0.9 × npy[b] ÷ (白板ROI内の npy[b] の平均)      ※ 0.9 は白板の反射率
```

### 再構成（`RECONSTRUCT_MISSING`）

既定は `False`（再構成結果が無い名前は、最後に「再構成結果が無い名前」として表示するだけ）。
`True` にすると、再構成結果が無い RAW を Iromiel Hyper SDK で復元して npy にする。やり方は `DIRECT_NPY` で選ぶ。

| `DIRECT_NPY` | やり方 | 1 枚の時間（RTX 4070 SUPER で実測） |
|---|---|---|
| `True`（既定） | SDK がコールバックで渡す復元画像を、PNG を作らずにそのまま npy にする（`make_numpy/reconstruct_to_npy.py`）。npy の保存は別スレッドで並行して行う | 約 4 秒 |
| `False` | `automation/batch_reconstruct.py` で `RECONSTRUCT_ROOT/<日付>/<時間>/<RAW名>/` に PNG を再構成してから npy にする | 約 10 秒 ＋ npy の保存 |

どちらで作っても npy の中身は同じ（RAW 4 枚で、画素単位で一致することを確認）。
再構成結果の PNG がもとからある RAW は、どちらの設定でも PNG から npy にする。

**SDK の本体（DLL）とキャリブレーションデータはこのリポジトリに入っていない**（SDK の使用許諾により配布できない）。
`batch_reconstruct.py` は、実行時に SDK の DLL を読み込んで呼び出すだけのコード。

| 必要なもの | 条件 |
|---|---|
| OS | Windows 11（64bit） |
| GPU | NVIDIA（Compute Capability 6.1 以上）、ドライバ 572.61 以降 |
| Python | 3.11 以上・64bit（`config.toml` の読み込みに `tomllib` を使う） |
| Iromiel Hyper SDK | `Iromirl_Hyper_SDK\Library\release`（`HSProc.dll` など 7 つの DLL） |
| キャリブレーションデータ | SDK 付属の `HSmaskdata` の `_hsmask.csv` と透過率 PNG（撮影の解像度・絞りに合ったもの） |

`automation/config.toml` で、次の 2 つを自分の PC に合わせる。

* `dll_dir`：SDK の `Library\release` のフォルダ
* `mask`：`_hsmask.csv`。既定は、`HSmaskdata` をリポジトリ直下にコピーしたときの FHD・絞り F1.7 用の位置
  （`HSmaskdata/` と `*.dll` は `.gitignore` で git に入らないようにしてある）

ほかの設定はそのままでよい。`DIRECT_NPY = True` なら、`config.toml` の `png` / `envi` / `log` は使わない（何も保存させない）。
`DIRECT_NPY = False` のときは `envi = false`（npy 化に使わない ENVI は作らない。再構成結果は 1 枚 約 50MB）。
日本語のパスで SDK が失敗するときは `path_encoding = 'mbcs'` を試す。

* 日付ごとに、npy の空き容量（`DIRECT_NPY = False` なら再構成結果の分も）を確かめ、足りなければその日付を始めずに止まる。
* `DIRECT_NPY = True` では、SDK が書く撮影画像の PNG だけを `RECONSTRUCT_ROOT/_direct_npy_tmp/` に一時的に置いて 1 枚ごとに消す。
  1 枚ごとの結果と秒数は `logs/npy_log.csv` に残る。
* `DELETE_RECONSTRUCT_AFTER_NPY = True` にすると、`DIRECT_NPY = False` で再構成した結果を npy にしたあと消す。
* 再構成だけを単体で実行することもできる（`config.toml` の `input` / `output` を書いて `python automation/batch_reconstruct.py`）。

---

詳しい処理の流れは `make_numpy/説明.md` を参照。
