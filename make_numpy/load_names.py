import json
from pathlib import Path


def load_names(path):
    """名前ファイルから、npy にする RAW 名を書かれた順・重複なしで返す。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"名前ファイルが見つかりません: {path}")

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            names = [name for name, status in data.items() if status == "keep"]
        elif isinstance(data, list):
            names = []
            for item in data:
                if isinstance(item, dict):  # meta_data.json の形 [{"image_id": "00800725", ...}, ...]
                    if "image_id" not in item:
                        raise ValueError(f"image_id の無い要素があります: {path}")
                    names.append(str(item["image_id"]))
                else:
                    names.append(str(item))
        else:
            raise ValueError(f"名前ファイルは辞書かリストのJSONにしてください: {path}")
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
        names = [line.strip() for line in lines if line.strip()]

    # '00480902.RAW' や 'rgb/00480902.png' と書かれていても '00480902' にする
    stems = []
    for name in names:
        p = Path(str(name).strip())
        stems.append(p.stem if p.suffix.lower() in {".raw", ".png", ".npy"} else p.name)
    return list(dict.fromkeys(stems))
