import settings


def check_settings():
    """設定の指定ミスがあれば、何も作らないうちに止める。確認した設定を表示する。"""
    if not settings.RAW_ROOT.is_dir():
        raise FileNotFoundError(f"RAW_ROOT が見つかりません: {settings.RAW_ROOT}")
    if not settings.RECONSTRUCT_ROOT.is_dir() and not settings.RECONSTRUCT_MISSING:
        raise FileNotFoundError(f"RECONSTRUCT_ROOT が見つかりません: {settings.RECONSTRUCT_ROOT}")
    if settings.WHITE_CORRECTION and not settings.CALIBRATION_JSON.is_file():
        raise FileNotFoundError(f"calibration.json が見つかりません: {settings.CALIBRATION_JSON}")
    if settings.BLACK_CORRECTION and not settings.DARK_DIR.is_dir():
        raise FileNotFoundError(
            f"ダークのフォルダが見つかりません: {settings.DARK_DIR}"
            "（黒補正しないなら BLACK_CORRECTION = False）"
        )
    if settings.WHITE_CORRECTION and not 0 < settings.WHITE_BOARD_REFLECTANCE <= 1:
        raise ValueError(f"白板の反射率は 0 より大きく 1 以下です: {settings.WHITE_BOARD_REFLECTANCE}")

    dates = ", ".join(map(str, settings.DATES)) or "すべて"
    print(f"RAW        : {settings.RAW_ROOT}  （日付: {dates}）")
    print(f"再構成結果 : {settings.RECONSTRUCT_ROOT}")
    print(f"対象の名前 : {settings.NAMES_FILE}")
    print(f"出力       : {settings.OUTPUT_DIR}")
    print(f"黒補正     : {settings.DARK_DIR if settings.BLACK_CORRECTION else 'なし'}")
    if settings.WHITE_CORRECTION:
        print(f"白板補正   : {settings.CALIBRATION_JSON}（白板の反射率 {settings.WHITE_BOARD_REFLECTANCE}）")
    else:
        print("白板補正   : なし")
    print("※ 黒補正・白板補正・白板の反射率が RGB 化のときと同じか確認してください。")
